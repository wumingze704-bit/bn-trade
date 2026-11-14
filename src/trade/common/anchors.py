"""入场锚点与逐笔成本锚点数组（回测与 `exit_sim` 标签共用）。

规格：docs/06-回测引擎.md §3.2、§3.3、§3.4

锚点指「信号在 K 线 t、入场在 t+1」时的两个触发价：

- `down[t]`：初始止损价 `D0 = P_fill × (1 − sl)`，按 tickSize 向下取整（06 §3.2）。
- `up[t]`：封顶止盈价 `trail` 为 `P_fill + tp_cap_r × R`、`fixed` 为
  `P_fill × (1 + rr × sl)`，按 tickSize 向上取整（06 §3.3）。

同一根 K 线的入场成本（`P_fill`、`sl`、`R`、最小止损上移幅度、每 1 USDT 名义价值的
止损亏损）一并按数组算出，逐笔状态机只按下标取值，不在状态机里重复计算（06 §3.4）。
数组按（币, 组配置）算一次，同一份 bars 上的全部候选信号共用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from trade.common._numba import njit
from trade.common.entry import half_spread_for

# 防除零（04 §1）
_EPS = 1e-12

# 出场模式编码（06 §3.3）：0 = fixed，1 = trail
_MODE_FIXED = 0

# 逐笔锚点元组的字段顺序（`anchor_at_py` 与 numba 版共用）
AnchorTuple = tuple[float, float, float, float, float, float, float, float, float, float]


@dataclass(frozen=True)
class AnchorSpec:
    """锚点组配置（06 §3.3）：一次运行内固定，决定每根 K 线的入场成本与触发价。

    Attributes:
        mode: 出场模式编码（0 = fixed，1 = trail）。
        rr: `fixed` 模式的止盈 R 倍数。
        tp_cap_r: `trail` 模式的封顶止盈 R 倍数。
        min_stop_move_r: 最小止损上移幅度（R 倍数）。
        k_sl/sl_min/sl_max: 止损 ATR 系数与裁剪区间（04 §7.2）。
        half_spread_min: 最小半价差（已按情景放大）。
        slip_min: 最小单边滑点（已按情景放大）。
        Y: 平方根冲击系数。
        fixed_slip: 固定单边滑点；NaN 表示动态滑点（hell / breakeven 用固定值）。
        fee_taker: 吃单费率。
        notional_ref: 参考名义价值（滑点模型用，06 §4.2）。
    """

    mode: int
    rr: float
    tp_cap_r: float
    min_stop_move_r: float
    k_sl: float
    sl_min: float
    sl_max: float
    half_spread_min: float
    slip_min: float
    Y: float
    fixed_slip: float
    fee_taker: float
    notional_ref: float


@dataclass(frozen=True)
class AnchorSeries:
    """每根 K 线的锚点与入场成本数组（06 §3.3、§3.4），长度等于 bars 行数。

    `valid[t]` 为 False 表示该 K 线不能作为信号（下一根入场价缺失、ATR 或 15 分钟
    波动不可用）；`valid` 为 False 的行其余字段为 NaN。
    """

    down: npt.NDArray[np.float64]
    up: npt.NDArray[np.float64]
    p_fill: npt.NDArray[np.float64]
    sl: npt.NDArray[np.float64]
    r_dist: npt.NDArray[np.float64]
    min_move: npt.NDArray[np.float64]
    slip: npt.NDArray[np.float64]
    slip_stop: npt.NDArray[np.float64]
    half_spread: npt.NDArray[np.float64]
    loss_rate: npt.NDArray[np.float64]
    valid: npt.NDArray[np.bool_]


def trade_anchors_py(
    *,
    p_fill: float,
    sl: float,
    tick_size: float,
    mode: int,
    rr: float,
    tp_cap_r: float,
    min_stop_move_r: float,
) -> tuple[float, float, float, float]:
    """已知入场价与止损幅度时的锚点（纯 Python 参考实现，06 §3.2、§3.3）。

    Args:
        p_fill: 开仓成交价（已含点差与入场滑点）。
        sl: 止损幅度（比例）。
        tick_size: 价格步长。
        mode: 出场模式编码（0 = fixed，1 = trail）。
        rr: `fixed` 模式的止盈 R 倍数。
        tp_cap_r: `trail` 模式的封顶止盈 R 倍数。
        min_stop_move_r: 最小止损上移幅度（R 倍数）。

    Returns:
        (初始止损价 D0, 封顶止盈价 cap, 1R 价格距离, 最小止损上移幅度)。
    """
    r_dist = p_fill * sl
    down = math.floor(p_fill * (1.0 - sl) / tick_size + 1e-9) * tick_size
    if mode == _MODE_FIXED:
        up = math.ceil(p_fill * (1.0 + rr * sl) / tick_size - 1e-9) * tick_size
    else:
        up = math.ceil((p_fill + tp_cap_r * r_dist) / tick_size - 1e-9) * tick_size
    min_move = max(tick_size, min_stop_move_r * r_dist)
    return down, up, r_dist, min_move


@njit
def trade_anchors_nb(
    p_fill: float,
    sl: float,
    tick_size: float,
    mode: int,
    rr: float,
    tp_cap_r: float,
    min_stop_move_r: float,
) -> tuple[float, float, float, float]:
    """numba 版单笔锚点，与 `trade_anchors_py` 逐字段一致（06 §3.2、§3.3）。"""
    r_dist = p_fill * sl
    down = math.floor(p_fill * (1.0 - sl) / tick_size + 1e-9) * tick_size
    if mode == _MODE_FIXED:
        up = math.ceil(p_fill * (1.0 + rr * sl) / tick_size - 1e-9) * tick_size
    else:
        up = math.ceil((p_fill + tp_cap_r * r_dist) / tick_size - 1e-9) * tick_size
    min_move = min_stop_move_r * r_dist
    if min_move < tick_size:
        min_move = tick_size
    return down, up, r_dist, min_move


def anchor_at_py(
    *,
    close_t: float,
    p_next: float,
    sigma: float,
    rv15: float,
    qv: float,
    tick_size: float,
    spec: AnchorSpec,
) -> AnchorTuple:
    """单根 K 线的锚点与入场成本（纯 Python 参考实现，06 §3.2、§3.3）。

    Args:
        close_t: 信号 K 线收盘价。
        p_next: 入场参考价 `p_1s(t+1)`（或 `p_3s(t+1)`）。
        sigma: 信号时刻 ATR 百分比。
        rv15: 未标准化的 15 分钟 1m 收益标准差（滑点用）。
        qv: 信号 K 线成交额。
        tick_size: 价格步长。
        spec: 锚点组配置。

    Returns:
        (down, up, p_fill, sl, r_dist, min_move, slip, slip_stop, half_spread, loss_rate)。
    """
    sl_ = min(max(spec.k_sl * sigma, spec.sl_min), spec.sl_max)
    hs = half_spread_for(close_t, tick_size, spec.half_spread_min)
    if math.isnan(spec.fixed_slip):
        slip_ = spec.slip_min + spec.Y * rv15 * math.sqrt(spec.notional_ref / (qv + _EPS))
    else:
        slip_ = spec.fixed_slip
    slip_stop = 2.0 * slip_
    p_fill = p_next * (1.0 + hs + slip_)
    down, up, r_dist, min_move = trade_anchors_py(
        p_fill=p_fill,
        sl=sl_,
        tick_size=tick_size,
        mode=spec.mode,
        rr=spec.rr,
        tp_cap_r=spec.tp_cap_r,
        min_stop_move_r=spec.min_stop_move_r,
    )
    lr = sl_ + slip_stop + hs + 2.0 * spec.fee_taker
    return down, up, p_fill, sl_, r_dist, min_move, slip_, slip_stop, hs, lr


@njit
def anchor_at_nb(
    close_t: float,
    p_next: float,
    sigma: float,
    rv15: float,
    qv: float,
    tick_size: float,
    mode: int,
    rr: float,
    tp_cap_r: float,
    min_stop_move_r: float,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    half_spread_min: float,
    slip_min: float,
    y_coef: float,
    fixed_slip: float,
    fee_taker: float,
    notional_ref: float,
) -> AnchorTuple:
    """numba 版单根 K 线锚点，与 `anchor_at_py` 逐字段一致（06 §3.3）。"""
    sl_ = k_sl * sigma
    if sl_ < sl_min:
        sl_ = sl_min
    if sl_ > sl_max:
        sl_ = sl_max
    tick_rel = tick_size / close_t
    hs = 0.5 * tick_rel
    if hs < half_spread_min:
        hs = half_spread_min
    if math.isnan(fixed_slip):
        slip_ = slip_min + y_coef * rv15 * math.sqrt(notional_ref / (qv + _EPS))
    else:
        slip_ = fixed_slip
    slip_stop = 2.0 * slip_
    p_fill = p_next * (1.0 + hs + slip_)
    down, up, r_dist, min_move = trade_anchors_nb(
        p_fill, sl_, tick_size, mode, rr, tp_cap_r, min_stop_move_r
    )
    lr = sl_ + slip_stop + hs + 2.0 * fee_taker
    return down, up, p_fill, sl_, r_dist, min_move, slip_, slip_stop, hs, lr


def _empty_series(n: int) -> tuple[AnchorSeries, list[npt.NDArray[np.float64]]]:
    """分配锚点数组（numba 版与 Python 版共用布局）。"""
    arrays = [np.full(n, np.nan, dtype="float64") for _ in range(10)]
    series = AnchorSeries(
        down=arrays[0],
        up=arrays[1],
        p_fill=arrays[2],
        sl=arrays[3],
        r_dist=arrays[4],
        min_move=arrays[5],
        slip=arrays[6],
        slip_stop=arrays[7],
        half_spread=arrays[8],
        loss_rate=arrays[9],
        valid=np.zeros(n, dtype=np.bool_),
    )
    return series, arrays


def anchor_series_py(
    *,
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    atr_pct: npt.NDArray[np.float64],
    rv15: npt.NDArray[np.float64],
    qv: npt.NDArray[np.float64],
    tick_size: float,
    spec: AnchorSpec,
) -> AnchorSeries:
    """逐根 K 线算锚点数组的纯 Python 参考实现（06 §3.4）。"""
    n = int(close.shape[0])
    series, arrays = _empty_series(n)
    for t in range(n - 1):
        c_t = float(close[t])
        p_next = float(p_entry[t + 1])
        sigma = float(atr_pct[t])
        rv15_t = float(rv15[t])
        qv_t = float(qv[t])
        if not (
            math.isfinite(c_t)
            and math.isfinite(p_next)
            and p_next > 0.0
            and math.isfinite(sigma)
            and math.isfinite(rv15_t)
            and c_t > 0.0
        ):
            continue
        values = anchor_at_py(
            close_t=c_t,
            p_next=p_next,
            sigma=sigma,
            rv15=rv15_t,
            qv=qv_t,
            tick_size=tick_size,
            spec=spec,
        )
        for array, value in zip(arrays, values, strict=True):
            array[t] = value
        series.valid[t] = True
    return series


@njit
def _anchor_series_nb(
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    atr_pct: npt.NDArray[np.float64],
    rv15: npt.NDArray[np.float64],
    qv: npt.NDArray[np.float64],
    tick_size: float,
    mode: int,
    rr: float,
    tp_cap_r: float,
    min_stop_move_r: float,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    half_spread_min: float,
    slip_min: float,
    y_coef: float,
    fixed_slip: float,
    fee_taker: float,
    notional_ref: float,
) -> tuple[
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.bool_],
]:
    """按币一次算完全部 K 线的锚点数组（06 §3.4），与 `anchor_series_py` 逐字段一致。"""
    n = close.shape[0]
    down = np.full(n, np.nan)
    up = np.full(n, np.nan)
    p_fill = np.full(n, np.nan)
    sl_arr = np.full(n, np.nan)
    r_dist = np.full(n, np.nan)
    min_move = np.full(n, np.nan)
    slip_arr = np.full(n, np.nan)
    slip_stop = np.full(n, np.nan)
    half_spread = np.full(n, np.nan)
    loss_rate = np.full(n, np.nan)
    valid = np.zeros(n, dtype=np.bool_)
    for t in range(n - 1):
        c_t = close[t]
        p_next = p_entry[t + 1]
        sigma = atr_pct[t]
        rv15_t = rv15[t]
        qv_t = qv[t]
        if not (
            math.isfinite(c_t)
            and math.isfinite(p_next)
            and p_next > 0.0
            and math.isfinite(sigma)
            and math.isfinite(rv15_t)
            and c_t > 0.0
        ):
            continue
        values = anchor_at_nb(
            c_t,
            p_next,
            sigma,
            rv15_t,
            qv_t,
            tick_size,
            mode,
            rr,
            tp_cap_r,
            min_stop_move_r,
            k_sl,
            sl_min,
            sl_max,
            half_spread_min,
            slip_min,
            y_coef,
            fixed_slip,
            fee_taker,
            notional_ref,
        )
        down[t] = values[0]
        up[t] = values[1]
        p_fill[t] = values[2]
        sl_arr[t] = values[3]
        r_dist[t] = values[4]
        min_move[t] = values[5]
        slip_arr[t] = values[6]
        slip_stop[t] = values[7]
        half_spread[t] = values[8]
        loss_rate[t] = values[9]
        valid[t] = True
    return (
        down,
        up,
        p_fill,
        sl_arr,
        r_dist,
        min_move,
        slip_arr,
        slip_stop,
        half_spread,
        loss_rate,
        valid,
    )


def anchor_series(
    *,
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    atr_pct: npt.NDArray[np.float64],
    rv15: npt.NDArray[np.float64],
    qv: npt.NDArray[np.float64],
    tick_size: float,
    spec: AnchorSpec,
    use_numba: bool = True,
) -> AnchorSeries:
    """按币算一次锚点与入场成本数组（06 §3.4）。

    回测的逐笔预计算与 `exit_sim` 标签都调用本函数，二者取到同一组锚点。

    Args:
        close: 每根 K 线收盘价。
        p_entry: 每根 K 线的入场参考价（`p_1s` 或 `p_3s`）。
        atr_pct: 每根 K 线的 ATR 百分比。
        rv15: 每根 K 线的未标准化 15 分钟 1m 收益标准差。
        qv: 每根 K 线的成交额。
        tick_size: 价格步长。
        spec: 锚点组配置。
        use_numba: 是否使用 numba 版（测试比对时可传 False）。

    Returns:
        `AnchorSeries`；`valid[t]` 为 False 的行不能作为信号。

    Raises:
        ValueError: 输入数组长度不一致。
    """
    lengths = {
        int(close.shape[0]),
        int(p_entry.shape[0]),
        int(atr_pct.shape[0]),
        int(rv15.shape[0]),
        int(qv.shape[0]),
    }
    if len(lengths) != 1:
        raise ValueError(f"锚点输入数组长度不一致：{sorted(lengths)}（06 §3.4）")
    if not use_numba:
        return anchor_series_py(
            close=close,
            p_entry=p_entry,
            atr_pct=atr_pct,
            rv15=rv15,
            qv=qv,
            tick_size=tick_size,
            spec=spec,
        )
    values = _anchor_series_nb(
        close,
        p_entry,
        atr_pct,
        rv15,
        qv,
        tick_size,
        spec.mode,
        spec.rr,
        spec.tp_cap_r,
        spec.min_stop_move_r,
        spec.k_sl,
        spec.sl_min,
        spec.sl_max,
        spec.half_spread_min,
        spec.slip_min,
        spec.Y,
        spec.fixed_slip,
        spec.fee_taker,
        spec.notional_ref,
    )
    return AnchorSeries(
        down=values[0],
        up=values[1],
        p_fill=values[2],
        sl=values[3],
        r_dist=values[4],
        min_move=values[5],
        slip=values[6],
        slip_stop=values[7],
        half_spread=values[8],
        loss_rate=values[9],
        valid=values[10],
    )
