"""行情管理与再平衡纯逻辑单测。

规格：docs/07-实盘与风控.md §3.1、§3.5
"""

from __future__ import annotations

from trade.data.universe import SnapshotCoin, UniverseSnapshot
from trade.live.market_data import (
    exited_symbols,
    new_entrants,
    should_renew_listen_key,
    should_rotate_connection,
    subscription_symbols,
    top_gainers,
    trading_scope,
)


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
