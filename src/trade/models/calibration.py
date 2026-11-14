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
from scipy.optimize import minimize_scalar  # type: ignore[import-untyped]

from trade.common.exits import ExitConfig
from trade.features import enabled_features, feature_hash
from trade.features.registry import FeaturesConfig

THRESHOLD_MIN = 0.30
THRESHOLD_MAX = 0.80
THRESHOLD_STEP = 0.02
MIN_TRADES_PER_DAY = 3.0
PF_RETENTION = 0.9


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
    """阈值候选 θ ∈ {0.30, 0.32, …, 0.80}（05 §7.2）。"""
    count = int(round((THRESHOLD_MAX - THRESHOLD_MIN) / THRESHOLD_STEP)) + 1
    return [round(THRESHOLD_MIN + index * THRESHOLD_STEP, 4) for index in range(count)]


def select_threshold(
    metrics_by_theta: dict[float, dict[str, float]],
    *,
    n_days: float,
    min_trades_per_day: float = MIN_TRADES_PER_DAY,
    pf_retention: float = PF_RETENTION,
) -> ThresholdChoice:
    """按 05 §7.2 在验证集指标上选择阈值。

    约束：验证集日均交易笔数 ≥ `min_trades_per_day`；
    在满足约束的 θ 中取 PF 最高者 θ*，最终 θ = 使 PF ≥ pf_retention × PF(θ*) 的
    最小 θ（偏向更多交易，降低对单点的过拟合）。

    Args:
        metrics_by_theta: θ → {"net_return", "profit_factor", "n_trades"}。
        n_days: 验证段自然日数。
        min_trades_per_day: 日均笔数下限（05 §7.2 为 3）。
        pf_retention: 最终阈值保留的 PF 比例（05 §7.2 为 0.9）。

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
    threshold = best_pf * pf_retention
    chosen_theta, chosen = next(
        (theta, metrics)
        for theta, metrics in eligible
        if float(metrics.get("profit_factor", 0.0)) >= threshold
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
        pf_by_theta={
            float(theta): float(metrics.get("profit_factor", 0.0))
            for theta, metrics in sorted(metrics_by_theta.items())
        },
        n_trades_by_theta={
            float(theta): int(metrics.get("n_trades", 0.0))
            for theta, metrics in sorted(metrics_by_theta.items())
        },
    )


def calibration_metrics(logits: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """返回温度缩放前后的 NLL/ECE 与温度（05 §7.1）。"""
    logits = np.asarray(logits, dtype="float64")
    y = np.asarray(y, dtype="float64")
    temperature = fit_temperature(logits, y)
    p_before = sigmoid(logits)
    p_after = sigmoid(logits / temperature)

    def nll(p: np.ndarray) -> float:
        return float(
            -np.mean(
                y * np.log(np.clip(p, 1e-12, 1.0)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1.0))
            )
        )

    return {
        "temperature": temperature,
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
