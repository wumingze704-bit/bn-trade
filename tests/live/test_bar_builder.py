"""分钟聚合单测。

规格：docs/07-实盘与风控.md §3.2、§3.3
"""

from __future__ import annotations

from trade.live.bar_builder import BarBuilder, agg_trade_gap, aggregate_trades, minute_complete


def test_agg_trade_gap_detected() -> None:
    assert agg_trade_gap(0, 1) is False  # 首条
    assert agg_trade_gap(1, 2) is False
    assert agg_trade_gap(2, 4) is True  # 缺口


def test_minute_complete_by_next_minute_trade() -> None:
    assert minute_complete(last_trade_ts_ms=60000, minute_end_ms=60000, now_ms=59000) is True


def test_minute_complete_by_grace() -> None:
    assert minute_complete(last_trade_ts_ms=59900, minute_end_ms=60000, now_ms=60400) is True
    assert minute_complete(last_trade_ts_ms=59900, minute_end_ms=60000, now_ms=60200) is False


def test_aggregate_trades_atomics() -> None:
    trades = [
        (100.0, 2.0, False, 1000),  # taker buy
        (101.0, 3.0, True, 1500),  # taker sell
        (102.0, 1.0, False, 2000),  # taker buy
    ]
    atomics = aggregate_trades(trades, whale_thr=200.0)
    assert atomics["volume"] == 6.0
    assert atomics["count"] == 3
    assert atomics["quote_volume"] == 100.0 * 2 + 101.0 * 3 + 102.0 * 1
    assert atomics["taker_buy_quote_volume"] == 100.0 * 2 + 102.0 * 1
    assert atomics["sell_quote"] == 101.0 * 3
    assert atomics["max_trade_quote"] == 303.0


def test_bar_builder_assembles_bar() -> None:
    builder = BarBuilder("SOLUSDT")
    builder.on_kline({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5})
    assert (
        builder.on_agg_trade(agg_id=1, price=10.2, qty=2.0, is_buyer_maker=False, ts_ms=1000)
        is False
    )
    bar = builder.build_bar(open_time_ms=0, whale_thr=1000.0)
    assert bar["close"] == 10.5
    assert bar["volume"] == 2.0
    assert bar["count"] == 1
