"""紧急停机测试（docs/07 §7.3）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from trade.live.killswitch import KillSwitchError, kill_switch
from trade.live.store import Store


class FakeClient:
    """模拟交易所：记录调用顺序，并在每次平仓时记录止损单是否仍在（红线 7）。"""

    def __init__(self, *, close_works: bool = True) -> None:
        self.close_works = close_works
        self.cancelled_orders: list[str] = []
        self.cancelled_algos: list[str] = []
        self.placed: list[dict[str, Any]] = []
        self.stops_at_close: list[int] = []
        self.sequence: list[str] = []
        self.closed = False

    async def open_orders(self) -> list[dict[str, Any]]:
        self.sequence.append("open_orders")
        return [{"symbol": "AAAUSDT", "clientOrderId": "trd-1"}]

    async def algo_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self.sequence.append("algo_open_orders")
        return [{"symbol": "AAAUSDT", "clientAlgoId": "stp-1"}]

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        self.sequence.append("cancel_order")
        self.cancelled_orders.append(orig_client_order_id)
        return {}

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]:
        self.sequence.append("cancel_algo_order")
        self.cancelled_algos.append(str(params.get("clientAlgoId")))
        return {}

    async def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self.closed:
            return []
        return [{"symbol": "AAAUSDT", "positionAmt": "2.0"}]

    async def place_order(self, **params: Any) -> dict[str, Any]:
        self.sequence.append("place_order")
        self.stops_at_close.append(1 - len(self.cancelled_algos))
        self.placed.append(dict(params))
        if self.close_works:
            self.closed = True
        return {}


async def _stop_ok() -> bool:
    """实例已确认停止（测试注入）。"""
    return True


async def _stop_failed() -> bool:
    """实例无法确认停止（测试注入）。"""
    return False


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
            stop_runner=_stop_ok,
        )
    )
    assert summary["cancelled_orders"] == 1
    assert summary["cancelled_algos"] == 1
    assert summary["remaining"] == []
    assert client.placed[0]["side"] == "SELL"
    assert client.placed[0]["reduceOnly"] == "true"
    assert store.get_engine_halt(candidate="C1", mode="live")["halted"] == 1  # type: ignore[index]


def test_先停实例再动账户且平仓完成前止损单仍在(store: Store) -> None:
    """3 号第五部分第 5 条：停实例 → 撤普通挂单 → 平仓 → 确认清零 → 撤止损。"""
    client = FakeClient()
    order: list[str] = []

    async def stop_runner() -> bool:
        order.append("stop")
        assert store.get_engine_halt(candidate="C1", mode="live")["halted"] == 1  # type: ignore[index]
        return True

    summary = asyncio.run(
        kill_switch(
            client=client,  # type: ignore[arg-type]
            store=store,
            candidate="C1",
            mode="live",
            step_sizes={"AAAUSDT": 0.1},
            stop_runner=stop_runner,
        )
    )
    # 停实例发生在任何撤单 / 平仓之前
    assert client.sequence[0] == "open_orders"
    assert order == ["stop"]
    # 平仓那一刻止损单仍然存在（没有提前撤条件单）
    assert client.stops_at_close == [1]
    assert client.sequence.index("place_order") < client.sequence.index("cancel_algo_order")
    assert summary["remaining"] == []


def test_实例未确认停止时不动账户(store: Store) -> None:
    """3 号第五部分第 5 条：systemctl stop 未确认成功不得进入撤单与平仓。"""
    client = FakeClient()
    with pytest.raises(KillSwitchError, match="未确认停止"):
        asyncio.run(
            kill_switch(
                client=client,  # type: ignore[arg-type]
                store=store,
                candidate="C1",
                mode="live",
                step_sizes={"AAAUSDT": 0.1},
                stop_runner=_stop_failed,
            )
        )
    assert client.cancelled_orders == []
    assert client.placed == []
    assert store.get_engine_halt(candidate="C1", mode="live")["halted"] == 1  # type: ignore[index]


def test_停机后删除心跳文件(store: Store, tmp_path: Path) -> None:
    """3 号第五部分第 5 条⑥：停机完成后删除心跳文件。"""
    heartbeat = tmp_path / "C1-live.heartbeat"
    heartbeat.write_text("1", encoding="utf-8")
    summary = asyncio.run(
        kill_switch(
            client=FakeClient(),  # type: ignore[arg-type]
            store=store,
            candidate="C1",
            mode="live",
            step_sizes={"AAAUSDT": 0.1},
            stop_runner=_stop_ok,
            heartbeat_path=heartbeat,
        )
    )
    assert summary["stopped"] is True
    assert not heartbeat.exists()


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
                stop_runner=_stop_ok,
                sleep=_no_sleep,
            )
        )
    # 3 号第五部分第 5 条：仍有持仓时保留止损单
    assert client.cancelled_algos == []


async def _no_sleep(seconds: float) -> None:
    return None
