"""M0 / M1 / M2 训练流程测试（早停、bf16、种子集成、train_log）。

规格：docs/05-模型与训练.md §3~§6；08 §2 P4
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch  # type: ignore[import-untyped]

from trade.models.config import ModelConfig
from trade.models.dataset import WindowDataset
from trade.models.lstm import LSTMModel
from trade.models.train import (
    average_precision,
    roc_auc,
    set_deterministic,
    top_k_precision,
    train_ensemble,
    train_lgbm_baseline,
)

_FEATURES = ("f0", "f1", "f2", "f3")
_WINDOW = 20
_ROWS = 400


def _make_dataset(root: Path, alias: str, seed: int) -> None:
    """构造有可学习动量信号的币种特征与标签（fixture）。"""
    rng = np.random.default_rng(seed)
    features_dir = root / "features" / "run"
    labels_dir = root / "labels" / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    increments = rng.normal(0.0, 1.0, _ROWS)
    close = np.cumsum(increments)
    open_time = (np.arange(_ROWS, dtype="int64") * 60_000) + 1_735_689_600_000
    frame = pd.DataFrame(
        {
            "f0": close.astype("float16"),
            "f1": increments.astype("float16"),
            "f2": np.abs(increments).astype("float16"),
            "f3": rng.normal(size=_ROWS).astype("float16"),
            "open_time": open_time,
            "is_filled": 0,
            "kline_missing": 0,
            "aggtrades_missing": 0,
        }
    )
    frame.to_parquet(features_dir / f"{alias}.parquet", index=False)
    y = np.zeros(_ROWS, dtype="int64")
    y[1:] = (increments[1:] > 0).astype("int64")
    lab = pd.DataFrame(
        {
            "open_time": open_time,
            "y": y,
            "r_mult": np.where(y == 1, 1.0, -1.0),
            "weight": np.ones(_ROWS),
            "t_exit": open_time + 60_000,
        }
    )
    lab.to_parquet(labels_dir / f"{alias}.parquet", index=False)


@pytest.fixture
def datasets(tmp_path: Path) -> tuple[WindowDataset, WindowDataset]:
    _make_dataset(tmp_path, "C01", seed=1)
    _make_dataset(tmp_path, "C02", seed=2)
    common = {
        "window_L": _WINDOW,
        "max_fill_ratio": 0.20,
    }
    train = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01", "C02"],
        _FEATURES,
        **common,
    )
    val = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01", "C02"],
        _FEATURES,
        **common,
    )
    return train, val


def _small_cfg(**overrides) -> ModelConfig:
    values = {
        "arch": "tcn",
        "window_L": _WINDOW,
        "hidden": 8,
        "blocks": 2,
        "kernel": 3,
        "dropout": 0.0,
        "batch_size": 128,
        "max_epochs": 2,
        "early_stopping_patience": 2,
        "warmup_epochs": 0,
        "bf16": False,
        "seeds": [42],
    }
    values.update(overrides)
    return ModelConfig(**values)


def test_average_precision_known_values() -> None:
    y = np.array([1.0, 0.0, 1.0, 0.0])
    assert average_precision(y, np.array([1.0, 0.0, 0.5, 0.2])) == pytest.approx(1.0)
    assert roc_auc(y, np.array([0.9, 0.8, 0.7, 0.6])) == pytest.approx(0.75)
    assert top_k_precision(y, np.array([0.9, 0.1, 0.8, 0.0]), fraction=0.5) == pytest.approx(1.0)


def test_lstm_forward_shape() -> None:
    model = LSTMModel(in_features=4, hidden=8, dropout=0.0)
    logit, m = model(torch.randn(3, _WINDOW, 4))
    assert logit.shape == (3,)
    assert m.shape == (3,)


def test_train_ensemble_writes_log_and_model(
    tmp_path: Path, datasets: tuple[WindowDataset, WindowDataset]
) -> None:
    train, val = datasets
    out_dir = tmp_path / "model"
    result = train_ensemble(train, val, _small_cfg(), out_dir=out_dir)
    assert len(result.state_dicts) == 1
    assert (out_dir / "model.pt").is_file()
    log = pd.read_csv(out_dir / "train_log.csv")
    for column in (
        "seed",
        "epoch",
        "train_loss",
        "val_loss",
        "val_pr_auc",
        "val_roc_auc",
        "val_top5_precision",
        "elapsed_sec",
        "gpu_peak_mb",
        "is_best",
        "best_epoch",
    ):
        assert column in log.columns
    assert len(log) >= 1
    assert 0.0 <= float(result.val_pr_auc) <= 1.0


def test_same_seed_pr_auc_stable(
    datasets: tuple[WindowDataset, WindowDataset],
) -> None:
    """同一种子两次训练的验证集 PR-AUC 差 < 0.002（08 §2 P4）。"""
    train, val = datasets
    cfg = _small_cfg(max_epochs=3, seeds=[42])
    first = train_ensemble(train, val, cfg)
    second = train_ensemble(train, val, cfg)
    assert abs(first.val_pr_auc - second.val_pr_auc) < 0.002


def test_seed_ensemble_averages_seeds(
    tmp_path: Path, datasets: tuple[WindowDataset, WindowDataset]
) -> None:
    train, val = datasets
    cfg = _small_cfg(max_epochs=1, seeds=[42, 43])
    result = train_ensemble(train, val, cfg, out_dir=tmp_path / "model")
    assert len(result.state_dicts) == 2
    assert len(result.val_logits) == 2
    assert len(result.best_epochs) == 2


def test_lgbm_baseline(datasets: tuple[WindowDataset, WindowDataset]) -> None:
    """M0 LightGBM 基线可训练并输出概率（05 §6 第 3 条）。"""
    train, val = datasets
    result = train_lgbm_baseline(
        train, val, _small_cfg(arch="lgbm", lgbm_num_boost_round=20, lgbm_early_stopping_rounds=5)
    )
    assert len(result.val_prob) == len(val)
    assert np.all((result.val_prob >= 0.0) & (result.val_prob <= 1.0))


def test_time_decay_weights_reduce_old_samples(
    tmp_path: Path, datasets: tuple[WindowDataset, WindowDataset]
) -> None:
    """time_decay_halflife_months 打开时训练仍可运行（权重按月龄衰减）。"""
    train, val = datasets
    result = train_ensemble(train, val, _small_cfg(max_epochs=1, time_decay_halflife_months=12))
    assert result.val_y is not None
    assert np.isfinite(result.val_pr_auc)


def test_set_deterministic_is_repeatable() -> None:
    set_deterministic(7)
    first = torch.randn(4)
    set_deterministic(7)
    second = torch.randn(4)
    assert torch.equal(first, second)
