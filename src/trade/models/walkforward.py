"""5 折 Walk-Forward 与 F_final 训练（每折同一冻结超参）。

规格：docs/03-数据与选币.md §6；docs/05-模型与训练.md §6、§7
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.common.exits import ExitConfig
from trade.features import enabled_features, feature_hash
from trade.features.registry import FeaturesConfig
from trade.models.calibration import (
    calibration_metrics,
    make_model_id,
    select_threshold,
    sha256_file,
    write_model_meta,
)
from trade.models.config import ModelConfig, WalkForwardConfig
from trade.models.dataset import WindowDataset, month_end_ms, month_start_ms
from trade.models.train import train_ensemble

ThresholdEvaluator = Callable[[pd.DataFrame, float], dict[float, dict[str, float]]]


@dataclass(frozen=True)
class FoldSpec:
    """单折的月份划分（03 §6）。"""

    fold_id: str
    train_months: list[str]
    val_month: str
    test_months: list[str]


@dataclass
class FoldResult:
    """单折训练与信号产出结果。"""

    fold: FoldSpec
    model_dir: Path
    threshold: float
    temperature: float
    val_pr_auc: float
    n_test_signals: int
    signals_path: Path


def build_folds(
    months: Sequence[str],
    *,
    first_test_month: str,
    n_folds: int = 5,
    train_start: str = "2024-10",
) -> list[FoldSpec]:
    """按 03 §6 生成扩展窗口折。

    F1：训练 M1~M11、验证 M12、测试 M13~M14；F2 起验证后移 2 个月、
    测试为随后 2 个月；共 5 折，测试段 M13~M22。

    Args:
        months: 全部月份（`YYYY-MM`，升序）。
        first_test_month: 第一折测试段起始月（M13）。
        n_folds: 折数（默认 5）。
        train_start: 训练起始月（M1）。

    Returns:
        FoldSpec 列表。

    Raises:
        ValueError: 月份不足或不是升序。
    """
    if list(months) != sorted(months):
        raise ValueError("months 必须升序")
    if first_test_month not in months:
        raise ValueError(f"first_test_month={first_test_month} 不在月份列表中")
    start_index = months.index(train_start)
    test_start = months.index(first_test_month)
    folds: list[FoldSpec] = []
    for offset in range(n_folds):
        val_index = test_start + offset * 2 - 1
        test_indices = [test_start + offset * 2, test_start + offset * 2 + 1]
        if test_indices[-1] >= len(months) or val_index <= start_index:
            break
        folds.append(
            FoldSpec(
                fold_id=f"F{offset + 1}",
                train_months=list(months[start_index:val_index]),
                val_month=months[val_index],
                test_months=[months[index] for index in test_indices],
            )
        )
    return folds


def require_purge_floor(purge_bars: int, *, label_mode: str, h_max: int, h_label: int) -> None:
    """校验 `purge_bars` 不小于标签最长持仓（03 §6、05 §5.1）。

    Args:
        purge_bars: `train.yaml` 的 Purge 根数（下限）。
        label_mode: `exit_sim` 或 `triple_barrier`。
        h_max: `exits.yaml` 的最长持仓根数。
        h_label: `features.yaml` 的时间障碍根数。

    Raises:
        ValueError: `purge_bars` 小于下限（`exit_sim` 取 `H_max + 1`，
            `triple_barrier` 取 `H_label + 1`）。
    """
    floor = h_max + 1 if label_mode == "exit_sim" else h_label + 1
    if purge_bars < floor:
        raise ValueError(
            f"purge_bars={purge_bars} 小于标签最长持仓 {floor}"
            f"（label.mode={label_mode}，h_max={h_max}，h_label={h_label}；03 §6）"
        )


def apply_purge_embargo(
    train_ds: WindowDataset,
    val_ds: WindowDataset,
    *,
    boundary_ms: int,
    purge_bars: int,
    embargo_days: int,
    next_boundary_ms: int | None = None,
) -> tuple[WindowDataset, WindowDataset]:
    """在训练/验证边界执行 Purge 与 Embargo（03 §6）。

    Purge：删除 `t_exit >= boundary_ms` 的训练样本，并保留 `purge_bars` 作为下限
    （`open_time` 须早于 `boundary_ms - purge_bars × 60s`）。
    Embargo：删除验证段开头 `embargo_days` 天的样本（避免与训练段标签重叠）。
    给出 `next_boundary_ms` 时，验证样本中 `t_exit` 落入下一段（测试月）的也被删除。

    Args:
        train_ds: 训练段数据集。
        val_ds: 验证段数据集。
        boundary_ms: 训练/验证边界（验证段起点，毫秒）。
        purge_bars: Purge 下限根数。
        embargo_days: 验证段开头屏蔽天数。
        next_boundary_ms: 验证/测试边界（毫秒）；None 表示不裁剪验证段尾部。

    Returns:
        (裁剪后的训练集, 裁剪后的验证集)。
    """
    purge_ms = purge_bars * 60_000
    train_mask = np.asarray(
        [
            sample.open_time <= boundary_ms - purge_ms and sample.t_exit < boundary_ms
            for sample in train_ds.samples
        ],
        dtype=bool,
    )
    embargo_until = boundary_ms + embargo_days * 86_400_000
    val_mask = np.asarray(
        [
            sample.open_time >= embargo_until
            and (next_boundary_ms is None or sample.t_exit < next_boundary_ms)
            for sample in val_ds.samples
        ],
        dtype=bool,
    )
    return train_ds.select(train_mask), val_ds.select(val_mask)


def predict_signals(
    dataset: WindowDataset,
    *,
    state_dicts: Sequence[dict[str, object]],
    arch: str,
    arch_params: dict[str, object],
    temperature: float,
    months: Sequence[str],
    batch_size: int = 2048,
) -> pd.DataFrame:
    """对数据集中指定月份的全部样本批量推理，返回 signals.parquet 结构。"""
    import torch  # 局部导入，保持模块导入轻量

    from trade.models.train import build_model, predict_dataset

    cfg = ModelConfig(
        **{
            key: arch_params[key]
            for key in ("hidden", "blocks", "kernel", "dropout", "window_L")
            if key in arch_params
        }
    )
    logits: list[np.ndarray] = []
    regs: list[np.ndarray] = []
    device = "cuda" if torch.cuda.is_available() else "cpu"
    for state_dict in state_dicts:
        model = build_model(arch, len(dataset.feature_names), cfg)
        model.load_state_dict(state_dict)  # type: ignore[arg-type]
        model.eval()
        model.to(device)
        logit, m = predict_dataset(model, dataset, batch_size=batch_size, device=device)
        logits.append(logit)
        regs.append(m)
    mean_logit = np.mean(np.stack(logits, axis=0), axis=0)
    p = 1.0 / (1.0 + np.exp(-mean_logit / temperature))
    m = np.mean(np.stack(regs, axis=0), axis=0)
    wanted = set(months)
    selected = [
        index
        for index, sample in enumerate(dataset.samples)
        if _month_key(sample.open_time) in wanted
    ]
    frame = pd.DataFrame(
        {
            "symbol": [dataset.symbols[dataset.samples[index].sym_id] for index in selected],
            "open_time": [dataset.samples[index].open_time for index in selected],
            "p": p[selected],
            "m": m[selected],
            "is_sample": 1,
        }
    )
    return frame.sort_values(["open_time", "symbol"]).reset_index(drop=True)


def _month_key(ts_ms: int) -> str:
    moment = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


SIGNALS_COLUMNS: tuple[str, ...] = (
    "symbol",
    "open_time",
    "p",
    "m",
    "is_sample",
    "fold_id",
    "theta",
)

# 校验五折一致性时比对的 model_meta.json 字段（05 §7.2、06 §8）
_MODEL_META_CONSISTENT_FIELDS = (
    "feature_hash",
    "exit_policy",
    "label",
    "universe_rule_hash",
    "market",
)


def load_walk_forward_signals(
    summary_path: Path, *, expected_months: Sequence[str]
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """读取 `walk_forward_summary.json`，校验后把各折测试段信号拼成连续测试段（06 §8）。

    拼接前逐折校验（任一不满足即报错并写明折号）：

    - 各折 `test_months` 不重叠、首尾相接，合起来恰好等于 `expected_months`（M13~M22）；
    - 各折 `model_meta.json` 的 `feature_hash`、`exit_policy`、`label`、`universe_rule_hash`、
      `market` 完全一致；
    - 信号文件存在且含 `symbol/open_time/p/m/is_sample/fold_id/theta`；
    - 每行 `open_time` 落在本折 `test_months` 内；
    - 不读取 `expected_months` 之外的月份（封存集 M23 及以后不加载）。

    Args:
        summary_path: `artifacts/models/{run_id}/walk_forward_summary.json`。
        expected_months: 期望的测试段月份（升序，M13~M22）。

    Returns:
        (拼接后的 signals（按 open_time、symbol 排序）, 每折元数据列表)。

    Raises:
        ValueError: 折号缺失、月份不连续/重叠、模型元数据不一致、缺列或行越界。
    """
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    folds = payload.get("folds")
    if not isinstance(folds, list) or not folds:
        raise ValueError(f"walk_forward_summary.json 没有 folds：{summary_path}")

    expected = list(expected_months)
    collected: list[str] = []
    metas: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    reference: dict[str, object] | None = None
    for entry in folds:
        if not isinstance(entry, dict):
            raise ValueError(f"walk_forward_summary.json 的折条目格式不合法：{summary_path}")
        fold_id = str(entry.get("fold_id"))
        test_months = [str(month) for month in entry.get("test_months") or []]
        if not test_months:
            raise ValueError(f"折 {fold_id} 没有 test_months：{summary_path}")
        overlap = set(test_months) & set(collected)
        if overlap:
            raise ValueError(f"折 {fold_id} 的测试月与其他折重叠：{sorted(overlap)}")
        if collected and test_months[0] <= collected[-1]:
            raise ValueError(
                f"折 {fold_id} 的测试月不连续："
                f"上一折止于 {collected[-1]}，本折起于 {test_months[0]}"
            )
        collected.extend(test_months)

        model_dir = Path(str(entry.get("model_dir")))
        meta_path = model_dir / "model_meta.json"
        if not meta_path.is_file():
            raise ValueError(f"折 {fold_id} 缺少 model_meta.json：{meta_path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        signature = {name: meta.get(name) for name in _MODEL_META_CONSISTENT_FIELDS}
        if reference is None:
            reference = signature
        elif signature != reference:
            diff = [
                name for name in _MODEL_META_CONSISTENT_FIELDS if signature[name] != reference[name]
            ]
            raise ValueError(f"折 {fold_id} 的 model_meta 与首折不一致：{diff}")

        signals_path = Path(str(entry.get("signals")))
        if not signals_path.is_file():
            raise ValueError(f"折 {fold_id} 缺少 signals 文件：{signals_path}")
        frame = pd.read_parquet(signals_path)
        missing = [name for name in SIGNALS_COLUMNS if name not in frame.columns]
        if missing:
            raise ValueError(f"折 {fold_id} 的 signals 缺列：{missing}（06 §8）")
        if not bool((frame["fold_id"].astype(str) == fold_id).all()):
            raise ValueError(f"折 {fold_id} 的 signals 存在其他折的行（fold_id 不一致）")
        months_of_row = {_month_key(int(ts)) for ts in frame["open_time"]}
        outside = sorted(months_of_row - set(test_months))
        if outside:
            raise ValueError(f"折 {fold_id} 的信号落在本折测试月之外：{outside}")
        frames.append(frame.loc[:, list(SIGNALS_COLUMNS)])

        metas.append(
            {
                "fold_id": fold_id,
                "test_months": test_months,
                "theta": float(entry.get("threshold", meta.get("threshold", 0.0))),
                "temperature": float(entry.get("temperature", meta.get("temperature", 0.0))),
                "val_pr_auc": float(entry.get("val_pr_auc", 0.0)),
                "n_test_signals": int(entry.get("n_test_signals", len(frame))),
                "model_dir": str(model_dir),
            }
        )

    if collected != expected:
        raise ValueError(
            f"五折测试月拼接后为 {collected}，与期望的测试段 {expected} 不一致（06 §8）"
        )
    signals = (
        pd.concat(frames, ignore_index=True)
        .sort_values(["open_time", "symbol"])
        .reset_index(drop=True)
    )
    return signals, metas


def run_fold(
    fold: FoldSpec,
    *,
    run_id: str,
    features_dir: Path,
    labels_dir: Path,
    feature_names: Sequence[str],
    train_symbols: Sequence[str],
    all_symbols: Sequence[str],
    cfg: ModelConfig,
    walk_cfg: WalkForwardConfig,
    threshold_evaluator: ThresholdEvaluator,
    features_cfg: FeaturesConfig,
    exit_cfg: ExitConfig,
    universe_rule_hash: str,
    market: str,
    models_root: Path,
    signals_root: Path,
    manifest_path: Path,
) -> FoldResult:
    """训练单折并产出测试段 signals.parquet（05 §6、§7）。"""
    train_ds = WindowDataset(
        features_dir,
        labels_dir,
        list(train_symbols),
        list(feature_names),
        window_L=cfg.window_L,
        months=fold.train_months,
        data_placement=cfg.data_placement,
    )
    val_ds = WindowDataset(
        features_dir,
        labels_dir,
        list(train_symbols),
        list(feature_names),
        window_L=cfg.window_L,
        months=[fold.val_month],
        data_placement=cfg.data_placement,
    )
    require_purge_floor(
        walk_cfg.purge_bars,
        label_mode=features_cfg.label.mode,
        h_max=int(exit_cfg.H_max),
        h_label=int(features_cfg.label.H_label),
    )
    train_ds, val_ds = apply_purge_embargo(
        train_ds,
        val_ds,
        boundary_ms=month_start_ms(fold.val_month),
        purge_bars=walk_cfg.purge_bars,
        embargo_days=walk_cfg.embargo_days,
        next_boundary_ms=(month_start_ms(fold.test_months[0]) if fold.test_months else None),
    )
    short_hash = feature_hash(enabled_features(features_cfg))
    model_id = f"{make_model_id(cfg.arch, short_hash)}_{fold.fold_id}"
    model_dir = models_root / model_id
    result = train_ensemble(train_ds, val_ds, cfg, out_dir=model_dir)
    # 温度缩放在 5 种子集成后的平均 logit 上拟合（05 §6 第 4 条、§7.1），与推理口径一致。
    ensemble_logit = np.mean(np.stack([np.asarray(item) for item in result.val_logits]), axis=0)
    calibration = calibration_metrics(ensemble_logit, np.asarray(result.val_y))
    temperature = float(calibration["temperature"])

    val_signals = predict_signals(
        val_ds,
        state_dicts=result.state_dicts,
        arch=result.arch,
        arch_params=result.arch_params,
        temperature=temperature,
        months=[fold.val_month],
    )
    n_days = (month_end_ms(fold.val_month) - month_start_ms(fold.val_month)) / 86_400_000
    choice = select_threshold(threshold_evaluator(val_signals, n_days), n_days=n_days)

    test_ds = WindowDataset(
        features_dir,
        labels_dir,
        list(all_symbols),
        list(feature_names),
        window_L=cfg.window_L,
        months=fold.test_months,
        data_placement=cfg.data_placement,
    )
    test_signals = predict_signals(
        test_ds,
        state_dicts=result.state_dicts,
        arch=result.arch,
        arch_params=result.arch_params,
        temperature=temperature,
        months=fold.test_months,
    )
    # signals.parquet 契约：每行带本折 fold_id 与本折选定阈值 θ（06 §8）
    test_signals["fold_id"] = fold.fold_id
    test_signals["theta"] = float(choice.theta)
    signals_root.mkdir(parents=True, exist_ok=True)
    signals_path = signals_root / f"{run_id}_{fold.fold_id}.parquet"
    test_signals.to_parquet(signals_path, index=False)

    write_model_meta(
        model_dir,
        model_id=model_id,
        arch=result.arch,
        arch_params=result.arch_params,
        window_L=cfg.window_L,
        features_cfg=features_cfg,
        exit_cfg=exit_cfg,
        market=market,
        universe_rule_hash=universe_rule_hash,
        train_symbols=list(train_symbols),
        train_range=(fold.train_months[0], fold.train_months[-1]),
        val_range=(fold.val_month, fold.val_month),
        ensemble_seeds=cfg.seeds,
        temperature=temperature,
        threshold=choice.theta,
        dataset_manifest_sha256=sha256_file(manifest_path),
        threshold_selection={
            "theta": choice.theta,
            "best_theta": choice.best_theta,
            "best_profit_factor": choice.best_profit_factor,
            "n_trades": choice.n_trades,
            "n_days": n_days,
            "n_candidates": choice.n_candidates,
            "validation_pr_auc": result.val_pr_auc,
            "ece_before": calibration["ece_before"],
            "ece_after": calibration["ece_after"],
            # 验证集笔数与 θ 网格上的 PF 曲线（05 §9）
            "validation_n_trades": choice.n_trades,
            "pf_curve": [
                {
                    "theta": theta,
                    "profit_factor": pf,
                    "n_trades": choice.n_trades_by_theta.get(theta, 0),
                }
                for theta, pf in sorted(choice.pf_by_theta.items())
            ],
        },
    )
    return FoldResult(
        fold=fold,
        model_dir=model_dir,
        threshold=choice.theta,
        temperature=temperature,
        val_pr_auc=result.val_pr_auc,
        n_test_signals=int(len(test_signals)),
        signals_path=signals_path,
    )


def run_walk_forward(
    folds: Sequence[FoldSpec],
    *,
    output_dir: Path,
    **kwargs: object,
) -> dict[str, object]:
    """逐折训练并写 `walk_forward_summary.json`（每折同一冻结超参）。"""
    results: list[FoldResult] = []
    for fold in folds:
        results.append(run_fold(fold, **kwargs))  # type: ignore[arg-type]
    summary: dict[str, object] = {
        "created_at": datetime.now(UTC).isoformat(),
        "folds": [
            {
                "fold_id": item.fold.fold_id,
                "train_months": item.fold.train_months,
                "val_month": item.fold.val_month,
                "test_months": item.fold.test_months,
                "model_dir": str(item.model_dir),
                "threshold": item.threshold,
                "temperature": item.temperature,
                "val_pr_auc": item.val_pr_auc,
                "n_test_signals": item.n_test_signals,
                "signals": str(item.signals_path),
            }
            for item in results
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "walk_forward_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def run_final(
    *,
    run_id: str,
    features_dir: Path,
    labels_dir: Path,
    feature_names: Sequence[str],
    train_symbols: Sequence[str],
    train_months: Sequence[str],
    val_month: str,
    cfg: ModelConfig,
    walk_cfg: WalkForwardConfig,
    threshold_evaluator: ThresholdEvaluator,
    features_cfg: FeaturesConfig,
    exit_cfg: ExitConfig,
    universe_rule_hash: str,
    market: str,
    models_root: Path,
    manifest_path: Path,
) -> dict[str, object]:
    """训练 F_final（M1~M21 训练、M22 验证），只产出模型目录与元数据（03 §6）。"""
    train_ds = WindowDataset(
        features_dir,
        labels_dir,
        list(train_symbols),
        list(feature_names),
        window_L=cfg.window_L,
        months=list(train_months),
        data_placement=cfg.data_placement,
    )
    val_ds = WindowDataset(
        features_dir,
        labels_dir,
        list(train_symbols),
        list(feature_names),
        window_L=cfg.window_L,
        months=[val_month],
        data_placement=cfg.data_placement,
    )
    require_purge_floor(
        walk_cfg.purge_bars,
        label_mode=features_cfg.label.mode,
        h_max=int(exit_cfg.H_max),
        h_label=int(features_cfg.label.H_label),
    )
    train_ds, val_ds = apply_purge_embargo(
        train_ds,
        val_ds,
        boundary_ms=month_start_ms(val_month),
        purge_bars=walk_cfg.purge_bars,
        embargo_days=walk_cfg.embargo_days,
    )
    short_hash = feature_hash(enabled_features(features_cfg))
    model_id = f"{make_model_id(cfg.arch, short_hash)}_final"
    model_dir = models_root / model_id
    result = train_ensemble(train_ds, val_ds, cfg, out_dir=model_dir)
    # 温度缩放在 5 种子集成后的平均 logit 上拟合（05 §6 第 4 条、§7.1），与推理口径一致。
    ensemble_logit = np.mean(np.stack([np.asarray(item) for item in result.val_logits]), axis=0)
    calibration = calibration_metrics(ensemble_logit, np.asarray(result.val_y))
    temperature = float(calibration["temperature"])
    val_signals = predict_signals(
        val_ds,
        state_dicts=result.state_dicts,
        arch=result.arch,
        arch_params=result.arch_params,
        temperature=temperature,
        months=[val_month],
    )
    n_days = (month_end_ms(val_month) - month_start_ms(val_month)) / 86_400_000
    choice = select_threshold(threshold_evaluator(val_signals, n_days), n_days=n_days)
    write_model_meta(
        model_dir,
        model_id=model_id,
        arch=result.arch,
        arch_params=result.arch_params,
        window_L=cfg.window_L,
        features_cfg=features_cfg,
        exit_cfg=exit_cfg,
        market=market,
        universe_rule_hash=universe_rule_hash,
        train_symbols=list(train_symbols),
        train_range=(train_months[0], train_months[-1]),
        val_range=(val_month, val_month),
        ensemble_seeds=cfg.seeds,
        temperature=temperature,
        threshold=choice.theta,
        dataset_manifest_sha256=sha256_file(manifest_path),
        threshold_selection={
            "theta": choice.theta,
            "best_theta": choice.best_theta,
            "best_profit_factor": choice.best_profit_factor,
            "n_trades": choice.n_trades,
            "n_days": n_days,
            "validation_pr_auc": result.val_pr_auc,
            "validation_n_trades": choice.n_trades,
            "pf_curve": [
                {
                    "theta": theta,
                    "profit_factor": pf,
                    "n_trades": choice.n_trades_by_theta.get(theta, 0),
                }
                for theta, pf in sorted(choice.pf_by_theta.items())
            ],
        },
    )
    summary = {
        "run_id": run_id,
        "model_id": model_id,
        "model_dir": str(model_dir),
        "threshold": choice.theta,
        "temperature": temperature,
        "val_pr_auc": result.val_pr_auc,
    }
    (model_dir / "final_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
