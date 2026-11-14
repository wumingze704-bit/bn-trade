"""入场价与成本计算（回测与标签共用，单一实现）。

规格：docs/06-回测引擎.md §3.2、§4；docs/04-特征与标签.md §7.3
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from trade.common.costs import half_spread, slip
from trade.common.sizing import loss_rate


def half_spread_for(close_t: float, tick_size: float, half_spread_min: float) -> float:
    """单根 K 线的半价差（06 §3.2）：`max(0.5 × tick_rel, half_spread_min)`。

    追价判断（06 §3.1 条件 5）与锚点计算（06 §3.4）共用本函数，避免两处各写一份公式。

    Args:
        close_t: 信号 K 线收盘价。
        tick_size: 价格步长。
        half_spread_min: 最小半价差（已按情景放大）。

    Returns:
        半价差（比例，0.0001 = 1bp）。
    """
    return half_spread(tick_size / close_t, half_spread_min)


def half_spread_array(
    close: npt.NDArray[np.float64], tick_size: float, half_spread_min: float
) -> npt.NDArray[np.float64]:
    """`half_spread_for` 的向量化版本（04 §7.3 批量筛样本用）。"""
    return np.maximum(0.5 * tick_size / close, half_spread_min)


def stale_entry_mask(filled: npt.NDArray[np.float64]) -> npt.NDArray[np.bool_]:
    """入场价陈旧标记 `p_{lat}_filled = 1`（03 §2.2、06 §3.1 条件 1）。

    该标记为 1 表示入场那一秒内没有成交、价格沿用上一分钟收盘价，实盘读到的是
    陈旧价，因此放弃该信号（原因 `entry_price_stale`）。逐笔整分钟缺失时该列可能为
    NaN，此处返回 False，由调用方的入场价有限性检查（`no_entry_price`）负责丢弃。
    """
    return np.asarray(np.asarray(filled, dtype="float64") == 1.0, dtype=bool)


def chase_exceeded_array(
    *,
    p_raw: npt.NDArray[np.float64],
    half_spread: npt.NDArray[np.float64],
    close_t: npt.NDArray[np.float64],
    max_chase: float,
) -> npt.NDArray[np.bool_]:
    """追价保护（06 §3.1 条件 5）：用 `p_raw × (1 + half_spread)` 近似卖一价。

    实盘按卖一价判断（`trade.live.orders.compute_limit_price` 的 `min(ask × (1 + 2 tick_rel),
    c_t × (1 + max_chase))`）；历史数据没有盘口，用最近成交价加上半价差近似卖一价。

    Args:
        p_raw: 入场参考价 `p_1s(t+1)` 或 `p_3s(t+1)`。
        half_spread: 与该根 K 线对应的半价差（`half_spread_array`）。
        close_t: 信号 K 线收盘价 `c_t`。
        max_chase: 追价上限（`risk.max_chase`）。

    Returns:
        bool 数组，True 表示超过上限、应放弃该信号。
    """
    return p_raw * (1.0 + half_spread) > close_t * (1.0 + max_chase)


@dataclass(frozen=True)
class EntryParams:
    """入场计算结果（回测与标签共用，06 §3.2、§4；04 §7.3）。"""

    p_fill: float  # 开仓成交价 = p_raw × (1 + 半价差 + 入场滑点)
    sl: float  # 初始止损幅度（clip 到 [sl_min, sl_max]）
    atr_abs: float  # 入场时刻 ATR14 绝对值（持仓期固定）
    slip: float  # 单边入场滑点
    slip_stop: float  # 止损离场滑点 = 2 × slip
    half_spread: float  # 半价差
    loss_rate: float  # 每 1 USDT 名义价值的初始止损预计亏损（06 §5.2）


def compute_entry(
    *,
    c_t: float,
    p_raw: float,
    sigma: float,
    atr_abs: float,
    rv15: float,
    qv: float,
    tick_size: float,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    notional_ref: float,
    half_spread_min: float,
    slip_min: float,
    Y: float,
    fixed_slip: float | None,
    fee_taker: float,
) -> EntryParams:
    """按回测口径计算入场价、初始止损与成本（06 §3.2、§4）。

    回测 `precompute_trades` 与标签 `exit_sim` 都调用本函数，保证两者入场价与成本
    逐笔一致（04 §7.3「不另写一份出场逻辑」）。

    Args:
        c_t: 信号 K 线收盘价。
        p_raw: 入场参考价（p_1s(t+1) 或 p_3s(t+1)）。
        sigma: 信号时刻 ATR 百分比（atr_pct）。
        atr_abs: 信号时刻 ATR14 绝对值。
        rv15: 未标准化的 15 分钟 1m 收益标准差（用于滑点）。
        qv: 信号 K 线成交额（quote_volume）。
        tick_size: 价格步长。
        k_sl/sl_min/sl_max: 止损 ATR 系数与裁剪区间（04 §7.2）。
        notional_ref: 参考名义价值（滑点模型用，06 §4.2）。
        half_spread_min: 最小半价差（已按情景放大）。
        slip_min: 最小单边滑点（已按情景放大）。
        Y: 平方根冲击系数。
        fixed_slip: 固定单边滑点（hell/breakeven 用），None 表示动态滑点。
        fee_taker: 吃单费率。

    Returns:
        EntryParams。
    """
    sl_ = float(np.clip(k_sl * sigma, sl_min, sl_max))
    tick_rel = tick_size / c_t
    hs = half_spread(tick_rel, half_spread_min)
    if fixed_slip is not None:
        slip_ = fixed_slip
    else:
        slip_ = slip(
            slip_min=slip_min,
            Y=Y,
            rv_15_raw=rv15,
            notional=notional_ref,
            qv=qv,
        )
    slip_stop = 2.0 * slip_
    p_fill = p_raw * (1.0 + hs + slip_)
    lr = loss_rate(sl=sl_, slip_stop=slip_stop, half_spread=hs, fee_taker=fee_taker)
    return EntryParams(
        p_fill=p_fill,
        sl=sl_,
        atr_abs=atr_abs,
        slip=slip_,
        slip_stop=slip_stop,
        half_spread=hs,
        loss_rate=lr,
    )
