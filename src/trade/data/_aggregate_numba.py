"""aggTrades 分钟汇总的 numba 内核。

规格：docs/03-数据与选币.md §2.2；纯 Python 对照实现位于 `aggregate.py`。
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from trade.common._numba import njit


@njit
def aggregate_arrays_numba(
    transact_times_ms: NDArray[np.int64],
    prices: NDArray[np.float64],
    quantities: NDArray[np.float64],
    is_buyer_maker: NDArray[np.bool_],
    whale_threshold: float,
    previous_close: float,
    has_previous_close: bool,
) -> NDArray[np.float64]:
    """汇总有序交易数组，返回每分钟一行的 17 列 float64 矩阵。"""
    trade_count = len(transact_times_ms)
    if trade_count == 0:
        return np.empty((0, 17), dtype=np.float64)

    group_count = 1
    previous_minute = transact_times_ms[0] // 60_000
    for row_index in range(1, trade_count):
        current_minute = transact_times_ms[row_index] // 60_000
        if current_minute != previous_minute:
            group_count += 1
            previous_minute = current_minute

    output = np.empty((group_count, 17), dtype=np.float64)
    group_start = 0
    prior_close = previous_close if has_previous_close else np.nan

    for group_index in range(group_count):
        minute_open_ms = transact_times_ms[group_start] // 60_000 * 60_000
        group_end = group_start + 1
        while (
            group_end < trade_count
            and transact_times_ms[group_end] // 60_000 * 60_000 == minute_open_ms
        ):
            group_end += 1

        buy_quote = 0.0
        sell_quote = 0.0
        max_trade_quote = 0.0
        max_buy_1s_quote = 0.0
        whale_buy_quote = 0.0
        whale_sell_quote = 0.0
        current_second = transact_times_ms[group_start] // 1000
        current_second_buy_quote = 0.0
        p_1s = np.nan
        p_3s = np.nan
        hi_post_1s = np.nan
        lo_post_1s = np.nan
        hi_post_3s = np.nan
        lo_post_3s = np.nan
        has_p_1s = False
        has_p_3s = False
        has_post_1s = False
        has_post_3s = False

        for row_index in range(group_start, group_end):
            quote = prices[row_index] * quantities[row_index]
            if quote > max_trade_quote:
                max_trade_quote = quote
            if is_buyer_maker[row_index]:
                sell_quote += quote
                if quote >= whale_threshold:
                    whale_sell_quote += quote
            else:
                buy_quote += quote
                if quote >= whale_threshold:
                    whale_buy_quote += quote

            trade_second = transact_times_ms[row_index] // 1000
            if trade_second != current_second:
                if current_second_buy_quote > max_buy_1s_quote:
                    max_buy_1s_quote = current_second_buy_quote
                current_second = trade_second
                current_second_buy_quote = 0.0
            if not is_buyer_maker[row_index]:
                current_second_buy_quote += quote

            if transact_times_ms[row_index] <= minute_open_ms + 1000:
                p_1s = prices[row_index]
                has_p_1s = True
            else:
                if not has_post_1s:
                    hi_post_1s = prices[row_index]
                    lo_post_1s = prices[row_index]
                    has_post_1s = True
                else:
                    if prices[row_index] > hi_post_1s:
                        hi_post_1s = prices[row_index]
                    if prices[row_index] < lo_post_1s:
                        lo_post_1s = prices[row_index]

            if transact_times_ms[row_index] <= minute_open_ms + 3000:
                p_3s = prices[row_index]
                has_p_3s = True
            else:
                if not has_post_3s:
                    hi_post_3s = prices[row_index]
                    lo_post_3s = prices[row_index]
                    has_post_3s = True
                else:
                    if prices[row_index] > hi_post_3s:
                        hi_post_3s = prices[row_index]
                    if prices[row_index] < lo_post_3s:
                        lo_post_3s = prices[row_index]

        if current_second_buy_quote > max_buy_1s_quote:
            max_buy_1s_quote = current_second_buy_quote
        if not has_p_1s:
            p_1s = prior_close
        if not has_p_3s:
            p_3s = prior_close
        if not has_post_1s:
            hi_post_1s = p_1s
            lo_post_1s = p_1s
        if not has_post_3s:
            hi_post_3s = p_3s
            lo_post_3s = p_3s

        output[group_index, 0] = minute_open_ms
        output[group_index, 1] = group_end - group_start
        output[group_index, 2] = buy_quote
        output[group_index, 3] = sell_quote
        output[group_index, 4] = max_trade_quote
        output[group_index, 5] = max_buy_1s_quote
        output[group_index, 6] = whale_buy_quote
        output[group_index, 7] = whale_sell_quote
        output[group_index, 8] = whale_threshold
        output[group_index, 9] = p_1s
        output[group_index, 10] = 0 if has_p_1s else 1
        output[group_index, 11] = p_3s
        output[group_index, 12] = 0 if has_p_3s else 1
        output[group_index, 13] = hi_post_1s
        output[group_index, 14] = lo_post_1s
        output[group_index, 15] = hi_post_3s
        output[group_index, 16] = lo_post_3s
        prior_close = prices[group_end - 1]
        group_start = group_end

    return output
