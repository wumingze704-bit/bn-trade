"""止损单管理测试（docs/07 §6.5）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from trade.live.stops import StopError, StopManager, stop_client_id, stop_trigger_price


class FakeStopClient:
    def __init__(self, *, confirm: str = "NEW", fail_cancel: bool = False) -> None:
        self.confirm = confirm
        self.fail_cancel = fail_cancel
        self.placed: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.open: list[dict[str, Any]] = []

    async def place_algo_order(self, **params: Any) -> dict[str, Any]:
        self.placed.append(dict(params))
        return {"algoStatus": "NEW"}

    async def get_algo_order(self, symbol: str, client_algo_id: str) -> dict[str, Any]:
        return {"algoStatus": self.confirm}

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]:
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        self.cancelled.append(dict(params))
        return {}

    async def algo_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return list(self.open)


def test_止损触发价按方向取整() -> None:
    assert stop_trigger_price(side=1, sl=0.05, reference_price=100.0, tick_size=0.1) == 95.0
    assert stop_trigger_price(side=-1, sl=0.05, reference_price=100.0, tick_size=0.1) == 105.0
    assert stop_trigger_price(side=1, sl=0.05, reference_price=100.0, tick_size=0.3) == 94.8
    assert stop_trigger_price(side=-1, sl=0.05, reference_price=100.0, tick_size=0.3) == 105.0


def test_止损ID长度与字符集() -> None:
    client_id = stop_client_id("sol", "2026010100", 1)
    assert client_id.startswith("stp-SOL-")
    assert len(client_id) <= 36


def test_先挂新止损确认后再撤旧止损() -> None:
    client = FakeStopClient()
    manager = StopManager(client)  # type: ignore[arg-type]
    new_id = asyncio.run(
        manager.ensure(
            symbol="AAAUSDT",
            side=1,
            qty=2.0,
            trigger_price=95.0,
            alias="AAA",
            minute="2026010100",
            seq=1,
            old_client_algo_id="stp-AAA-old",
        )
    )
    assert client.placed[0]["side"] == "SELL"
    assert client.placed[0]["type"] == "STOP_MARKET"
    assert client.placed[0]["triggerPrice"] == "95"
    assert client.placed[0]["reduceOnly"] == "true"
    assert client.cancelled[0]["clientAlgoId"] == "stp-AAA-old"
    assert new_id != "stp-AAA-old"


def test_止损无法确认时抛出且不撤旧单() -> None:
    client = FakeStopClient(confirm="CANCELED")
    manager = StopManager(client)  # type: ignore[arg-type]
    with pytest.raises(StopError):
        asyncio.run(
            manager.ensure(
                symbol="AAAUSDT",
                side=-1,
                qty=1.0,
                trigger_price=105.0,
                alias="AAA",
                minute="2026010100",
                seq=2,
                old_client_algo_id="stp-AAA-old",
            )
        )
    assert client.cancelled == []


def test_撤销某币全部条件单() -> None:
    client = FakeStopClient()
    client.open = [{"clientAlgoId": "a"}, {"clientAlgoId": "b"}]
    manager = StopManager(client)  # type: ignore[arg-type]
    assert asyncio.run(manager.cancel_symbol("AAAUSDT")) == 2
    assert len(client.cancelled) == 2
