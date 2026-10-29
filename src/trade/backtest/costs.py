"""成交成本模型与压力情景（06 §4）。

规格：docs/06-回测引擎.md §3.2、§4
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

# 防除零（04 §1）
_EPS = 1e-12


@dataclass(frozen=True)
class FeeSchedule:
    """挂单/吃单费率（base.yaml，06 §4.1）。"""

    maker: float
    taker: float


@dataclass(frozen=True)
class Scenario:
    """成本情景参数（06 §4.3）。"""

    name: str
    latency_ms: int  # 执行延迟：1000 → p_1s；3000 → p_3s
    slip_mult: float  # 滑点放大倍数
    half_spread_mult: float  # 最小点差放大倍数
    fixed_slip: float | None  # 单边固定滑点（hell/breakeven 用），None 表示用动态滑点
    tp_taker: bool  # 止盈是否按吃单费率
    all_taker: bool  # 所有成交是否按吃单费率（hell）


_SCENARIOS: dict[str, Scenario] = {
    "base": Scenario("base", 1000, 1.0, 1.0, None, False, False),
    "stress": Scenario("stress", 3000, 2.0, 2.0, None, True, False),
    "hell": Scenario("hell", 3000, 1.0, 1.0, 0.0015, True, True),
}


def scenario(name: str) -> Scenario:
    """返回成本情景（06 §4.3）。breakeven 由二分搜索单独求。"""
    if name not in _SCENARIOS:
        raise ValueError(f"未知成本情景：{name}（仅 {sorted(_SCENARIOS)}）")
    return _SCENARIOS[name]


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
