"""入场价与成本计算（回测与标签共用，单一实现）。

规格：docs/06-回测引擎.md §3.2、§4；docs/04-特征与标签.md §7.5
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trade.common.costs import half_spread, slip
from trade.common.sizing import loss_rate


@dataclass(frozen=True)
class EntryParams:
    """入场计算结果（回测与标签共用，06 §3.2、§4；04 §7.5）。"""

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
    逐笔一致（04 §7.5「不另写一份出场逻辑」）。

    Args:
        c_t: 信号 K 线收盘价。
        p_raw: 入场参考价（p_1s(t+1) 或 p_3s(t+1)）。
        sigma: 信号时刻 ATR 百分比（atr_pct）。
        atr_abs: 信号时刻 ATR14 绝对值。
        rv15: 未标准化的 15 分钟 1m 收益标准差（用于滑点）。
        qv: 信号 K 线成交额（quote_volume）。
        tick_size: 价格步长。
        k_sl/sl_min/sl_max: 止损 ATR 系数与裁剪区间（04 §7.1）。
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
