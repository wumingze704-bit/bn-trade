"""T4-3 第 1 步试验登记测试（05 §6.1、06 §8）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

from scripts.tuning_exits import _BACKFILL_TRIALS, _backfill_log, _load_disk_features, _trial_run_id
from trade.common.config import load_config
from trade.features import FeaturesConfig, enabled_features, feature_hash

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_temp_base(tmp_path: Path) -> Path:
    """复制 configs/base.yaml，把 artifact_root 指向临时目录（避免写真实 artifacts）。"""
    values = yaml.safe_load((_REPO_ROOT / "configs" / "base.yaml").read_text(encoding="utf-8"))
    values["paths"] = {
        "data_root": str(_REPO_ROOT / "data"),
        "artifact_root": str(tmp_path / "artifacts"),
    }
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
    return path


def test_补登日志写入18组且重复执行不重复登记(tmp_path):
    base_path = _write_temp_base(tmp_path)
    args = argparse.Namespace(base=str(base_path))
    _backfill_log(tmp_path, args)
    log_path = tmp_path / "artifacts" / "reports" / "EXPERIMENTS.log"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == len(_BACKFILL_TRIALS) == 18
    assert all(row["n_trades"] is None for row in rows)
    assert all(row["sharpe"] is None for row in rows)

    _backfill_log(tmp_path, args)
    rows_again = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(rows_again) == 18


def test_补登run_id唯一且标注结果缺失():
    run_ids = [_trial_run_id(trial) for trial in _BACKFILL_TRIALS]
    assert len(set(run_ids)) == 18
    assert all(run_id.endswith("_n_missing") for run_id in run_ids)
    assert sum("valM11-12" in run_id for run_id in run_ids) == 12
    assert sum("valM10-12" in run_id for run_id in run_ids) == 6


def test_数据集feature_hash不一致时报错(tmp_path: Path) -> None:
    features_dir = tmp_path / "ds"
    features_dir.mkdir()
    features_cfg = load_config(FeaturesConfig, _REPO_ROOT / "configs" / "features.yaml")
    names = [feature.name for feature in enabled_features(features_cfg)]
    manifest = {
        "feature_hash": "deadbeef",
        "features": names,
        "window": {"start_ms": 0, "end_ms": 10**15},
    }
    (features_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="feature_hash"):
        _load_disk_features(features_dir, features_cfg, names, ["C01"], 0, 1)


def test_数据集覆盖区间不足时报错(tmp_path: Path) -> None:
    features_dir = tmp_path / "ds"
    features_dir.mkdir()
    features_cfg = load_config(FeaturesConfig, _REPO_ROOT / "configs" / "features.yaml")
    features = enabled_features(features_cfg)
    names = [feature.name for feature in features]
    manifest = {
        "feature_hash": feature_hash(features),
        "features": names,
        "window": {"start_ms": 1_000, "end_ms": 2_000},
    }
    (features_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="不包含所需区间"):
        _load_disk_features(features_dir, features_cfg, names, ["C01"], 0, 3_000)
