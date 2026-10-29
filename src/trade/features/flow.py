"""订单流类特征（#23~#27，来自 aggTrades 原子量）。

规格：docs/04-特征与标签.md §4.2
"""

from __future__ import annotations

import numpy as np

from trade.features.kline import EPS, rolling_mean, rolling_sum


def whale_ratio(
    buy_quote: np.ndarray,
    sell_quote: np.ndarray,
    whale_buy_quote: np.ndarray,
    whale_sell_quote: np.ndarray,
) -> np.ndarray:
    """#23 whale_ratio = (whale_buy_quote_t + whale_sell_quote_t) / (tot_t + ε)。"""
    tot = np.asarray(buy_quote, dtype="float64") + np.asarray(sell_quote, dtype="float64")
    return (np.asarray(whale_buy_quote) + np.asarray(whale_sell_quote)) / (tot + EPS)


def whale_net(
    buy_quote: np.ndarray,
    sell_quote: np.ndarray,
    whale_buy_quote: np.ndarray,
    whale_sell_quote: np.ndarray,
) -> np.ndarray:
    """#24 whale_net = (whale_buy_quote_t - whale_sell_quote_t) / (tot_t + ε)。"""
    tot = np.asarray(buy_quote, dtype="float64") + np.asarray(sell_quote, dtype="float64")
    return (np.asarray(whale_buy_quote) - np.asarray(whale_sell_quote)) / (tot + EPS)


def whale_net_15(
    buy_quote: np.ndarray,
    sell_quote: np.ndarray,
    whale_buy_quote: np.ndarray,
    whale_sell_quote: np.ndarray,
) -> np.ndarray:
    """#25 whale_net_15 = sum_15(whale_buy - whale_sell) / (sum_15(tot) + ε)。"""
    tot = np.asarray(buy_quote, dtype="float64") + np.asarray(sell_quote, dtype="float64")
    num = rolling_sum(np.asarray(whale_buy_quote) - np.asarray(whale_sell_quote), 15)
    return num / (rolling_sum(tot, 15) + EPS)


def burst_1s(max_buy_1s_quote: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#26 burst_1s = ln(1 + max_buy_1s_quote_t / (mean_60(qv)/60 + ε))。"""
    qv = np.asarray(quote_volume, dtype="float64")
    return np.log1p(np.asarray(max_buy_1s_quote) / (rolling_mean(qv, 60) / 60.0 + EPS))


def max_trade_rel(max_trade_quote: np.ndarray, whale_thr: np.ndarray) -> np.ndarray:
    """#27 max_trade_rel = ln(1 + max_trade_quote_t / (whale_thr_t + ε))。"""
    return np.log1p(
        np.asarray(max_trade_quote, dtype="float64")
        / (np.asarray(whale_thr, dtype="float64") + EPS)
    )
