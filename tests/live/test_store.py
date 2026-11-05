"""状态库读写单测。

规格：docs/07-实盘与风控.md §6.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade.live.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "state.db")
    yield s
    s.close()


def test_upsert_and_get_order(store: Store) -> None:
    store.upsert_order(
        client_id="trd-SOL-x-E",
        symbol="SOLUSDT",
        side="BUY",
        order_type="LIMIT",
        price=99.0,
        qty=1.0,
        status="NEW",
    )
    store.commit()
    order = store.get_order("trd-SOL-x-E")
    assert order is not None
    assert order["symbol"] == "SOLUSDT"


def test_position_roundtrip(store: Store) -> None:
    store.upsert_position(
        {
            "position_id": "p1",
            "symbol": "SOLUSDT",
            "alias": "SOL",
            "signal_t": 1000,
            "p": 0.6,
            "m": 0.01,
            "entry_price": 100.0,
            "qty": 1.0,
            "margin": 20.0,
            "leverage": 5,
            "sl": 0.02,
            "atr_abs": 2.0,
            "stop_price": 95.0,
            "cap_price": 110.0,
            "hh": 100.0,
            "stop_updates": 0,
            "exit_state_json": None,
            "t_exit_deadline": 2000,
            "status": "OPEN",
            "exit_type": None,
            "exit_price": None,
            "fee": None,
            "funding": None,
            "pnl": None,
            "position_frac": 0.2,
        }
    )
    store.commit()
    assert store.get_position("p1") is not None
    assert len(store.open_positions()) == 1


def test_overrides_and_reset(store: Store) -> None:
    store.set_override(key="position_frac", value="0.3", source="cli")
    assert store.get_overrides() == {"position_frac": "0.3"}
    store.reset_overrides()
    assert store.get_overrides() == {}


def test_scan_write_transaction(store: Store) -> None:
    store.write_scan(
        cycle={
            "open_time": 1000,
            "started_ms": 1000,
            "finished_ms": 1100,
            "n_symbols": 1,
            "n_prefilter": 1,
            "n_signal": 0,
            "n_opened": 0,
        },
        states=[
            {
                "symbol": "SOLUSDT",
                "alias": "SOL",
                "open_time": 1000,
                "seq": 1,
                "status": "skip",
                "prefilter": 0,
                "p": None,
                "m": None,
                "decision": None,
                "skip_reason": "prefilter",
                "spread_bp": 1.0,
                "processed_ms": 5.0,
            }
        ],
    )
    store.commit()
