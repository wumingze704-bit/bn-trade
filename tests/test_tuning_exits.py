"""T4-3 第 1 步试验登记测试（05 §6.1、06 §8）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from scripts.tuning_exits import _BACKFILL_TRIALS, _backfill_log, _trial_run_id

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
