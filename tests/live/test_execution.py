"""挂单优先执行器与影子盘成交口径测试（docs/07 §6.4、docs/13 §4）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from trade.live.exchange.client import ExchangeError
from trade.live.exchange.models import BookTicker, OrderResult
from trade.live.execution import (
    ExchangeExecutor,
    ExecutionError,
    ReadOnlyMarketClient,
    ShadowExecutor,
    ShadowModeError,
    SymbolRules,
    research_fill_price,
)
from trade.live.targets import TradeIntent


class FakeClient:
    def __init__(self, *, place_results: list[Any], get_results: list[Any] | None = None) -> None:
        self.place_results = list(place_results)
        self.get_results = list(get_results or [])
        self.placed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.gets: list[str] = []

    async def book_ticker(self, symbol: str | None = None) -> list[BookTicker]:
        return [BookTicker(symbol="AAAUSDT", bidPrice=99.9, askPrice=100.1)]

    async def place_order(self, **params: Any) -> OrderResult:
        self.placed.append(dict(params))
        result = self.place_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def get_order(self, symbol: str, orig_client_order_id: str) -> OrderResult:
        self.gets.append(orig_client_order_id)
        result = self.get_results.pop(0) if self.get_results else OrderResult(status="NEW")
        if isinstance(result, BaseException):
            raise result
        return result

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        self.cancelled.append(orig_client_order_id)
        return {}


def _order(status: str, executed: float = 0.0, price: float = 0.0) -> OrderResult:
    return OrderResult.model_validate(
        {
            "clientOrderId": "x",
            "status": status,
            "executedQty": str(executed),
            "avgPrice": str(price),
        }
    )


def _executor(client: FakeClient) -> ExchangeExecutor:
    async def _sleep(seconds: float) -> None:
        return None

    return ExchangeExecutor(
        client,  # type: ignore[arg-type]
        now_ms=lambda: 1_700_000_000_000,
        sleep=_sleep,
        wait_sec=9,
        tick_buffer=2,
        fee_rate=0.0005,
        poll_sec=3,
    )


_RULES = SymbolRules(tick_size=0.1, step_size=0.1)


def test_post_only一次成交不再吃单() -> None:
    client = FakeClient(place_results=[_order("FILLED", 1.0, 99.9)])
    report = asyncio.run(
        _executor(client).execute(
            TradeIntent("AAAUSDT", 1.0, False),
            rules=_RULES,
            alias="AAA",
            minute="2026010100",
            seq=0,
        )
    )
    assert report.filled_qty == pytest.approx(1.0)
    assert report.maker_qty == pytest.approx(1.0)
    assert len(client.placed) == 1
    assert client.placed[0]["type"] == "LIMIT"
    assert client.placed[0]["timeInForce"] == "GTX"
    assert client.placed[0]["price"] == "99.9"


def test_部分成交后改挂并使用IOC补单() -> None:
    client = FakeClient(
        place_results=[_order("NEW"), _order("NEW"), _order("FILLED", 1.0, 100.1)],
        get_results=[
            _order("PARTIALLY_FILLED", 1.0, 99.9),
            _order("PARTIALLY_FILLED", 1.0, 99.9),
            _order("PARTIALLY_FILLED", 1.0, 99.9),
            _order("NEW"),
            _order("NEW"),
            _order("NEW"),
        ],
    )
    report = asyncio.run(
        _executor(client).execute(
            TradeIntent("AAAUSDT", 2.0, False),
            rules=_RULES,
            alias="AAA",
            minute="2026010100",
            seq=1,
        )
    )
    assert report.filled_qty == pytest.approx(2.0)
    assert report.maker_qty == pytest.approx(1.0)
    assert [item["timeInForce"] for item in client.placed] == ["GTX", "GTX", "IOC"]
    assert client.placed[2]["price"] == "100.3"
    assert len(client.cancelled) == 2


def test_post_only被拒视为未成交() -> None:
    error = ExchangeError(code=-5022, msg="post only rejected", http_status=400)
    client = FakeClient(place_results=[error, error, _order("FILLED", 1.0, 100.3)])
    report = asyncio.run(
        _executor(client).execute(
            TradeIntent("AAAUSDT", 1.0, False),
            rules=_RULES,
            alias="AAA",
            minute="2026010100",
            seq=2,
        )
    )
    assert report.filled_qty == pytest.approx(1.0)
    assert [item["type"] for item in client.placed] == ["LIMIT", "LIMIT", "LIMIT"]
    assert client.placed[-1]["timeInForce"] == "IOC"


def test_订单状态未知先查询() -> None:
    client = FakeClient(
        place_results=[TimeoutError("timeout")],
        get_results=[_order("FILLED", 1.0, 99.9)],
    )
    report = asyncio.run(
        _executor(client).execute(
            TradeIntent("AAAUSDT", 1.0, False),
            rules=_RULES,
            alias="AAA",
            minute="2026010100",
            seq=3,
        )
    )
    assert report.filled_qty == pytest.approx(1.0)


def test_订单状态未知且查询不存在时报错() -> None:
    client = FakeClient(
        place_results=[TimeoutError("timeout")],
        get_results=[ExchangeError(code=-2013, msg="not found", http_status=400)],
    )
    with pytest.raises(ExecutionError, match="订单不存在"):
        asyncio.run(
            _executor(client).execute(
                TradeIntent("AAAUSDT", 1.0, False),
                rules=_RULES,
                alias="AAA",
                minute="2026010100",
                seq=4,
            )
        )


def test_影子盘成交价与研究口径一致() -> None:
    fill = ShadowExecutor(fee_rate=0.0005).fill(
        TradeIntent("AAAUSDT", 1.0, False),
        open_price=100.0,
        half_spread=0.0002,
        slip=0.0003,
        ts_ms=1,
        client_id="sdw-AAA-1",
    )
    expected = research_fill_price(open_price=100.0, side=1, half_spread=0.0002, slip=0.0003)
    assert fill.price == pytest.approx(expected)
    assert fill.fee == pytest.approx(fill.price * 0.0005)


def test_只读客户端拒绝所有交易调用() -> None:
    guard = ReadOnlyMarketClient(object())  # type: ignore[arg-type]
    with pytest.raises(ShadowModeError):
        asyncio.run(guard.place_order(symbol="AAAUSDT"))
    with pytest.raises(ShadowModeError):
        asyncio.run(guard.cancel_order("AAAUSDT", "x"))
    with pytest.raises(ShadowModeError):
        asyncio.run(guard.set_leverage("AAAUSDT", 5))
