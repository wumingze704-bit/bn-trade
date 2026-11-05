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


def apply_purge_embargo(
    train_ds: WindowDataset,
    val_ds: WindowDataset,
    *,
    boundary_ms: int,
    purge_bars: int,
    embargo_days: int,
) -> tuple[WindowDataset, WindowDataset]:
    """在训练/验证边界执行 Purge 与 Embargo（03 §6）。

    Purge：删除训练集末尾 `purge_bars` 根内的样本（标签窗口会延伸进验证段）。
    Embargo：删除验证段开头 `embargo_days` 天的样本（避免与训练段标签重叠）。
    """
    purge_ms = purge_bars * 60_000
    train_mask = np.asarray(
        [sample.open_time <= boundary_ms - purge_ms for sample in train_ds.samples], dtype=bool
    )
    embargo_until = boundary_ms + embargo_days * 86_400_000
    val_mask = np.asarray(
        [sample.open_time >= embargo_until for sample in val_ds.samples], dtype=bool
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
    train_ds, val_ds = apply_purge_embargo(
        train_ds,
        val_ds,
        boundary_ms=month_start_ms(fold.val_month),
        purge_bars=walk_cfg.purge_bars,
        embargo_days=walk_cfg.embargo_days,
    )
    short_hash = feature_hash(enabled_features(features_cfg))
    model_id = f"{make_model_id(cfg.arch, short_hash)}_{fold.fold_id}"
    model_dir = models_root / model_id
    result = train_ensemble(train_ds, val_ds, cfg, out_dir=model_dir)
    calibration = calibration_metrics(
        np.concatenate([np.asarray(logit) for logit in result.val_logits]),
        np.asarray(result.val_y),
    )
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
    calibration = calibration_metrics(
        np.concatenate([np.asarray(logit) for logit in result.val_logits]),
        np.asarray(result.val_y),
    )
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
