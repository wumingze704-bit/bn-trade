"""币安 USDⓈ-M 合约 WebSocket 客户端与消息解析。

规格：docs/09-币安数据与API.md §3.3；docs/07-实盘与风控.md §3
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# 流消息统一字段：e=事件类型、E=事件时间（毫秒）、s=交易对
_STREAM_HEADER = ("e", "E", "s")


@dataclass(frozen=True)
class KlineEvent:
    """1m K 线流事件（09 §3.3）。"""

    symbol: str
    event_time_ms: int
    open_time_ms: int
    close: float
    high: float
    low: float
    volume: float
    quote_volume: float
    is_closed: bool


@dataclass(frozen=True)
class AggTradeEvent:
    """归集逐笔流事件（09 §3.3）。"""

    symbol: str
    event_time_ms: int
    agg_trade_id: int
    price: float
    qty: float
    transact_time_ms: int
    is_buyer_maker: bool


@dataclass(frozen=True)
class BookTickerEvent:
    """最优买卖价流事件（09 §3.3）。"""

    symbol: str
    event_time_ms: int
    best_bid: float
    best_ask: float


def parse_ws_message(raw: str | bytes) -> dict[str, Any]:
    """解析一条 WS 消息为字典；非 JSON 时返回空字典（09 §3.3）。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _header_fields(data: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(data.get("s", "")),
        int(data.get("E", 0)),
        str(data.get("e", "")),
    )


def parse_kline(data: dict[str, Any]) -> KlineEvent:
    """解析 `kline_1m` 流消息（09 §3.3、07 §3.2）。

    返回的 `is_closed` 为 k.x（该根 K 线是否已收盘）。
    """
    symbol, event_ms, _ = _header_fields(data)
    kline = data.get("k", {})
    kline = kline if isinstance(kline, dict) else {}
    return KlineEvent(
        symbol=symbol,
        event_time_ms=event_ms,
        open_time_ms=int(kline.get("t", 0)),
        close=float(kline.get("c", 0.0)),
        high=float(kline.get("h", 0.0)),
        low=float(kline.get("l", 0.0)),
        volume=float(kline.get("v", 0.0)),
        quote_volume=float(kline.get("q", 0.0)),
        is_closed=bool(kline.get("x", False)),
    )


def parse_agg_trade(data: dict[str, Any]) -> AggTradeEvent:
    """解析 `aggTrade` 流消息（09 §3.3、07 §3.2）。"""
    symbol, event_ms, _ = _header_fields(data)
    return AggTradeEvent(
        symbol=symbol,
        event_time_ms=event_ms,
        agg_trade_id=int(data.get("a", 0)),
        price=float(data.get("p", 0.0)),
        qty=float(data.get("q", 0.0)),
        transact_time_ms=int(data.get("T", 0)),
        is_buyer_maker=bool(data.get("m", False)),
    )


def parse_book_ticker(data: dict[str, Any]) -> BookTickerEvent:
    """解析 `bookTicker` 流消息（09 §3.3）。"""
    symbol, event_ms, _ = _header_fields(data)
    return BookTickerEvent(
        symbol=symbol,
        event_time_ms=event_ms,
        best_bid=float(data.get("b", 0.0)),
        best_ask=float(data.get("a", 0.0)),
    )


class WsConnection:
    """单条 WebSocket 连接封装（09 §3.3、07 §3.1）。

    负责建立连接、发送订阅、迭代 JSON 消息与关闭。23 小时轮换与 listenKey
    续期由上层 `trade.live.market_data` 负责（07 §3.1）。
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None

    async def connect(self) -> None:
        """建立连接。"""
        from websockets.asyncio.client import connect

        self._ws = await connect(self._url)

    async def send(self, payload: dict[str, Any]) -> None:
        """发送 JSON 载荷（如 SUBSCRIBE）。"""
        if self._ws is None:
            raise RuntimeError("连接尚未建立")
        await self._ws.send(json.dumps(payload))

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise RuntimeError("连接尚未建立")
        async for raw in self._ws:
            yield parse_ws_message(raw)

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
