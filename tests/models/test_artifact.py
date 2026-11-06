"""模型产物加载与一致性校验测试。

规格：docs/05-模型与训练.md §9；08 §2 P4
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.data.universe import load_universe_config, rule_hash
from trade.features import enabled_features
from trade.features.registry import FeaturesConfig
from trade.models.artifact import ArtifactError, load
from trade.models.calibration import sha256_file, write_model_meta
from trade.models.config import ModelConfig
from trade.models.dataset import WindowDataset
from trade.models.train import train_ensemble, train_lgbm_baseline

_ROOT = Path(__file__).resolve().parents[2]
_WINDOW = 10
_ROWS = 160


def _make_coin(root: Path, alias: str, feature_names: list[str], seed: int) -> None:
    rng = np.random.default_rng(seed)
    features_dir = root / "features" / "run"
    labels_dir = root / "labels" / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    open_time = np.arange(_ROWS, dtype="int64") * 60_000 + 1_735_689_600_000
    frame = pd.DataFrame(rng.normal(size=(_ROWS, len(feature_names))).astype("float16"))
    frame.columns = feature_names
    frame["open_time"] = open_time
    frame["is_filled"] = 0
    frame.to_parquet(features_dir / f"{alias}.parquet", index=False)
    y = (rng.random(_ROWS) > 0.5).astype("int64")
    pd.DataFrame(
        {
            "open_time": open_time,
            "y": y,
            "r_mult": np.where(y == 1, 1.0, -1.0),
            "weight": 1.0,
        }
    ).to_parquet(labels_dir / f"{alias}.parquet", index=False)


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    features_cfg = load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml")
    feature_names = [feature.name for feature in enabled_features(features_cfg)]
    _make_coin(tmp_path, "C01", feature_names, seed=3)
    dataset = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        feature_names,
        window_L=_WINDOW,
    )
    cfg = ModelConfig(
        arch="tcn",
        window_L=_WINDOW,
        hidden=8,
        blocks=2,
        batch_size=64,
        max_epochs=1,
        warmup_epochs=0,
        bf16=False,
        seeds=[42],
    )
    model_dir = tmp_path / "models" / "20261105_tcn_abcdef"
    result = train_ensemble(dataset, dataset, cfg, out_dir=model_dir)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    exits_cfg = load_config(ExitConfig, _ROOT / "configs" / "exits.yaml")
    universe_cfg = load_universe_config(_ROOT / "configs" / "universe.yaml")
    write_model_meta(
        model_dir,
        model_id=model_dir.name,
        arch="tcn",
        arch_params=result.arch_params,
        window_L=_WINDOW,
        features_cfg=features_cfg,
        exit_cfg=exits_cfg,
        market="usdm",
        universe_rule_hash=rule_hash(universe_cfg),
        train_symbols=["C01"],
        train_range=("2025-01-01", "2025-01-31"),
        val_range=("2025-02-01", "2025-02-28"),
        ensemble_seeds=[42],
        temperature=1.0,
        threshold=0.5,
        dataset_manifest_sha256=sha256_file(manifest),
    )
    return model_dir


def _configs() -> tuple[FeaturesConfig, ExitConfig, object, BaseConfig]:
    return (
        load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml"),
        load_config(ExitConfig, _ROOT / "configs" / "exits.yaml"),
        load_universe_config(_ROOT / "configs" / "universe.yaml"),
        load_config(BaseConfig, _ROOT / "configs" / "base.yaml"),
    )


def test_一致时正常加载(artifact_dir: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    predictor = load(
        artifact_dir,
        features_config=features_cfg,
        exit_config=exits_cfg,
        universe_config=universe_cfg,
        base_config=base_cfg,
    )
    assert predictor.model_id == artifact_dir.name
    x = np.random.default_rng(0).normal(size=(_WINDOW, len(predictor.meta["features"])))
    p, m = predictor.predict(x)
    assert 0.0 <= p <= 1.0
    assert np.isfinite(m)
    batch_p, batch_m = predictor.predict_batch(np.stack([x, x]))
    assert batch_p.shape == (2,)
    assert batch_m.shape == (2,)


def test_特征哈希不一致时报错(artifact_dir: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    meta_path = artifact_dir / "model_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["feature_hash"] = "0" * 64
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ArtifactError, match="feature_hash"):
        load(
            artifact_dir,
            features_config=features_cfg,
            exit_config=exits_cfg,
            universe_config=universe_cfg,
            base_config=base_cfg,
        )


def test_特征列表不一致时报错(artifact_dir: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    meta_path = artifact_dir / "model_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["features"] = meta["features"][:-1]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ArtifactError, match="特征列表"):
        load(
            artifact_dir,
            features_config=features_cfg,
            exit_config=exits_cfg,
            universe_config=universe_cfg,
            base_config=base_cfg,
        )


def test_出场策略不一致时报错(artifact_dir: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    meta_path = artifact_dir / "model_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["exit_policy"]["H_max"] = int(meta["exit_policy"]["H_max"]) + 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ArtifactError, match="出场策略"):
        load(
            artifact_dir,
            features_config=features_cfg,
            exit_config=exits_cfg,
            universe_config=universe_cfg,
            base_config=base_cfg,
        )


def test_market不一致时报错(artifact_dir: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    meta_path = artifact_dir / "model_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["market"] = "spot"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ArtifactError, match="market"):
        load(
            artifact_dir,
            features_config=features_cfg,
            exit_config=exits_cfg,
            universe_config=universe_cfg,
            base_config=base_cfg,
        )


def test_缺少产物文件时报错(tmp_path: Path) -> None:
    features_cfg, exits_cfg, universe_cfg, base_cfg = _configs()
    with pytest.raises(ArtifactError, match="model_meta.json"):
        load(
            tmp_path / "missing",
            features_config=features_cfg,
            exit_config=exits_cfg,
            universe_config=universe_cfg,
            base_config=base_cfg,
        )


def test_m0_lgbm产物可加载并推理(tmp_path: Path) -> None:
    """M0 产物：lgbm.txt + model_meta.json 可加载，窗口→表格特征与训练一致。"""
    features_cfg = load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml")
    exits_cfg = load_config(ExitConfig, _ROOT / "configs" / "exits.yaml")
    universe_cfg = load_universe_config(_ROOT / "configs" / "universe.yaml")
    base_cfg = load_config(BaseConfig, _ROOT / "configs" / "base.yaml")
    feature_names = [feature.name for feature in enabled_features(features_cfg)]
    _make_coin(tmp_path, "C01", feature_names, seed=7)
    dataset = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        feature_names,
        window_L=20,
    )
    cfg = ModelConfig(
        arch="lgbm",
        window_L=20,
        lgbm_num_boost_round=10,
        lgbm_early_stopping_rounds=3,
    )
    result = train_lgbm_baseline(dataset, dataset, cfg)
    model_dir = tmp_path / "models" / "20261105_lgbm_abcdef"
    model_dir.mkdir(parents=True)
    (model_dir / "lgbm.txt").write_text(result.booster.model_to_string(), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    write_model_meta(
        model_dir,
        model_id=model_dir.name,
        arch="lgbm",
        arch_params={},
        window_L=20,
        features_cfg=features_cfg,
        exit_cfg=exits_cfg,
        market="usdm",
        universe_rule_hash=rule_hash(universe_cfg),
        train_symbols=["C01"],
        train_range=("2025-01-01", "2025-01-31"),
        val_range=("2025-02-01", "2025-02-28"),
        ensemble_seeds=[42],
        temperature=1.0,
        threshold=0.5,
        dataset_manifest_sha256=sha256_file(manifest),
    )
    predictor = load(
        model_dir,
        features_config=features_cfg,
        exit_config=exits_cfg,
        universe_config=universe_cfg,
        base_config=base_cfg,
    )
    x = np.random.default_rng(1).normal(size=(20, len(feature_names)))
    p, m = predictor.predict(x)
    assert 0.0 <= p <= 1.0
    assert m == 0.0
