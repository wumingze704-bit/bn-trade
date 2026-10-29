"""测试用合成 bars 生成器（2 币 × N 天等小数据，确定性）。

规格：tests/fixtures 手造小数据（AGENTS.md §7）；不含真实下载数据。
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd


def make_bars(
    n: int,
    seed: int,
    *,
    base_ms: int | None = None,
    with_futures: bool = True,
) -> pd.DataFrame:
    """生成 n 根分钟 bars（含 K 线 + 原子量 + 可选 oi/funding），确定性。"""
    rng = np.random.default_rng(seed)
    if base_ms is None:
        base_ms = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    open_time = (base_ms + np.arange(n) * 60_000).astype("int64")

    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, n)))
    prev_close = np.concatenate([[100.0], close[:-1]])
    open_ = prev_close.copy()
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0, 0.001, n))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0, 0.001, n))

    volume = rng.lognormal(3.0, 0.5, n)
    quote_volume = volume * close
    count = rng.integers(50, 500, n)
    taker_buy_quote_volume = quote_volume * rng.uniform(0.3, 0.7, n)
    taker_buy_volume = taker_buy_quote_volume / close

    buy_quote = taker_buy_quote_volume
    sell_quote = quote_volume - taker_buy_quote_volume
    max_trade_quote = quote_volume * rng.uniform(0.001, 0.05, n)
    max_buy_1s_quote = quote_volume / 60.0 * rng.uniform(0.5, 5.0, n)
    whale_buy_quote = buy_quote * rng.uniform(0.0, 0.3, n)
    whale_sell_quote = sell_quote * rng.uniform(0.0, 0.3, n)
    whale_thr = np.full(n, 5000.0)

    frame = pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": open_time + 59_999,
            "quote_volume": quote_volume,
            "count": count,
            "taker_buy_volume": taker_buy_volume,
            "taker_buy_quote_volume": taker_buy_quote_volume,
            "agg_n": count,
            "buy_quote": buy_quote,
            "sell_quote": sell_quote,
            "max_trade_quote": max_trade_quote,
            "max_buy_1s_quote": max_buy_1s_quote,
            "whale_buy_quote": whale_buy_quote,
            "whale_sell_quote": whale_sell_quote,
            "whale_thr": whale_thr,
            "p_1s": close,
            "p_1s_filled": np.zeros(n, dtype="int64"),
            "p_3s": close,
            "p_3s_filled": np.zeros(n, dtype="int64"),
            "hi_post_1s": high,
            "lo_post_1s": low,
            "hi_post_3s": high,
            "lo_post_3s": low,
            "is_filled": np.zeros(n, dtype="int64"),
        }
    )
    if with_futures:
        oi = 1_000_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.0001, n)))
        frame["oi"] = oi
        frame["funding_rate"] = np.full(n, 0.0001)
    return frame
