"""WebSocket 路由与端点测试。

规格：docs/09-币安数据与API.md §2、§3.3
"""

from __future__ import annotations

import pytest

from trade.live.exchange.endpoints import Endpoints, endpoints_for, stream_route
from trade.live.market_data import group_streams_by_route, split_stream_batches


def test_stream_route映射正确() -> None:
    assert stream_route("btcusdt@aggTrade") == "market"
    assert stream_route("btcusdt@kline_1m") == "market"
    assert stream_route("btcusdt@markPrice@1s") == "market"
    assert stream_route("btcusdt@miniTicker") == "market"
    assert stream_route("btcusdt@ticker") == "market"
    assert stream_route("btcusdt@bookTicker") == "public"
    assert stream_route("btcusdt@depth5@100ms") == "public"
    assert stream_route("userData") == "private"


def test_stream_route未知流名抛错() -> None:
    with pytest.raises(ValueError, match="未登记的 WebSocket 流名称"):
        stream_route("btcusdt@unknownStream")


def test_endpoints正式网与测试网三个路由地址() -> None:
    mainnet = endpoints_for("mainnet")
    assert mainnet.rest_base == "https://fapi.binance.com"
    assert mainnet.ws_public_base == "wss://fstream.binance.com/public"
    assert mainnet.ws_market_base == "wss://fstream.binance.com/market"
    assert mainnet.ws_private_base == "wss://fstream.binance.com/private"
    testnet = endpoints_for("testnet")
    assert testnet.rest_base == "https://testnet.binancefuture.com"
    assert testnet.ws_public_base == "wss://stream.binancefuture.com/public"
    assert testnet.ws_market_base == "wss://stream.binancefuture.com/market"
    assert testnet.ws_private_base == "wss://stream.binancefuture.com/private"


def test_组合流地址按路由拼接() -> None:
    endpoints = endpoints_for("mainnet")
    url = endpoints.ws_stream_url("market", ["btcusdt@aggTrade", "btcusdt@kline_1m"])
    assert url == (
        "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@kline_1m"
    )
    public = endpoints.ws_stream_url("public", ["btcusdt@bookTicker"])
    assert public == "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker"
    user = endpoints.ws_user_url("KEY123")
    assert user == "wss://fstream.binance.com/private/ws?listenKey=KEY123"


def test_组合流跨路由时报错() -> None:
    endpoints = endpoints_for("mainnet")
    with pytest.raises(ValueError, match="不属于路由"):
        endpoints.ws_stream_url("market", ["btcusdt@bookTicker"])


def test_按路由分组与切分() -> None:
    streams = ["btcusdt@kline_1m", "btcusdt@bookTicker", "btcusdt@aggTrade", "btcusdt@depth5"]
    groups = group_streams_by_route(streams)
    assert groups["market"] == ["btcusdt@kline_1m", "btcusdt@aggTrade"]
    assert groups["public"] == ["btcusdt@bookTicker", "btcusdt@depth5"]
    batches = split_stream_batches([f"c{index}@aggTrade" for index in range(5)], limit=2)
    assert [len(batch) for batch in batches] == [2, 2, 1]


def test_endpoints_dataclass字段() -> None:
    endpoints = Endpoints(
        rest_base="https://example",
        ws_public_base="wss://example/public",
        ws_market_base="wss://example/market",
        ws_private_base="wss://example/private",
    )
    assert endpoints.ws_base_for("public") == "wss://example/public"
    assert endpoints.ws_base_for("market") == "wss://example/market"
    assert endpoints.ws_base_for("private") == "wss://example/private"
