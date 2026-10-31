"""回测报告与模型产物读取测试。

规格：docs/06-回测引擎.md §8；docs/10-可视化看板.md §5.5
"""

from __future__ import annotations

from pathlib import Path

from trade.dashboard.reports import (
    list_models,
    list_reports,
    load_model,
    load_report,
)


def test_list_reports标记final与source(report_dir: Path) -> None:
    reports = list_reports(report_dir)
    assert len(reports) == 1
    report = reports[0]
    assert report["run_id"] == "run1"
    assert report["final"] is True
    assert report["source"] == "fixture"
    assert report["reference_base"]["n_trades"] == 1


def test_load_report读取完整内容(report_dir: Path) -> None:
    data = load_report(report_dir, "run1")
    assert data is not None
    assert data["run_id"] == "run1"
    assert data["summary"] is not None
    assert data["gate"] is not None
    assert data["gate"]["source"] == "fixture"
    assert data["report_md"] is not None
    assert len(data["trades"]) == 1
    assert data["trades"][0]["symbol"] == "ALT01USDT"
    assert len(data["equity"]) == 1
    assert len(data["equity"][0]["points"]) == 3


def test_load_report缺失返回None(report_dir: Path) -> None:
    assert load_report(report_dir, "nope") is None


def test_list_reports目录不存在返回空(tmp_path: Path) -> None:
    assert list_reports(tmp_path / "missing") == []


def test_list_models目录不存在返回空(tmp_path: Path) -> None:
    assert list_models(tmp_path / "missing") == []


def test_load_model缺失返回None(tmp_path: Path) -> None:
    assert load_model(tmp_path / "models", "m0") is None
