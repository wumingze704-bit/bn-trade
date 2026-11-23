"""低频引擎看板接口与控制 PIN 测试（docs/10 §5、§8）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from trade.dashboard.app import create_app
from trade.dashboard.config import (
    ControlConfig,
    DashboardConfig,
    DashboardPathsConfig,
    DisplayConfig,
)
from trade.live.store import Store


def _config(tmp_path: Path, *, ledger: Path, live_config: Path) -> DashboardConfig:
    return DashboardConfig(
        paths=DashboardPathsConfig(
            state_db=tmp_path / "live" / "state.db",
            live_dir=tmp_path / "live",
            reports_dir=tmp_path / "reports",
            models_dir=tmp_path / "models",
            ledger=ledger,
            research_dir=ledger.parent,
            live_config=live_config,
        ),
        display=DisplayConfig(tz="Asia/Shanghai"),
        control=ControlConfig(enabled=True, pin_env="DASHBOARD_CONTROL_PIN"),
    )


def _seed(tmp_path: Path) -> tuple[Path, Path, Path]:
    db_path = tmp_path / "live" / "state.db"
    db_path.parent.mkdir(parents=True)
    store = Store(db_path)
    try:
        now = 1_700_000_000_000
        store.upsert_engine_status(
            candidate="C1",
            mode="shadow",
            pid=123,
            started_ms=now,
            heartbeat_ms=now,
            last_rebalance_ms=now,
            next_rebalance_ms=now + 3_600_000,
            version="p5-1",
        )
        store.insert_rebalance_log(
            candidate="C1",
            mode="shadow",
            open_time=now,
            finished_ms=now + 1000,
            equity=100.0,
            long_notional=20.0,
            short_notional=20.0,
            cost_bp=1.2,
            est_cost_bp=5.0,
            n_targets=2,
            n_orders=3,
            detail={"targets": {"AAAUSDT": 1.0}},
        )
        store.insert_consistency(
            candidate="C1",
            kind="shadow_vs_replay",
            ts_ms=now,
            agree_ratio=0.98,
            detail={"n": 2},
        )
        store.set_control_flags(candidate="C1", pause_open=False, flatten_requested=False)
        store.insert_equity_snapshot(
            ts_ms=now, wallet_balance=100.0, unrealized_pnl=0.0, available_margin=80.0
        )
        store.commit()
    finally:
        store.close()
    ledger = tmp_path / "research" / "ledger.yaml"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        yaml.safe_dump(
            {
                "trials_used": 65,
                "global_trial_budget": 200,
                "qualified_pool": [],
                "rounds": [
                    {"id": "R1", "family": "xs_momentum_ls", "status": "failed", "trials": 12}
                ],
                "candidates": [{"id": "C1", "final": "pending", "ops": "shadow"}],
                "final_windows": [
                    {
                        "id": "W1",
                        "start": "2026-08-01",
                        "end": "2026-11-30",
                        "available_after": "2026-12-03",
                        "status": "unused",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    live_config = tmp_path / "live.yaml"
    live_config.write_text(
        yaml.safe_dump({"engine": {"hide_pnl_until_final": True}}), encoding="utf-8"
    )
    return db_path, ledger, live_config


def test_引擎与调仓一致性接口(tmp_path: Path) -> None:
    _db, ledger, live_config = _seed(tmp_path)
    client = TestClient(create_app(_config(tmp_path, ledger=ledger, live_config=live_config)))

    engine = client.get("/api/engine").json()
    assert engine["engines"][0]["candidate"] == "C1"
    assert engine["ledger"]["rounds"][0]["id"] == "R1"
    assert engine["ledger"]["final_windows"][0]["available_after"] == "2026-12-03"

    rows = client.get("/api/rebalances?candidate=C1").json()["rows"]
    assert rows[0]["n_targets"] == 2
    assert rows[0]["detail"]["targets"] == {"AAAUSDT": 1.0}
    assert len(client.get("/api/consistency?candidate=C1").json()["rows"]) == 1

    state = client.get("/api/control?candidate=C1").json()
    assert state["control"]["pause_open"] is False


def test_盈亏遮蔽时总览不显示权益(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, ledger, live_config = _seed(tmp_path)
    monkeypatch.setenv("DASHBOARD_CONTROL_PIN", "1234")
    client = TestClient(create_app(_config(tmp_path, ledger=ledger, live_config=live_config)))
    body = client.get("/api/overview?candidate=C1").json()
    assert body["pnl_hidden"] is True
    assert body["kpis"]["equity"]["value"] is None
    assert body["kpis"]["today_pnl"]["value"] is None
    assert client.get("/api/equity?range=1D&candidate=C1").json()["points"] == []


def test_控制操作需要正确PIN(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, ledger, live_config = _seed(tmp_path)
    monkeypatch.setenv("DASHBOARD_CONTROL_PIN", "1234")
    client = TestClient(create_app(_config(tmp_path, ledger=ledger, live_config=live_config)))
    payload = {"candidate": "C1", "action": "pause_open", "pin": "bad"}
    headers = {"Host": "localhost:8501"}
    assert client.post("/api/control", json=payload, headers=headers).status_code == 403
    payload["pin"] = "1234"
    response = client.post("/api/control", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["control"]["pause_open"] is True

    bad_value = {"candidate": "C1", "action": "risk_per_trade", "pin": "1234", "value": 0.05}
    assert client.post("/api/control", json=bad_value, headers=headers).status_code == 422


def test_调仓明细可序列化(tmp_path: Path) -> None:
    _db, ledger, live_config = _seed(tmp_path)
    client = TestClient(create_app(_config(tmp_path, ledger=ledger, live_config=live_config)))
    rows = client.get("/api/rebalances?candidate=C1").json()["rows"]
    assert json.dumps(rows)
