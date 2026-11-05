"""总览与研究接口测试（含 fixture state.db）。

规格：docs/10-可视化看板.md §5.1、§5.5、§6、§7
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trade.dashboard.app import create_app
from trade.dashboard.config import (
    ControlConfig,
    DashboardConfig,
    DashboardPathsConfig,
    DisplayConfig,
)

_ROOT = Path(__file__).resolve().parents[2]


def _config(
    tmp_path: Path,
    *,
    state_db: Path,
    reports_dir: Path,
    data_dir: Path | None = None,
) -> DashboardConfig:
    return DashboardConfig(
        paths=DashboardPathsConfig(
            state_db=state_db,
            live_dir=tmp_path / "live",
            reports_dir=reports_dir,
            models_dir=tmp_path / "models",
            data_dir=data_dir,
            quality_cache=(tmp_path / "quality" / "quality_cache.parquet") if data_dir else None,
            universe_config=_ROOT / "configs" / "universe.yaml",
            universe_report=tmp_path / "universe_report.md",
            sizing_config=_ROOT / "configs" / "sizing.yaml",
            exits_config=_ROOT / "configs" / "exits.yaml",
        ),
        display=DisplayConfig(tz="Asia/Shanghai"),
        control=ControlConfig(enabled=True, pin_env="DASHBOARD_CONTROL_PIN"),
    )


def test_overview指标与持仓(tmp_path: Path, state_db: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, state_db=state_db, reports_dir=report_dir)))
    body = client.get("/api/overview").json()
    assert body["live_available"] is True
    kpis = body["kpis"]
    assert kpis["equity"]["value"] == 10.35
    assert kpis["today_pnl"]["value"] == 0.23
    assert kpis["today_pnl"]["daily_loss_stop"] == 0.05
    assert kpis["positions"]["count"] == 1
    assert kpis["positions"]["max_positions"] == 3
    assert kpis["positions"]["margin_used"] == 4.0
    assert kpis["today_trades"]["count"] == 2
    assert kpis["today_trades"]["wins"] == 1
    assert kpis["today_trades"]["profit_factor"] == pytest.approx(0.35 / 0.12)

    assert len(body["positions"]) == 1
    position = body["positions"][0]
    assert position["stop_kind"] == "trail"
    assert position["h_max"] == 120
    assert 0.0 <= position["progress"] <= 1.0

    assert [order["valid"] for order in body["orders"]] == [True, False]
    assert body["events"][0]["level"] == "error"
    assert "time_local" in body["events"][0]


def test_scan接口顺序与统计(tmp_path: Path, state_db: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, state_db=state_db, reports_dir=report_dir)))
    body = client.get("/api/scan").json()
    assert body["cycle"]["n_symbols"] == 3
    assert [state["seq"] for state in body["states"]] == [1, 2, 3]


def test_equity接口范围校验(tmp_path: Path, state_db: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, state_db=state_db, reports_dir=report_dir)))
    assert client.get("/api/equity?range=1D").json()["points"]
    assert client.get("/api/equity?range=bad").status_code == 422


def test_研究接口_交易范围与质量(
    tmp_path: Path, state_db: Path, report_dir: Path, research_data: Path
) -> None:
    config = _config(tmp_path, state_db=state_db, reports_dir=report_dir, data_dir=research_data)
    client = TestClient(create_app(config))

    universe = client.get("/api/universe").json()
    assert universe["months"] == ["2025-09", "2025-10"]
    assert universe["selected"] == "2025-10"
    snapshot = universe["snapshot"]
    assert snapshot["month"] == "2025-10"
    assert len(snapshot["coins"]) == 2
    assert snapshot["role_counts"] == {"train": 2}
    assert universe["rule"]["reviewed"] is True
    change = universe["changes"][-1]
    assert change["entered"] == ["CCCUSDT"]
    assert change["exited"] == ["BBBUSDT"]
    assert "fixture" in universe["report_md"]

    quality = client.get("/api/quality").json()["quality"]
    assert quality["summary"]["n_rows"] == 2
    assert quality["summary"]["n_qualified"] == 1
    assert quality["rows"][1]["qualified"] is False

    datasets = client.get("/api/datasets").json()["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["run_id"] == "ds_fixture"
    assert datasets[0]["n_samples"] == 150
    assert datasets[0]["label_mode"] == "exit_sim"


def test_无状态库时实盘接口为空(tmp_path: Path, report_dir: Path) -> None:
    config = _config(tmp_path, state_db=tmp_path / "missing" / "state.db", reports_dir=report_dir)
    client = TestClient(create_app(config))
    overview = client.get("/api/overview").json()
    assert overview["live_available"] is False
    assert overview["kpis"] is None
    assert overview["positions"] == []
    assert client.get("/api/scan").json()["states"] == []
    assert client.get("/api/positions").json() == {"live_available": False}
