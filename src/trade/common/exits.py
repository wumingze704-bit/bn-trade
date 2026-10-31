"""出场策略状态机（回测与实盘共用）。

规格：docs/06-回测引擎.md §3.3
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

from trade.common._numba import njit

# 出场类型编码（06 §6：初始止损/保本/追踪/封顶止盈/无进展/超时；eod 为数据末尾实现约定）
_EXIT_INITIAL_STOP = 0
_EXIT_BREAKEVEN = 1
_EXIT_TRAIL = 2
_EXIT_TP = 3
_EXIT_STALL = 4
_EXIT_TIMEOUT = 5
_EXIT_EOD = 6

EXIT_TYPE_NAMES = {
    _EXIT_INITIAL_STOP: "initial_stop",
    _EXIT_BREAKEVEN: "breakeven",
    _EXIT_TRAIL: "trail",
    _EXIT_TP: "tp",
    _EXIT_STALL: "stall",
    _EXIT_TIMEOUT: "timeout",
    _EXIT_EOD: "eod",
}

# 出场模式编码（06 §3.3）
_MODE_FIXED = 0
_MODE_TRAIL = 1


class ExitType(IntEnum):
    """出场类型（06 §3.3、§6）。"""

    INITIAL_STOP = _EXIT_INITIAL_STOP
    BREAKEVEN = _EXIT_BREAKEVEN
    TRAIL = _EXIT_TRAIL
    TP = _EXIT_TP
    STALL = _EXIT_STALL
    TIMEOUT = _EXIT_TIMEOUT
    EOD = _EXIT_EOD


class ExitConfig(BaseModel):
    """configs/exits.yaml 的 pydantic 模型（06 §3.3）。"""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^(trail|fixed|partial)$")
    be_trigger_r: float = Field(default=1.0, gt=0.0)
    be_offset: float = Field(default=0.0015, ge=0.0)
    trail_trigger_r: float = Field(default=1.5, gt=0.0)
    k_trail: float = Field(default=1.5, gt=0.0)
    tp_cap_r: float = Field(default=4.0, gt=0.0)
    stall_bars: int = Field(default=20, ge=1)
    stall_r: float = Field(default=0.5, gt=0.0)
    H_max: int = Field(default=120, ge=1)
    tp_penetration: float = Field(default=0.0001, ge=0.0)
    min_stop_move_r: float = Field(default=0.1, ge=0.0)
    rr: float = Field(default=2.0, gt=0.0)


def exit_policy_hash(cfg: ExitConfig) -> str:
    """对出场策略配置求 SHA256（06 §3.3）。

    出场参数改变即视为新数据集（04 §8、05 §6.1）：`exit_sim` 标签、回测与实盘
    使用同一份出场策略，构建数据集时把该哈希写入 manifest，供下游校验一致性。

    Args:
        cfg: 出场配置（configs/exits.yaml 的 pydantic 模型）。

    Returns:
        配置字段的 SHA256 十六进制字符串。
    """
    canonical = json.dumps(
        cfg.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExitResult:
    """单笔出场的预计算结果（06 §3.4）。"""

    exit_idx: int  # 出场 K 线在数组中的位置
    exit_price: float  # 出场价（含止损滑点/离场滑点，见 06 §3.3）
    exit_type: str  # 出场类型名（EXIT_TYPE_NAMES）
    exit_type_code: int
    stop_updates: int  # 止损上移次数
    mfe_r: float  # 最大有利波动（R）
    mae_r: float  # 最大不利波动（R）
    bars_held: int  # 持有根数（= exit_idx - t）
    data_gaps: int  # 持仓期补齐分钟数
    fee_rate: float  # 出场适用费率（吃单/挂单）


@njit
def _floor_tick(x: float, tick: float) -> float:
    """价格向下取整到 tickSize（06 §3.2）。"""
    return math.floor(x / tick + 1e-9) * tick


@njit
def _ceil_tick(x: float, tick: float) -> float:
    """价格向上取整到 tickSize（限价止盈卖出向上取整，保守）。"""
    return math.ceil(x / tick - 1e-9) * tick


@njit
def _simulate_exit_nb(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    t_entry: int,
    p_fill: float,
    sl: float,
    rr: float,
    atr_abs: float,
    tick_size: float,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    tp_cap_r: float,
    stall_bars: int,
    stall_r: float,
    H_max: int,
    tp_penetration: float,
    min_stop_move_r: float,
    slip_stop: float,
    slip_exit: float,
    half_spread: float,
    fee_taker: float,
    fee_maker: float,
) -> tuple[int, float, int, int, float, float, int, int, float]:
    """numba 版单笔出场状态机（06 §3.3），与纯 Python 参考逐字段一致。"""
    n = open_.shape[0]
    R = p_fill * sl
    stop = _floor_tick(p_fill * (1.0 - sl), tick_size)
    if mode == _MODE_FIXED:
        cap = _ceil_tick(p_fill * (1.0 + rr * sl), tick_size)
    else:
        cap = _ceil_tick(p_fill + tp_cap_r * R, tick_size)

    hh = -np.inf
    min_lo = np.inf
    phase = _EXIT_INITIAL_STOP
    made_progress = False
    stop_updates = 0
    data_gaps = 0
    min_move = tick_size
    if min_stop_move_r * R > min_move:
        min_move = min_stop_move_r * R

    exit_idx = -1
    exit_price = np.nan
    exit_type = _EXIT_EOD
    fee_rate = fee_taker

    j = t_entry
    exited = False
    while j < t_entry + H_max and not exited:
        if j >= n:
            exit_idx = n - 1
            exit_price = close[n - 1]
            exit_type = _EXIT_EOD
            fee_rate = fee_taker
            exited = True
            break
        if is_filled[j] == 1:
            # 补齐分钟不判定（06 §3.3），但时间仍在推进
            data_gaps += 1
            j += 1
            continue
        if j == t_entry:
            lo = lo_post[j]
            hi = hi_post[j]
        else:
            lo = low[j]
            hi = high[j]
        if hi > hh:
            hh = hi
        if lo < min_lo:
            min_lo = lo
        # 同根同时触发时先判止损（06 §3.3 保守假设）
        if lo <= stop:
            if j == t_entry:
                exit_price = max(lo_post[j], stop * (1.0 - slip_stop))
            else:
                ref = stop
                if open_[j] < stop:
                    ref = open_[j]  # 跳空（06 §3.3）
                exit_price = max(low[j], ref * (1.0 - slip_stop))
            exit_idx = j
            exit_type = phase
            fee_rate = fee_taker
            exited = True
            break
        if hi >= cap * (1.0 + tp_penetration):
            exit_idx = j
            exit_price = cap
            exit_type = _EXIT_TP
            fee_rate = fee_maker
            exited = True
            break
        # 收盘更新状态机，下一根生效（06 §3.3）
        if mode == _MODE_TRAIL:
            if hh >= p_fill + be_trigger_r * R:
                new_stop = _floor_tick(p_fill * (1.0 + be_offset), tick_size)
                if new_stop - stop >= min_move:
                    stop = new_stop
                    stop_updates += 1
                phase = _EXIT_BREAKEVEN
            if hh >= p_fill + trail_trigger_r * R:
                new_stop = _floor_tick(hh - k_trail * atr_abs, tick_size)
                if new_stop - stop >= min_move:
                    stop = new_stop
                    stop_updates += 1
                phase = _EXIT_TRAIL
            if j - t_entry + 1 <= stall_bars:
                if hh >= p_fill + stall_r * R:
                    made_progress = True
                if j - t_entry + 1 == stall_bars and not made_progress:
                    exit_idx = j + 1
                    exit_type = _EXIT_STALL
                    fee_rate = fee_taker
                    exited = True
                    break
        j += 1

    if not exited:
        # 到达 H_max：下一根开盘离场（06 §3.3）
        exit_idx = t_entry + H_max
        exit_type = _EXIT_TIMEOUT
        fee_rate = fee_taker

    # 无进展/超时：下一根开盘离场价（06 §3.3）
    if exit_type in (_EXIT_STALL, _EXIT_TIMEOUT):
        if exit_idx < n:
            exit_price = p_entry[exit_idx] * (1.0 - half_spread - slip_exit)
        else:
            exit_idx = n - 1
            exit_price = close[n - 1]
            exit_type = _EXIT_EOD
            fee_rate = fee_taker

    bars_held = exit_idx - t_entry + 1
    mfe_r = (hh - p_fill) / R if np.isfinite(hh) else 0.0
    mae_r = (p_fill - min_lo) / R if np.isfinite(min_lo) else 0.0
    return (
        exit_idx,
        exit_price,
        exit_type,
        stop_updates,
        mfe_r,
        mae_r,
        bars_held,
        data_gaps,
        fee_rate,
    )


def simulate_exit(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    *,
    t_entry: int,
    p_fill: float,
    sl: float,
    atr_abs: float,
    tick_size: float,
    H_max: int,
    cfg: ExitConfig,
    slip_stop: float,
    slip_exit: float,
    half_spread: float,
    fee_taker: float,
    fee_maker: float,
    use_numba: bool = True,
) -> ExitResult:
    """计算单笔交易的出场结果（06 §3.3）。

    状态只在 K 线收盘时更新、下一根生效；同根触发时按止损优先。

    Args:
        open_/high/low/close: 该币分钟 K 线数组。
        p_entry: 每根 K 线的入场参考价（p_1s 或 p_3s）。
        hi_post/lo_post: 每根 K 线开盘延迟后的最高/最低价。
        is_filled: 补齐分钟标记（1=补齐，不判定）。
        t_entry: 入场 K 线位置（= 信号 t + 1）。
        p_fill: 开仓成交价（已含点差与入场滑点）。
        sl: 止损幅度（比例）。
        atr_abs: 入场时刻 ATR14 绝对值（持仓期固定，06 §3.3）。
        tick_size: 价格步长（用于止损/止盈取整）。
        H_max: 最长持仓根数（trail 用 cfg.H_max；fixed 用 label.H_label）。
        cfg: 出场策略配置。
        slip_stop/slip_exit/half_spread: 离场滑点与点差（06 §3.3、§4.2）。
        fee_taker/fee_maker: 吃单/挂单费率（base.yaml）。
        use_numba: 是否使用 numba 版（测试比对时可传 False）。

    Returns:
        ExitResult，含出场时间/价格/类型/止损更新次数/MFE/MAE 等。
    """
    if cfg.type == "partial":
        raise ValueError("partial 出场预留，P3 不实现（06 §3.3）")
    mode = _MODE_FIXED if cfg.type == "fixed" else _MODE_TRAIL
    args = (
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        is_filled,
        t_entry,
        p_fill,
        sl,
        cfg.rr,
        atr_abs,
        tick_size,
        mode,
        cfg.be_trigger_r,
        cfg.be_offset,
        cfg.trail_trigger_r,
        cfg.k_trail,
        cfg.tp_cap_r,
        cfg.stall_bars,
        cfg.stall_r,
        H_max,
        cfg.tp_penetration,
        cfg.min_stop_move_r,
        slip_stop,
        slip_exit,
        half_spread,
        fee_taker,
        fee_maker,
    )
    result = _simulate_exit_nb(*args) if use_numba else _simulate_exit_py(*args)
    (
        exit_idx,
        exit_price,
        exit_type,
        stop_updates,
        mfe_r,
        mae_r,
        bars_held,
        data_gaps,
        fee_rate,
    ) = result
    code = int(exit_type)
    return ExitResult(
        exit_idx=int(exit_idx),
        exit_price=float(exit_price),
        exit_type=EXIT_TYPE_NAMES[code],
        exit_type_code=code,
        stop_updates=int(stop_updates),
        mfe_r=float(mfe_r),
        mae_r=float(mae_r),
        bars_held=int(bars_held),
        data_gaps=int(data_gaps),
        fee_rate=float(fee_rate),
    )


def _simulate_exit_py(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    t_entry: int,
    p_fill: float,
    sl: float,
    rr: float,
    atr_abs: float,
    tick_size: float,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    tp_cap_r: float,
    stall_bars: int,
    stall_r: float,
    H_max: int,
    tp_penetration: float,
    min_stop_move_r: float,
    slip_stop: float,
    slip_exit: float,
    half_spread: float,
    fee_taker: float,
    fee_maker: float,
) -> tuple[int, float, int, int, float, float, int, int, float]:
    """纯 Python 参考实现（与 numba 版逐字段一致，AGENTS.md §6）。"""
    n = open_.shape[0]
    R = p_fill * sl
    stop = math.floor(p_fill * (1.0 - sl) / tick_size + 1e-9) * tick_size
    if mode == _MODE_FIXED:
        cap = math.ceil(p_fill * (1.0 + rr * sl) / tick_size - 1e-9) * tick_size
    else:
        cap = math.ceil((p_fill + tp_cap_r * R) / tick_size - 1e-9) * tick_size

    hh = float("-inf")
    min_lo = float("inf")
    phase = _EXIT_INITIAL_STOP
    made_progress = False
    stop_updates = 0
    data_gaps = 0
    min_move = max(tick_size, min_stop_move_r * R)

    exit_idx = -1
    exit_price = float("nan")
    exit_type = _EXIT_EOD
    fee_rate = fee_taker

    j = t_entry
    exited = False
    while j < t_entry + H_max and not exited:
        if j >= n:
            exit_idx = n - 1
            exit_price = float(close[n - 1])
            exit_type = _EXIT_EOD
            fee_rate = fee_taker
            exited = True
            break
        if is_filled[j] == 1:
            data_gaps += 1
            j += 1
            continue
        if j == t_entry:
            lo = float(lo_post[j])
            hi = float(hi_post[j])
        else:
            lo = float(low[j])
            hi = float(high[j])
        if hi > hh:
            hh = hi
        if lo < min_lo:
            min_lo = lo
        if lo <= stop:
            if j == t_entry:
                exit_price = max(float(lo_post[j]), stop * (1.0 - slip_stop))
            else:
                ref = stop if float(open_[j]) >= stop else float(open_[j])
                exit_price = max(float(low[j]), ref * (1.0 - slip_stop))
            exit_idx = j
            exit_type = phase
            fee_rate = fee_taker
            exited = True
            break
        if hi >= cap * (1.0 + tp_penetration):
            exit_idx = j
            exit_price = cap
            exit_type = _EXIT_TP
            fee_rate = fee_maker
            exited = True
            break
        if mode == _MODE_TRAIL:
            if hh >= p_fill + be_trigger_r * R:
                new_stop = math.floor(p_fill * (1.0 + be_offset) / tick_size + 1e-9) * tick_size
                if new_stop - stop >= min_move:
                    stop = new_stop
                    stop_updates += 1
                phase = _EXIT_BREAKEVEN
            if hh >= p_fill + trail_trigger_r * R:
                new_stop = math.floor((hh - k_trail * atr_abs) / tick_size + 1e-9) * tick_size
                if new_stop - stop >= min_move:
                    stop = new_stop
                    stop_updates += 1
                phase = _EXIT_TRAIL
            if j - t_entry + 1 <= stall_bars:
                if hh >= p_fill + stall_r * R:
                    made_progress = True
                if j - t_entry + 1 == stall_bars and not made_progress:
                    exit_idx = j + 1
                    exit_type = _EXIT_STALL
                    fee_rate = fee_taker
                    exited = True
                    break
        j += 1

    if not exited:
        exit_idx = t_entry + H_max
        exit_type = _EXIT_TIMEOUT
        fee_rate = fee_taker

    if exit_type in (_EXIT_STALL, _EXIT_TIMEOUT):
        if exit_idx < n:
            exit_price = float(p_entry[exit_idx]) * (1.0 - half_spread - slip_exit)
        else:
            exit_idx = n - 1
            exit_price = float(close[n - 1])
            exit_type = _EXIT_EOD
            fee_rate = fee_taker

    bars_held = exit_idx - t_entry + 1
    mfe_r = (hh - p_fill) / R if math.isfinite(hh) else 0.0
    mae_r = (p_fill - min_lo) / R if math.isfinite(min_lo) else 0.0
    return (
        exit_idx,
        exit_price,
        exit_type,
        stop_updates,
        mfe_r,
        mae_r,
        bars_held,
        data_gaps,
        fee_rate,
    )
