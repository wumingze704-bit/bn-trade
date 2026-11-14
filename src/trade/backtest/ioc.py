"""IOC 未成交率评估的纯函数（06 §4.3；只报告，不改变 `base` 成本）。

规格：docs/06-回测引擎.md §4.3

实盘开仓是 LIMIT IOC：读价后到订单到达之间价格可能跑掉，导致 IOC 未成交。评估方法：
以 `p_1s` 为读价时刻，取其后 `[lat, lat + order_rtt_ms]` 内的主动买成交价；若最高价
超过实盘限价公式算出的限价，则记为“可能未成交”。
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def window_taker_buy_high(
    *,
    timestamps_ms: npt.NDArray[np.int64],
    prices: npt.NDArray[np.float64],
    is_buyer_maker: npt.NDArray[np.int64],
    start_ms: int,
    end_ms: int,
) -> float:
    """窗口内主动买成交的最高价（无主动买成交时返回 NaN）。

    Args:
        timestamps_ms: 逐笔成交时间（毫秒，UTC，升序）。
        prices: 逐笔成交价。
        is_buyer_maker: 1 表示买方是挂单方（卖方主动），0 表示买方主动。
        start_ms: 窗口起点（含）。
        end_ms: 窗口终点（不含）。

    Returns:
        窗口内主动买成交价的最大值；窗口内没有主动买成交时为 NaN。
    """
    stamps = np.asarray(timestamps_ms, dtype="int64")
    lo = int(np.searchsorted(stamps, start_ms, side="left"))
    hi = int(np.searchsorted(stamps, end_ms, side="left"))
    if hi <= lo:
        return float("nan")
    window_prices = np.asarray(prices, dtype="float64")[lo:hi]
    taker_buy = np.asarray(is_buyer_maker, dtype="int64")[lo:hi] == 0
    if not bool(taker_buy.any()):
        return float("nan")
    return float(window_prices[taker_buy].max())


def ioc_missed(*, limit_price: float, taker_buy_high: float) -> bool:
    """窗口内主动买最高价超过限价 → 该笔 IOC 可能未成交（06 §4.3）。

    Args:
        limit_price: 实盘限价（`trade.live.orders.compute_limit_price` 的返回值）。
        taker_buy_high: `window_taker_buy_high` 的返回值。

    Returns:
        True 表示“可能未成交”；窗口内无主动买成交（NaN）按未成交风险处理，
        返回 False 并由调用方单独统计“无成交”。
    """
    if not np.isfinite(taker_buy_high) or limit_price <= 0.0:
        return False
    return taker_buy_high > limit_price
