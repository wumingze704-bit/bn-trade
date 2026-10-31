"""成交成本纯函数（半价差、滑点、盈亏平衡滑点）。

规格：docs/06-回测引擎.md §3.2、§4。放 `trade.common` 以避免 `trade.common` 依赖
`trade.backtest`（后者会触发 `trade.backtest.__init__` → engine → entry 的循环导入）。
"""

from __future__ import annotations

import math
from collections.abc import Callable

# 防除零（04 §1）
_EPS = 1e-12


def half_spread(tick_rel: float, half_spread_min: float) -> float:
    """半价差 = max(0.5 × tick_rel, half_spread_min)（06 §3.2）。"""
    return max(0.5 * tick_rel, half_spread_min)


def slip(
    *,
    slip_min: float,
    Y: float,
    rv_15_raw: float,
    notional: float,
    qv: float,
) -> float:
    """平方根冲击模型滑点（06 §4.2）。

    Args:
        slip_min: 最小单边滑点（1bp）。
        Y: 冲击系数（1.0）。
        rv_15_raw: 未标准化的 15 分钟 1m 收益标准差。
        notional: 名义价值。
        qv: 信号 K 线成交额（quote_volume）。
    """
    return slip_min + Y * rv_15_raw * math.sqrt(notional / (qv + _EPS))


def breakeven_slip(
    *,
    net_return: Callable[[float], float],
    lo: float = 0.0,
    hi: float = 0.01,
    tol: float = 1e-7,
    max_iter: int = 100,
) -> float:
    """二分搜索使净收益 = 0 的单边固定滑点（06 §4.3 breakeven）。

    Args:
        net_return: 以单边固定滑点为输入、返回净收益（USDT）的函数。
        lo/hi: 搜索区间（默认 0~1%）。
        tol: 收敛容差。

    Returns:
        盈亏平衡滑点（比例）。
    """
    f_lo = net_return(lo)
    f_hi = net_return(hi)
    if f_lo < 0:  # 滑点为 0 时仍亏损 → 无盈亏平衡点，返回 0
        return lo
    if f_hi > 0:  # 上限仍盈利 → 返回上限（超出搜索区间）
        return hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = net_return(mid)
        if abs(f_mid) < tol:
            return mid
        if f_mid > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
