"""行情管理与再平衡纯逻辑单测。

规格：docs/07-实盘与风控.md §3.1、§3.5
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest

from trade.data.universe import SnapshotCoin, UniverseSnapshot
from trade.live.exchange.endpoints import endpoints_for
from trade.live.market_data import (
    KlineGapFiller,
    agg_trade_streams,
    book_ticker_streams,
    exited_symbols,
    kline_streams,
    missing_minutes,
    new_entrants,
    reconnect_delay_ms,
    should_renew_listen_key,
    should_rotate_connection,
    subscription_symbols,
    top_gainers,
    trading_scope,
)


def _run(coro: object) -> object:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]


def _snapshot(coins: set[str]) -> UniverseSnapshot:
    return UniverseSnapshot(
        month="2026-11",
        rebalance_ms=0,
        rule_hash="x",
        coins=[
            SnapshotCoin(
                symbol=s, alias="", role="train", rank=1, daily_quote_median=1.0, first_listing_ms=0
            )
            for s in coins
        ],
    )


def test_subscription_symbols_include_btc_and_positions() -> None:
    kline, book = subscription_symbols(
        trading_symbols={"SOLUSDT", "SUIUSDT"},
        open_position_symbols={"OLDUSDT"},
        btc_context="BTCUSDT",
    )
    assert "BTCUSDT" in kline
    assert "OLDUSDT" in kline
    assert "BTCUSDT" not in book  # bookTicker 不含 BTC
    assert "SOLUSDT" in book


def test_should_rotate_connection_after_23h() -> None:
    assert should_rotate_connection(connected_ms=0, now_ms=23 * 3600 * 1000) is True
    assert should_rotate_connection(connected_ms=0, now_ms=22 * 3600 * 1000) is False


def test_should_renew_listen_key_after_30min() -> None:
    assert should_renew_listen_key(last_renew_ms=0, now_ms=30 * 60 * 1000) is True
    assert should_renew_listen_key(last_renew_ms=0, now_ms=29 * 60 * 1000) is False


def test_new_entrants_and_exited() -> None:
    prev = _snapshot({"SOLUSDT", "SUIUSDT"})
    curr = _snapshot({"SOLUSDT", "NEWUSDT"})
    assert new_entrants(prev, curr) == {"NEWUSDT"}
    assert exited_symbols(prev, curr) == {"SUIUSDT"}


def test_top_gainers_filters_and_ranks() -> None:
    tickers = [
        {"symbol": "BTCUSDT", "price_change_percent": 2.0},
        {"symbol": "SOLUSDT", "price_change_percent": 5.0},
        {"symbol": "SUIUSDT", "price_change_percent": 3.0},
        {"symbol": "ETHUSDC", "price_change_percent": 9.0},  # 非 USDT 计价，应被过滤
    ]
    assert top_gainers(tickers, size=2) == ["SOLUSDT", "SUIUSDT"]


def test_trading_scope_is_union() -> None:
    assert trading_scope({"SOLUSDT", "SUIUSDT"}, {"SUIUSDT", "NEWUSDT"}) == {
        "SOLUSDT",
        "SUIUSDT",
        "NEWUSDT",
    }


def test_stream_names_use_lowercase_and_sorted() -> None:
    assert kline_streams({"SOLUSDT", "BTCUSDT"}) == ["btcusdt@kline_1m", "solusdt@kline_1m"]
    assert agg_trade_streams({"SOLUSDT"}) == ["solusdt@aggTrade"]
    assert book_ticker_streams({"SOLUSDT"}) == ["solusdt@bookTicker"]


def test_reconnect_delay_backoff_and_cap() -> None:
    assert reconnect_delay_ms(0) == 0
    assert reconnect_delay_ms(1) == 1_000
    assert reconnect_delay_ms(2) == 2_000
    assert reconnect_delay_ms(3) == 4_000
    assert reconnect_delay_ms(10) == 30_000  # 上限 30 秒


def test_missing_minutes_covers_closed_bars_only() -> None:
    minute = 60_000
    # 断线前最后一根为第 10 分钟，当前 13:30 → 缺第 11、12 分钟（第 13 分钟尚未收盘）
    assert missing_minutes(last_open_time_ms=0, now_ms=4 * minute + 30_000) == []
    assert missing_minutes(last_open_time_ms=10 * minute, now_ms=13 * minute + 30_000) == [
        11 * minute,
        12 * minute,
    ]


def test_missing_minutes_ignores_current_unclosed_bar() -> None:
    minute = 60_000
    # 当前 14:59，第 14 分钟尚未收盘，只补到第 13 分钟
    assert missing_minutes(last_open_time_ms=10 * minute, now_ms=14 * minute + 59_000) == [
        11 * minute,
        12 * minute,
        13 * minute,
    ]


class _FakeKlineClient:
    """记录请求参数、返回固定 K 线的假客户端（用于缺口回补单测）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[object]]:
        self.calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        assert start_time is not None
        return [
            [start_time, "1.0", "1.2", "0.9", "1.1", "10.0", 0, "11.0", 3, "5.0", "5.5", "0"],
            [
                start_time + 60_000,
                "1.1",
                "1.3",
                "1.0",
                "1.2",
                "20.0",
                0,
                "24.0",
                4,
                "9.0",
                "9.6",
                "0",
            ],
        ]


def test_kline_gap_filler_requests_rest_range_and_parses() -> None:
    minute = 60_000
    client = _FakeKlineClient()
    filler = KlineGapFiller(client)  # type: ignore[arg-type]
    bars = _run(filler.fill("SOLUSDT", last_open_time_ms=10 * minute, now_ms=13 * minute + 30_000))
    assert client.calls[0]["symbol"] == "SOLUSDT"
    assert client.calls[0]["start_time"] == 11 * minute
    assert client.calls[0]["end_time"] == 12 * minute + minute - 1
    assert [bar["open_time_ms"] for bar in bars] == [11 * minute, 12 * minute]
    assert bars[0]["quote_volume"] == 11.0


class _FakeWs:
    """假的 WsConnection：按脚本产出消息，可配置前 N 次连接失败（验证重连不逃出循环）。"""

    behaviors: list[list[dict[str, object]]] = []
    fail_connects = 0
    instances: list[_FakeWs] = []

    def __init__(self, url: str) -> None:
        self.url = url
        self.index = len(_FakeWs.instances)
        _FakeWs.instances.append(self)
        self.closed = False

    async def connect(self) -> None:
        if self.index < _FakeWs.fail_connects:
            raise OSError("模拟握手失败")

    async def close(self) -> None:
        self.closed = True

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for message in _FakeWs.behaviors[self.index]:
            yield message
        raise ConnectionError("模拟对端关闭")


def test_feed_reconnects_after_handshake_failure_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """握手失败必须继续退避重试，不能逃出 run()（T5-4 断网演练暴露的缺陷）。"""
    market_data = sys.modules["trade.live.market_data"]
    monkeypatch.setattr(market_data, "WsConnection", _FakeWs)
    monkeypatch.setattr(market_data, "reconnect_delay_ms", lambda attempt, **kwargs: 0)
    _FakeWs.instances = []
    _FakeWs.fail_connects = 1
    _FakeWs.behaviors = [
        [],  # 第 1 个实例握手失败，不使用
        [{"e": "kline", "E": 1_000, "k": {"t": 0, "x": True}}],
        [{"e": "kline", "E": 2_000, "k": {"t": 60_000, "x": True}}],
    ]
    ticks = iter(range(1, 100))

    def clock() -> int:
        return next(ticks) * 1_000

    reconnects: list[tuple[int, int]] = []
    feed = market_data.MarketDataFeed(
        endpoints=endpoints_for("testnet"),
        streams=["btcusdt@kline_1m"],
        on_message=lambda message: None,
        on_reconnect=lambda last, now: reconnects.append((last, now)),
        clock=clock,
    )
    stats = _run(feed.run(max_runtime_ms=5_000))
    assert stats.reconnects == 1  # type: ignore[attr-defined]
    assert stats.messages == 2  # type: ignore[attr-defined]
    assert reconnects and reconnects[0][0] == 1_000


def test_feed按路由分组成多条连接() -> None:
    """kline + aggTrade 落在 /market，bookTicker 落在 /public（09 §2、§3.3）。"""
    market_data = sys.modules["trade.live.market_data"]
    feed = market_data.MarketDataFeed(
        endpoints=endpoints_for("mainnet"),
        streams=["btcusdt@kline_1m", "btcusdt@aggTrade", "btcusdt@bookTicker"],
        on_message=lambda message: None,
    )
    assert feed.urls == [
        "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker",
        "wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m/btcusdt@aggTrade",
    ]
    assert feed.url == feed.urls[0]


def test_feed拒绝用户数据流放入组合流() -> None:
    market_data = sys.modules["trade.live.market_data"]
    with pytest.raises(ValueError, match="listenKey"):
        market_data.MarketDataFeed(
            endpoints=endpoints_for("mainnet"),
            streams=["userData"],
            on_message=lambda message: None,
        )
