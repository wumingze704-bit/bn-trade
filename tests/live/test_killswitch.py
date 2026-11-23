"""紧急停机测试（docs/07 §7.3）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from trade.live.killswitch import KillSwitchError, kill_switch
from trade.live.store import Store


class FakeClient:
    def __init__(self, *, close_works: bool = True) -> None:
        self.close_works = close_works
        self.cancelled_orders: list[str] = []
        self.cancelled_algos: list[str] = []
        self.placed: list[dict[str, Any]] = []
        self.closed = False

    async def open_orders(self) -> list[dict[str, Any]]:
        return [{"symbol": "AAAUSDT", "clientOrderId": "trd-1"}]

    async def algo_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [{"symbol": "AAAUSDT", "clientAlgoId": "stp-1"}]

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        self.cancelled_orders.append(orig_client_order_id)
        return {}

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]:
        self.cancelled_algos.append(str(params.get("clientAlgoId")))
        return {}

    async def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self.closed:
            return []
        return [{"symbol": "AAAUSDT", "positionAmt": "2.0"}]

    async def place_order(self, **params: Any) -> dict[str, Any]:
        self.placed.append(dict(params))
        if self.close_works:
            self.closed = True
        return {}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "state.db")
    yield s
    s.close()


def test_影子盘停机只写标志(store: Store) -> None:
    summary = asyncio.run(
        kill_switch(
            client=None,
            store=store,
            candidate="C1",
            mode="shadow",
            step_sizes={},
            reason="manual",
        )
    )
    assert summary["halted"] is True
    assert store.get_engine_halt(candidate="C1", mode="shadow")["halted"] == 1  # type: ignore[index]
    assert store.get_engine_halt(candidate="C1", mode="shadow")["reason"] == "manual"  # type: ignore[index]


def test_停机撤单平仓并确认清零(store: Store) -> None:
    client = FakeClient()
    summary = asyncio.run(
        kill_switch(
            client=client,  # type: ignore[arg-type]
            store=store,
            candidate="C1",
            mode="live",
            step_sizes={"AAAUSDT": 0.1},
        )
    )
    assert summary["cancelled_orders"] == 1
    assert summary["cancelled_algos"] == 1
    assert summary["remaining"] == []
    assert client.placed[0]["side"] == "SELL"
    assert client.placed[0]["reduceOnly"] == "true"
    assert store.get_engine_halt(candidate="C1", mode="live")["halted"] == 1  # type: ignore[index]


def test_平仓后仍有持仓时报警(store: Store) -> None:
    client = FakeClient(close_works=False)
    with pytest.raises(KillSwitchError, match="仍有持仓"):
        asyncio.run(
            kill_switch(
                client=client,  # type: ignore[arg-type]
                store=store,
                candidate="C1",
                mode="live",
                step_sizes={"AAAUSDT": 0.1},
                sleep=_no_sleep,
            )
        )


async def _no_sleep(seconds: float) -> None:
    return None
