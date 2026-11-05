"""WS 消息解析单测。

规格：docs/09-币安数据与API.md §3.3；docs/07-实盘与风控.md §3.2
"""

from __future__ import annotations

from trade.live.exchange.ws import (
    parse_agg_trade,
    parse_book_ticker,
    parse_kline,
    parse_ws_message,
)


def test_parse_kline_closed() -> None:
    data = {
        "e": "kline",
        "E": 1700000000000,
        "s": "BTCUSDT",
        "k": {
            "t": 1699999980000,
            "c": "43000.5",
            "h": "43100.0",
            "l": "42900.0",
            "v": "12.3",
            "q": "530000.0",
            "x": True,
        },
    }
    event = parse_kline(data)
    assert event.symbol == "BTCUSDT"
    assert event.close == 43000.5
    assert event.is_closed is True
    assert event.open_time_ms == 1699999980000


def test_parse_agg_trade() -> None:
    data = {
        "e": "aggTrade",
        "E": 1700000000000,
        "s": "SOLUSDT",
        "a": 12345,
        "p": "99.5",
        "q": "10.0",
        "T": 1700000000500,
        "m": False,
    }
    event = parse_agg_trade(data)
    assert event.agg_trade_id == 12345
    assert event.is_buyer_maker is False
    assert event.transact_time_ms == 1700000000500


def test_parse_book_ticker() -> None:
    data = {"e": "bookTicker", "E": 1700000000000, "s": "SUIUSDT", "b": "1.2", "a": "1.21"}
    event = parse_book_ticker(data)
    assert event.best_bid == 1.2
    assert event.best_ask == 1.21


def test_parse_non_json_returns_empty() -> None:
    assert parse_ws_message("not json") == {}
