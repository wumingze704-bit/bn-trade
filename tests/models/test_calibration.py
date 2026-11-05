"""概率校准、阈值选择与 model_meta.json 测试。

规格：docs/05-模型与训练.md §7、§9
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from trade.common.config import load_config
from trade.common.exits import ExitConfig, exit_policy_hash
from trade.data.universe import load_universe_config, rule_hash
from trade.features import enabled_features, feature_hash
from trade.features.registry import FeaturesConfig
from trade.models.calibration import (
    calibration_metrics,
    expected_calibration_error,
    fit_temperature,
    make_model_id,
    select_threshold,
    sha256_file,
    sigmoid,
    threshold_grid,
    write_model_meta,
)

_ROOT = Path(__file__).resolve().parents[2]


def test_threshold_grid_range() -> None:
    grid = threshold_grid()
    assert grid[0] == 0.30
    assert grid[-1] == 0.80
    assert len(grid) == 26
    assert all(abs(b - a - 0.02) < 1e-9 for a, b in zip(grid, grid[1:], strict=False))


def test_select_threshold_prefers_pf_then_more_trades() -> None:
    metrics = {
        0.30: {"net_return": 10.0, "profit_factor": 1.0, "n_trades": 300},
        0.34: {"net_return": 12.0, "profit_factor": 1.36, "n_trades": 200},
        0.40: {"net_return": 15.0, "profit_factor": 1.5, "n_trades": 120},
        0.50: {"net_return": 14.0, "profit_factor": 1.4, "n_trades": 90},
        0.60: {"net_return": 8.0, "profit_factor": 1.2, "n_trades": 30},
    }
    choice = select_threshold(metrics, n_days=30)
    assert choice.best_theta == 0.40
    assert choice.best_profit_factor == pytest.approx(1.5)
    # 0.9 × 1.5 = 1.35 → 取 PF ≥ 1.35 的最小 θ（偏向更多交易）
    assert choice.theta == 0.34
    assert choice.n_trades == 200
    assert choice.daily_trades == pytest.approx(200 / 30)


def test_select_threshold_requires_min_trades() -> None:
    metrics = {0.40: {"net_return": 5.0, "profit_factor": 2.0, "n_trades": 30}}
    with pytest.raises(ValueError, match="日均"):
        select_threshold(metrics, n_days=30, min_trades_per_day=3.0)


def test_fit_temperature_recovers_sharpness() -> None:
    rng = np.random.default_rng(0)
    true_logits = rng.normal(0.0, 2.0, size=20000)
    true_p = sigmoid(true_logits / 2.0)
    y = (rng.random(len(true_p)) < true_p).astype("float64")
    temperature = fit_temperature(true_logits, y)
    assert 1.7 < temperature < 2.3


def test_calibration_improves_ece() -> None:
    rng = np.random.default_rng(1)
    true_logits = rng.normal(0.0, 2.0, size=20000)
    y = (rng.random(len(true_logits)) < sigmoid(true_logits)).astype("float64")
    # 模型过度自信：logit 放大 2 倍
    metrics = calibration_metrics(true_logits * 2.0, y)
    assert metrics["temperature"] > 1.0
    assert metrics["ece_after"] <= metrics["ece_before"]
    assert 0.0 <= expected_calibration_error(y, sigmoid(true_logits)) <= 1.0


def test_write_model_meta_fields(tmp_path: Path) -> None:
    features_cfg = load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml")
    exits_cfg = load_config(ExitConfig, _ROOT / "configs" / "exits.yaml")
    universe_cfg = load_universe_config(_ROOT / "configs" / "universe.yaml")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    model_dir = tmp_path / "model"

    meta = write_model_meta(
        model_dir,
        model_id="20261105_tcn_abcdef",
        arch="tcn",
        arch_params={"hidden": 64, "blocks": 4, "kernel": 3, "dropout": 0.2},
        window_L=60,
        features_cfg=features_cfg,
        exit_cfg=exits_cfg,
        market="usdm",
        universe_rule_hash=rule_hash(universe_cfg),
        train_symbols=["C001"],
        train_range=("2024-10-01", "2026-06-30"),
        val_range=("2026-07-01", "2026-07-31"),
        ensemble_seeds=[42, 43],
        temperature=1.25,
        threshold=0.56,
        dataset_manifest_sha256=sha256_file(manifest),
        threshold_selection={"theta": 0.56, "n_trades": 120, "n_days": 30},
    )
    assert meta["label"]["mode"] == "exit_sim"
    assert meta["label"]["y_min_r"] == features_cfg.label.y_min_r
    assert meta["exit_policy"] == exits_cfg.model_dump()
    assert exit_policy_hash(ExitConfig(**meta["exit_policy"])) == exit_policy_hash(exits_cfg)
    assert meta["feature_hash"] == feature_hash(enabled_features(features_cfg))
    assert meta["universe_rule_hash"] == rule_hash(universe_cfg)
    assert meta["market"] == "usdm"
    for key in (
        "model_id",
        "arch",
        "arch_params",
        "window_L",
        "features",
        "feature_hash",
        "ewm_halflife_bars",
        "prefilter",
        "label",
        "exit_policy",
        "ensemble_seeds",
        "temperature",
        "threshold",
        "universe_rule_hash",
        "train_symbols",
        "train_range",
        "val_range",
        "dataset_manifest_sha256",
        "torch_version",
        "created_at",
    ):
        assert key in meta
    written = json.loads((model_dir / "model_meta.json").read_text(encoding="utf-8"))
    assert written["threshold_selection"]["theta"] == 0.56


def test_make_model_id_format() -> None:
    model_id = make_model_id("tcn", "abcdef123456")
    assert model_id.startswith("20")
    assert model_id.endswith("_tcn_abcdef")
