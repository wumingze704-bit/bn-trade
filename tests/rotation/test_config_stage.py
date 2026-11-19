"""轮动策略配置与阶段守卫测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade.common.config import ConfigError
from trade.rotation.config import load_rotation_config
from trade.rotation.data import read_month_bars
from trade.rotation.stage import (
    FINAL_EVAL_END_MS,
    FINAL_EVAL_START_MS,
    HOLDOUT_START_MS,
    M12_END_MS,
    M22_END_MS,
    check_window,
    read_verdict,
    stage_window,
)

_ROOT = Path(__file__).resolve().parents[2]


def test_读取仓库配置() -> None:
    """工作配置与冻结配置必须一致（冻结后两者都不再修改，11 §2）。"""
    cfg = load_rotation_config(_ROOT / "configs" / "rotation.yaml")
    frozen = load_rotation_config(_ROOT / "configs" / "rotation_frozen.yaml")
    assert cfg == frozen
    assert 0.0 < frozen.stop_max_pct < 0.5
    assert frozen.rebalance_hours in (1, 4, 8)


def test_未知字段被拒绝(tmp_path: Path) -> None:
    path = tmp_path / "rotation.yaml"
    path.write_text("ma_days: 50\nunknown_key: 1\n", "utf-8")
    with pytest.raises(ConfigError):
        load_rotation_config(path)


def test_非法调仓周期被拒绝(tmp_path: Path) -> None:
    path = tmp_path / "rotation.yaml"
    path.write_text("rebalance_hours: 3\n", "utf-8")
    with pytest.raises(ConfigError):
        load_rotation_config(path)


def test_research阶段只能到M12末尾(tmp_path: Path) -> None:
    window = stage_window("research", docs_dir=tmp_path, artifact_root=tmp_path)
    assert window.eval_end_ms == M12_END_MS
    assert window.allow_holdout is False
    with pytest.raises(ValueError):
        check_window(window, window.eval_start_ms, M12_END_MS + 1)


def test_confirm需要phase1为PASS(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase1.md").write_text("- 结论：FAIL\n", "utf-8")
    with pytest.raises(ValueError):
        stage_window("confirm", docs_dir=tmp_path, artifact_root=tmp_path)


def test_confirm在phase2已PASS时被拒绝(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase1.md").write_text("- 结论：PASS\n", "utf-8")
    (tmp_path / "rotation" / "phase2.md").write_text("- 结论：PASS\n", "utf-8")
    with pytest.raises(ValueError):
        stage_window("confirm", docs_dir=tmp_path, artifact_root=tmp_path)


def test_confirm区间上限为M22末尾(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase1.md").write_text("**结论**：PASS\n", "utf-8")
    window = stage_window("confirm", docs_dir=tmp_path, artifact_root=tmp_path)
    assert window.eval_end_ms == M22_END_MS
    assert window.allow_holdout is False
    with pytest.raises(ValueError):
        check_window(window, window.eval_start_ms, M22_END_MS + 1)


def test_final需要phase2为PASS(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase1.md").write_text("- 结论：PASS\n", "utf-8")
    (tmp_path / "rotation" / "phase2.md").write_text("- 结论：NOT_READY\n", "utf-8")
    with pytest.raises(ValueError):
        stage_window("final", docs_dir=tmp_path, artifact_root=tmp_path)


def test_final已有完成记录时被拒绝(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase2.md").write_text("- 结论：PASS\n", "utf-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "FINAL_RUNS.log").write_text(
        '{"run_id": "rot_final", "status": "completed"}\n', "utf-8"
    )
    with pytest.raises(ValueError):
        stage_window("final", docs_dir=tmp_path, artifact_root=tmp_path)


def test_final区间为M23到新数据末尾并允许封存集(tmp_path: Path) -> None:
    (tmp_path / "rotation").mkdir()
    (tmp_path / "rotation" / "phase2.md").write_text("- 结论：PASS\n", "utf-8")
    window = stage_window("final", docs_dir=tmp_path, artifact_root=tmp_path)
    assert window.eval_start_ms == FINAL_EVAL_START_MS
    assert window.eval_end_ms == FINAL_EVAL_END_MS
    assert window.allow_holdout is True
    assert window.eval_start_ms == HOLDOUT_START_MS
    with pytest.raises(ValueError):
        check_window(window, window.eval_start_ms, FINAL_EVAL_END_MS + 1)


def test_结论解析() -> None:
    path = Path(__file__).resolve().parents[2] / "README.md"
    assert read_verdict(path) is None  # 没有结论行


def test_read_month_bars越过封存集报错(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        read_month_bars(
            tmp_path,
            "C001",
            "2026-09",
            columns=("open_time", "close"),
            holdout_start_ms=HOLDOUT_START_MS,
            allow_holdout=False,
        )
