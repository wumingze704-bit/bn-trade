"""概率校准（温度缩放）、阈值选择与 model_meta.json。

规格：docs/05-模型与训练.md §7、§9
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize, minimize_scalar  # type: ignore[import-untyped]

from trade.common.exits import ExitConfig
from trade.features import enabled_features, feature_hash
from trade.features.registry import FeaturesConfig

THRESHOLD_MIN = 0.10
THRESHOLD_MAX = 0.80
THRESHOLD_STEP = 0.02
MIN_TRADES_PER_DAY = 5.0
# 验证集上没有任何 θ 达到正期望时的阈值：p 永远小于 1，等价于该折不交易（05 §7.2）
DISABLED_THETA = 1.0


@dataclass(frozen=True)
class ThresholdChoice:
    """阈值选择结果（05 §7.2）。"""

    theta: float
    profit_factor: float
    net_return: float
    n_trades: int
    daily_trades: float
    best_theta: float
    best_profit_factor: float
    n_candidates: int
    # 验证集 θ 网格上的 PF 与笔数（05 §9 model_meta.json 的 threshold_selection 记录）
    pf_by_theta: dict[float, float] = field(default_factory=dict)
    n_trades_by_theta: dict[float, int] = field(default_factory=dict)
    # 阈值被禁用（验证集无正期望）时的原因；正常选择为 None（05 §7.2）
    disabled_reason: str | None = None


def sigmoid(logits: np.ndarray) -> np.ndarray:
    """数值稳定的 sigmoid。"""
    values = np.asarray(logits, dtype="float64")
    return 1.0 / (1.0 + np.exp(-values))


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """在验证集上学习单一温度 T，最小化 NLL（05 §7.1）。

    Args:
        logits: 模型原始 logit。
        y: 0/1 标签。

    Returns:
        最优温度 T（> 0）；样本为空或标签单一分布时返回 1.0。
    """
    logits = np.asarray(logits, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if len(logits) == 0 or y.min() == y.max():
        return 1.0

    def objective(temperature: float) -> float:
        p = sigmoid(logits / temperature)
        terms = y * np.log(np.clip(p, 1e-12, 1.0)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1.0))
        return float(-np.mean(terms))

    result = minimize_scalar(objective, bounds=(0.05, 10.0), method="bounded")
    return float(result.x) if result.success else 1.0


def fit_platt(logits: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """在验证集上学习 Platt 校准 `p = σ(a·z + b)`（05 §7.1）。

    训练时 BCE 的 `pos_weight = √(neg/pos)` 会把 logit 整体平移 ln(pos_weight)，
    纯温度缩放（只缩放不平移）无法消除该偏移，因此校准改用带偏移的 Platt 形式。

    Args:
        logits: 模型原始 logit。
        y: 0/1 标签。

    Returns:
        (a, b)；样本为空或标签单一分布时返回 (1.0, 0.0)。
    """
    logits = np.asarray(logits, dtype="float64")
    y = np.asarray(y, dtype="float64")
    if len(logits) == 0 or y.min() == y.max():
        return 1.0, 0.0

    def objective(params: np.ndarray) -> float:
        a, b = float(params[0]), float(params[1])
        p = sigmoid(a * logits + b)
        terms = y * np.log(np.clip(p, 1e-12, 1.0)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1.0))
        return float(-np.mean(terms))

    result = minimize(
        objective,
        x0=np.array([1.0, 0.0]),
        method="L-BFGS-B",
        bounds=[(0.02, 20.0), (-10.0, 10.0)],
    )
    if not result.success:
        return 1.0, 0.0
    return float(result.x[0]), float(result.x[1])


def platt_sigmoid(logits: np.ndarray, a: float, b: float) -> np.ndarray:
    """按 Platt 参数把 logit 转成校准后概率：`σ(a·z + b)`（05 §7.1）。"""
    return sigmoid(a * np.asarray(logits, dtype="float64") + b)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, *, bins: int = 10) -> float:
    """ECE（10 分箱，05 §7.1）。"""
    y = np.asarray(y, dtype="float64")
    p = np.asarray(p, dtype="float64")
    if len(y) == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        mask = (p >= lower) & (p < upper if index < bins - 1 else p <= upper)
        if not mask.any():
            continue
        total += float(mask.mean()) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return total


def threshold_grid() -> list[float]:
    """阈值候选 θ ∈ {0.10, 0.12, …, 0.80}（05 §7.2）。"""
    count = int(round((THRESHOLD_MAX - THRESHOLD_MIN) / THRESHOLD_STEP)) + 1
    return [round(THRESHOLD_MIN + index * THRESHOLD_STEP, 4) for index in range(count)]


def select_threshold(
    metrics_by_theta: dict[float, dict[str, float]],
    *,
    n_days: float,
    min_trades_per_day: float = MIN_TRADES_PER_DAY,
    min_profit_factor: float = 0.0,
) -> ThresholdChoice:
    """按 05 §7.2 在验证集指标上选择阈值。

    约束与目标：
    1. 验证集日均交易笔数 ≥ `min_trades_per_day`；
    2. 在其中只保留 PF ≥ `min_profit_factor` 的 θ；
    3. 取净收益最大的 θ（同值取笔数多者）。
    若没有任何 θ 同时满足 1、2，判定该折无正期望：返回 `theta = DISABLED_THETA`
    （p 恒小于 1，等价于不交易）并写明 `disabled_reason`。

    Args:
        metrics_by_theta: θ → {"net_return", "profit_factor", "n_trades"}。
        n_days: 验证段自然日数。
        min_trades_per_day: 日均笔数下限（05 §7.2 为 5）。
        min_profit_factor: 验证集 PF 下限；低于它则该折不交易（05 §7.2）。

    Returns:
        ThresholdChoice。

    Raises:
        ValueError: 没有任何 θ 满足交易笔数约束。
    """
    if n_days <= 0:
        raise ValueError("验证段天数必须为正")
    eligible: list[tuple[float, dict[str, float]]] = []
    for theta in sorted(metrics_by_theta):
        metrics = metrics_by_theta[theta]
        daily = float(metrics.get("n_trades", 0.0)) / n_days
        if daily >= min_trades_per_day:
            eligible.append((float(theta), metrics))
    if not eligible:
        raise ValueError(f"验证集上没有任何 θ 达到日均 {min_trades_per_day} 笔（05 §7.2 约束）")
    best_theta, best = max(eligible, key=lambda item: float(item[1].get("profit_factor", 0.0)))
    best_pf = float(best.get("profit_factor", 0.0))
    pf_by_theta = {
        float(theta): float(metrics.get("profit_factor", 0.0))
        for theta, metrics in sorted(metrics_by_theta.items())
    }
    n_trades_by_theta = {
        float(theta): int(metrics.get("n_trades", 0.0))
        for theta, metrics in sorted(metrics_by_theta.items())
    }
    positive = [
        (theta, metrics)
        for theta, metrics in eligible
        if float(metrics.get("profit_factor", 0.0)) >= min_profit_factor
    ]
    if not positive:
        return ThresholdChoice(
            theta=DISABLED_THETA,
            profit_factor=0.0,
            net_return=0.0,
            n_trades=0,
            daily_trades=0.0,
            best_theta=float(best_theta),
            best_profit_factor=best_pf,
            n_candidates=len(eligible),
            pf_by_theta=pf_by_theta,
            n_trades_by_theta=n_trades_by_theta,
            disabled_reason=f"validation_pf_below_min:{best_pf:.4f}",
        )
    chosen_theta, chosen = max(
        positive,
        key=lambda item: (
            float(item[1].get("net_return", 0.0)),
            int(item[1].get("n_trades", 0.0)),
        ),
    )
    n_trades = int(chosen.get("n_trades", 0.0))
    return ThresholdChoice(
        theta=float(chosen_theta),
        profit_factor=float(chosen.get("profit_factor", 0.0)),
        net_return=float(chosen.get("net_return", 0.0)),
        n_trades=n_trades,
        daily_trades=n_trades / n_days,
        best_theta=float(best_theta),
        best_profit_factor=best_pf,
        n_candidates=len(eligible),
        pf_by_theta=pf_by_theta,
        n_trades_by_theta=n_trades_by_theta,
    )


def calibration_metrics(logits: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """返回 Platt 校准前后的 NLL/ECE 与校准参数（05 §7.1）。

    返回键含 `platt_a`/`platt_b`（校准后的 `p = σ(a·z + b)`）与兼容键
    `temperature`（= a，旧代码按温度缩放读取时仍可用）。
    """
    logits = np.asarray(logits, dtype="float64")
    y = np.asarray(y, dtype="float64")
    temperature = fit_temperature(logits, y)
    platt_a, platt_b = fit_platt(logits, y)
    p_before = sigmoid(logits)
    p_after = platt_sigmoid(logits, platt_a, platt_b)

    def nll(p: np.ndarray) -> float:
        return float(
            -np.mean(
                y * np.log(np.clip(p, 1e-12, 1.0)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1.0))
            )
        )

    return {
        "temperature": temperature,
        "platt_a": platt_a,
        "platt_b": platt_b,
        "nll_before": nll(p_before),
        "nll_after": nll(p_after),
        "ece_before": expected_calibration_error(y, p_before),
        "ece_after": expected_calibration_error(y, p_after),
    }


def make_model_id(arch: str, feature_hash_value: str, *, created_at: datetime | None = None) -> str:
    """生成 `model_id = {yyyyMMdd}_{arch}_{短哈希}`（05 §9）。"""
    moment = created_at or datetime.now(UTC)
    return f"{moment:%Y%m%d}_{arch}_{feature_hash_value[:6]}"


def write_model_meta(
    model_dir: Path,
    *,
    model_id: str,
    arch: str,
    arch_params: dict[str, Any],
    window_L: int,  # noqa: N803 - 与 05 §9 字段名一致
    features_cfg: FeaturesConfig,
    exit_cfg: ExitConfig,
    market: str,
    universe_rule_hash: str,
    train_symbols: list[str],
    train_range: tuple[str, str],
    val_range: tuple[str, str],
    ensemble_seeds: list[int],
    temperature: float,
    threshold: float,
    dataset_manifest_sha256: str,
    platt_b: float = 0.0,
    threshold_selection: dict[str, Any] | None = None,
    git_commit: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """写出 05 §9 规定的 `model_meta.json`，字段齐全。

    Args:
        model_dir: `artifacts/models/{model_id}` 目录。
        threshold_selection: 阈值选择的验证集证据（θ 候选、PF、笔数、天数）。
        git_commit: 未给出时读取当前仓库 HEAD（无 git 时为 None）。
        created_at: 创建时间，默认当前 UTC。

    Returns:
        写出的元数据字典。
    """
    features = enabled_features(features_cfg)
    moment = created_at or datetime.now(UTC)
    meta: dict[str, Any] = {
        "model_id": model_id,
        "arch": arch,
        "arch_params": arch_params,
        "window_L": window_L,
        "features": [feature.name for feature in features],
        "feature_hash": feature_hash(features),
        "ewm_halflife_bars": features_cfg.ewm.halflife_bars,
        "prefilter": features_cfg.prefilter.model_dump(),
        "label": {
            "mode": features_cfg.label.mode,
            "y_min_r": features_cfg.label.y_min_r,
            "k_sl": features_cfg.label.k_sl,
            "sl_min": features_cfg.label.sl_min,
            "sl_max": features_cfg.label.sl_max,
            "entry_latency_ms": features_cfg.label.entry_latency_ms,
        },
        "exit_policy": exit_cfg.model_dump(),
        "ensemble_seeds": list(ensemble_seeds),
        "temperature": float(temperature),
        # Platt 校准参数：p = σ(a·logit + b)，a = temperature（05 §7.1）
        "calibration": {"a": float(temperature), "b": float(platt_b)},
        "threshold": float(threshold),
        "market": market,
        "universe_rule_hash": universe_rule_hash,
        "train_symbols": list(train_symbols),
        "train_range": list(train_range),
        "val_range": list(val_range),
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "torch_version": _torch_version(),
        "created_at": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    if threshold_selection is not None:
        meta["threshold_selection"] = threshold_selection
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8"
    )
    return meta


def sha256_file(path: Path) -> str:
    """计算文件 SHA256（dataset_manifest_sha256 用）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _torch_version() -> str:
    import torch  # 局部导入，避免仅读元数据时加载 torch

    return str(torch.__version__)


def _git_commit() -> str | None:
    """读取当前仓库短提交哈希；不是 git 仓库时返回 None。"""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[3],
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
