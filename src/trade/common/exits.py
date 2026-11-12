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
from trade.common.anchors import (
    AnchorSpec,
    trade_anchors_nb,
    trade_anchors_py,
)

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
def floor_tick(x: float, tick: float) -> float:
    """价格向下取整到 tickSize（06 §3.2）。"""
    return math.floor(x / tick + 1e-9) * tick


@njit
def ceil_tick(x: float, tick: float) -> float:
    """价格向上取整到 tickSize（限价止盈卖出向上取整，保守）。"""
    return math.ceil(x / tick - 1e-9) * tick


def initial_stop(p_fill: float, sl: float, tick_size: float) -> float:
    """初始止损价 `D0`（06 §3.2）：`P_fill × (1 − sl)` 向下取整到 tickSize。"""
    return float(
        trade_anchors_py(
            p_fill=p_fill,
            sl=sl,
            tick_size=tick_size,
            mode=_MODE_TRAIL,
            rr=0.0,
            tp_cap_r=0.0,
            min_stop_move_r=0.0,
        )[0]
    )


def take_profit_price(p_fill: float, sl: float, cfg: ExitConfig, tick_size: float) -> float:
    """封顶止盈价 `cap`（06 §3.3）。

    `trail` 用 `P_fill + tp_cap_r × R`，`fixed` 用 `P_fill × (1 + rr × sl)`，
    两者都向上取整到 tickSize。
    """
    return float(
        trade_anchors_py(
            p_fill=p_fill,
            sl=sl,
            tick_size=tick_size,
            mode=exit_mode(cfg),
            rr=cfg.rr,
            tp_cap_r=cfg.tp_cap_r,
            min_stop_move_r=cfg.min_stop_move_r,
        )[1]
    )


def exit_mode(cfg: ExitConfig) -> int:
    """出场模式编码（0 = `fixed`，1 = `trail`），供 S8 逐笔重放选择同一套更新规则。"""
    return _MODE_FIXED if cfg.type == "fixed" else _MODE_TRAIL


def exit_anchor_spec(
    cfg: ExitConfig,
    *,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    half_spread_min: float,
    slip_min: float,
    Y: float,
    fixed_slip: float | None,
    fee_taker: float,
    notional_ref: float,
) -> AnchorSpec:
    """由出场配置与成本参数构造锚点组配置（06 §3.3、§3.4）。

    Args:
        cfg: 出场配置。
        k_sl/sl_min/sl_max: 止损 ATR 系数与裁剪区间（04 §7.2）。
        half_spread_min: 最小半价差（已按情景放大）。
        slip_min: 最小单边滑点（已按情景放大）。
        Y: 平方根冲击系数。
        fixed_slip: 固定单边滑点；None 表示动态滑点。
        fee_taker: 吃单费率。
        notional_ref: 参考名义价值（06 §4.2）。

    Returns:
        供 `trade.common.anchors.anchor_series` 使用的组配置。
    """
    return AnchorSpec(
        mode=exit_mode(cfg),
        rr=cfg.rr,
        tp_cap_r=cfg.tp_cap_r,
        min_stop_move_r=cfg.min_stop_move_r,
        k_sl=k_sl,
        sl_min=sl_min,
        sl_max=sl_max,
        half_spread_min=half_spread_min,
        slip_min=slip_min,
        Y=Y,
        fixed_slip=float("nan") if fixed_slip is None else fixed_slip,
        fee_taker=fee_taker,
        notional_ref=notional_ref,
    )


def min_stop_move(tick_size: float, min_stop_move_r: float, r_dist: float) -> float:
    """最小止损上移幅度 `max(1 tick, min_stop_move_r × R)`（06 §3.3）。"""
    return max(tick_size, min_stop_move_r * r_dist)


def update_stop_after_bar(
    stop: float,
    phase: int,
    hh: float,
    p_fill: float,
    r_dist: float,
    atr_abs: float,
    tick_size: float,
    min_move: float,
    held_bars: int,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    stall_bars: int,
    stall_r: float,
) -> tuple[float, int, int, bool]:
    """K 线收盘后更新止损价与出场阶段（06 §3.3）。

    保本与追踪在同一根收盘时按顺序判断：先保本、再追踪；止损只上移，新止损与旧止损
    相差不足 `min_move` 时不更新。K 线级 Python 参考实现与 S8 逐笔重放共用本函数
    （AGENTS.md §6 红线 4），numba 版 `_update_stop_after_bar_nb` 与它逐字段一致。

    Args:
        stop: 当前生效止损价（上一根收盘时的值）。
        phase: 当前出场阶段编码（0 初始止损 / 1 保本 / 2 追踪）。
        hh: 入场以来已收盘部分的最高价（入场 K 线用入场后高低点）。
        p_fill: 开仓成交价。
        r_dist: 1R 的价格距离 `P_fill × sl`。
        atr_abs: 入场时刻 ATR14 绝对值（持仓期固定）。
        tick_size: 价格步长。
        min_move: 最小止损上移幅度，见 `min_stop_move`。
        held_bars: 已持有根数（含当前根，从 1 开始）。
        mode: 出场模式编码，见 `exit_mode`。
        be_trigger_r/be_offset/trail_trigger_r/k_trail/stall_bars/stall_r: 出场参数。

    Returns:
        `(止损价, 出场阶段, 本次上移次数, 是否已达成“有进展”)`。
    """
    updates = 0
    made_progress = False
    if mode == _MODE_TRAIL:
        if hh >= p_fill + be_trigger_r * r_dist:
            new_stop = float(floor_tick(p_fill * (1.0 + be_offset), tick_size))
            if new_stop - stop >= min_move:
                stop = new_stop
                updates += 1
            phase = _EXIT_BREAKEVEN
        if hh >= p_fill + trail_trigger_r * r_dist:
            new_stop = float(floor_tick(hh - k_trail * atr_abs, tick_size))
            if new_stop - stop >= min_move:
                stop = new_stop
                updates += 1
            phase = _EXIT_TRAIL
        if held_bars <= stall_bars and hh >= p_fill + stall_r * r_dist:
            made_progress = True
    return stop, phase, updates, made_progress


@njit
def _update_stop_after_bar_nb(
    stop: float,
    phase: int,
    hh: float,
    p_fill: float,
    r_dist: float,
    atr_abs: float,
    tick_size: float,
    min_move: float,
    held_bars: int,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    stall_bars: int,
    stall_r: float,
) -> tuple[float, int, int, bool]:
    """numba 版收盘状态机更新，与 `update_stop_after_bar` 逐字段一致（06 §3.3）。"""
    updates = 0
    made_progress = False
    if mode == _MODE_TRAIL:
        if hh >= p_fill + be_trigger_r * r_dist:
            new_stop = floor_tick(p_fill * (1.0 + be_offset), tick_size)
            if new_stop - stop >= min_move:
                stop = new_stop
                updates += 1
            phase = _EXIT_BREAKEVEN
        if hh >= p_fill + trail_trigger_r * r_dist:
            new_stop = floor_tick(hh - k_trail * atr_abs, tick_size)
            if new_stop - stop >= min_move:
                stop = new_stop
                updates += 1
            phase = _EXIT_TRAIL
        if held_bars <= stall_bars and hh >= p_fill + stall_r * r_dist:
            made_progress = True
    return stop, phase, updates, made_progress


@njit
def _exit_machine_nb(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    t_entry: int,
    stop: float,
    cap: float,
    p_fill: float,
    r_dist: float,
    min_move: float,
    atr_abs: float,
    tick_size: float,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    stall_bars: int,
    stall_r: float,
    H_max: int,
    tp_penetration: float,
    slip_stop: float,
    slip_exit: float,
    half_spread: float,
    fee_taker: float,
    fee_maker: float,
) -> tuple[int, float, int, int, float, float, int, int, float]:
    """numba 版逐笔出场状态机（06 §3.3）：锚点由调用方算好，本函数只推进状态。"""
    n = open_.shape[0]
    R = r_dist

    hh = -np.inf
    min_lo = np.inf
    phase = _EXIT_INITIAL_STOP
    made_progress = False
    stop_updates = 0
    data_gaps = 0
    if min_move < tick_size:  # 锚点已取 max(1 tick, min_stop_move_r × R)，此处仅防御（06 §3.3）
        min_move = tick_size

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
        stop, phase, updates, progress_now = _update_stop_after_bar_nb(
            stop,
            phase,
            hh,
            p_fill,
            R,
            atr_abs,
            tick_size,
            min_move,
            j - t_entry + 1,
            mode,
            be_trigger_r,
            be_offset,
            trail_trigger_r,
            k_trail,
            stall_bars,
            stall_r,
        )
        stop_updates += updates
        made_progress = made_progress or progress_now
        if mode == _MODE_TRAIL and j - t_entry + 1 == stall_bars and not made_progress:
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
    """numba 版单笔出场：先按锚点算 D0/cap，再推进状态机（06 §3.3）。"""
    stop, cap, r_dist, min_move = trade_anchors_nb(
        p_fill, sl, tick_size, mode, rr, tp_cap_r, min_stop_move_r
    )
    return _exit_machine_nb(
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        is_filled,
        t_entry,
        stop,
        cap,
        p_fill,
        r_dist,
        min_move,
        atr_abs,
        tick_size,
        mode,
        be_trigger_r,
        be_offset,
        trail_trigger_r,
        k_trail,
        stall_bars,
        stall_r,
        H_max,
        tp_penetration,
        slip_stop,
        slip_exit,
        half_spread,
        fee_taker,
        fee_maker,
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


@dataclass(frozen=True)
class ExitBatch:
    """一批交易的出场结果数组（06 §3.4），字段与 `ExitResult` 一一对应。"""

    exit_idx: npt.NDArray[np.int64]
    exit_price: npt.NDArray[np.float64]
    exit_type: npt.NDArray[np.int64]
    stop_updates: npt.NDArray[np.int64]
    mfe_r: npt.NDArray[np.float64]
    mae_r: npt.NDArray[np.float64]
    bars_held: npt.NDArray[np.int64]
    data_gaps: npt.NDArray[np.int64]
    fee_rate: npt.NDArray[np.float64]

    def __len__(self) -> int:
        """批内交易笔数。"""
        return int(self.exit_idx.shape[0])

    def result(self, index: int) -> ExitResult:
        """取第 index 笔的 `ExitResult`（与逐笔调用 `simulate_exit` 的返回值一致）。"""
        code = int(self.exit_type[index])
        return ExitResult(
            exit_idx=int(self.exit_idx[index]),
            exit_price=float(self.exit_price[index]),
            exit_type=EXIT_TYPE_NAMES[code],
            exit_type_code=code,
            stop_updates=int(self.stop_updates[index]),
            mfe_r=float(self.mfe_r[index]),
            mae_r=float(self.mae_r[index]),
            bars_held=int(self.bars_held[index]),
            data_gaps=int(self.data_gaps[index]),
            fee_rate=float(self.fee_rate[index]),
        )


@njit
def _simulate_exit_batch_nb(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    t_entries: npt.NDArray[np.int64],
    down: npt.NDArray[np.float64],
    up: npt.NDArray[np.float64],
    r_dist: npt.NDArray[np.float64],
    min_move: npt.NDArray[np.float64],
    p_fill: npt.NDArray[np.float64],
    atr_abs: npt.NDArray[np.float64],
    slip_stop: npt.NDArray[np.float64],
    slip_exit: npt.NDArray[np.float64],
    half_spread: npt.NDArray[np.float64],
    tick_size: float,
    mode: int,
    be_trigger_r: float,
    be_offset: float,
    trail_trigger_r: float,
    k_trail: float,
    stall_bars: int,
    stall_r: float,
    H_max: int,
    tp_penetration: float,
    fee_taker: float,
    fee_maker: float,
) -> tuple[
    npt.NDArray[np.int64],
    npt.NDArray[np.float64],
    npt.NDArray[np.int64],
    npt.NDArray[np.int64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.int64],
    npt.NDArray[np.int64],
    npt.NDArray[np.float64],
]:
    """一次 numba 调用推进同一币的一批交易（06 §3.4 micro-batch）。

    锚点与入场成本由 `trade.common.anchors.anchor_series` 预先算好，本函数只做
    状态机推进，逐笔结果与 `simulate_exit` 逐字段一致。
    """
    count = t_entries.shape[0]
    exit_idx = np.zeros(count, dtype=np.int64)
    exit_price = np.zeros(count, dtype=np.float64)
    exit_type = np.zeros(count, dtype=np.int64)
    stop_updates = np.zeros(count, dtype=np.int64)
    mfe_r = np.zeros(count, dtype=np.float64)
    mae_r = np.zeros(count, dtype=np.float64)
    bars_held = np.zeros(count, dtype=np.int64)
    data_gaps = np.zeros(count, dtype=np.int64)
    fee_rate = np.zeros(count, dtype=np.float64)
    for k in range(count):
        values = _exit_machine_nb(
            open_,
            high,
            low,
            close,
            p_entry,
            hi_post,
            lo_post,
            is_filled,
            int(t_entries[k]),
            down[k],
            up[k],
            p_fill[k],
            r_dist[k],
            min_move[k],
            atr_abs[k],
            tick_size,
            mode,
            be_trigger_r,
            be_offset,
            trail_trigger_r,
            k_trail,
            stall_bars,
            stall_r,
            H_max,
            tp_penetration,
            slip_stop[k],
            slip_exit[k],
            half_spread[k],
            fee_taker,
            fee_maker,
        )
        exit_idx[k] = values[0]
        exit_price[k] = values[1]
        exit_type[k] = values[2]
        stop_updates[k] = values[3]
        mfe_r[k] = values[4]
        mae_r[k] = values[5]
        bars_held[k] = values[6]
        data_gaps[k] = values[7]
        fee_rate[k] = values[8]
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


def simulate_exit_batch(
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    is_filled: npt.NDArray[np.int64],
    *,
    t_entries: npt.NDArray[np.int64],
    down: npt.NDArray[np.float64],
    up: npt.NDArray[np.float64],
    r_dist: npt.NDArray[np.float64],
    min_move: npt.NDArray[np.float64],
    p_fill: npt.NDArray[np.float64],
    atr_abs: npt.NDArray[np.float64],
    slip_stop: npt.NDArray[np.float64],
    slip_exit: npt.NDArray[np.float64],
    half_spread: npt.NDArray[np.float64],
    H_max: int,
    cfg: ExitConfig,
    tick_size: float,
    fee_taker: float,
    fee_maker: float,
) -> ExitBatch:
    """一次调用算完同一币的一批交易出场（06 §3.4 micro-batch）。

    锚点与入场成本来自 `trade.common.anchors.anchor_series`（同一根 K 线的入场成本
    只算一次）；逐笔结果与 `simulate_exit(..., use_numba=False)` 逐字段一致。

    Args:
        open_/high/low/close: 该币分钟 K 线数组。
        p_entry: 每根 K 线的入场参考价（p_1s 或 p_3s）。
        hi_post/lo_post: 每根 K 线开盘延迟后的最高/最低价。
        is_filled: 补齐分钟标记（1=补齐，不判定）。
        t_entries: 每笔的入场 K 线位置（= 信号 t + 1）。
        down/up/r_dist/min_move: 每笔的初始止损价、封顶止盈价、1R 距离、最小上移幅度。
        p_fill/atr_abs/slip_stop/slip_exit/half_spread: 每笔的入场成本与滑点。
        H_max: 最长持仓根数。
        cfg: 出场策略配置。
        tick_size: 价格步长（止损 / 止盈取整用）。
        fee_taker/fee_maker: 吃单/挂单费率。

    Returns:
        `ExitBatch`，长度等于 `t_entries`。

    Raises:
        ValueError: 每笔数组长度不一致，或入场位置越界（红线 13）。
    """
    if cfg.type == "partial":
        raise ValueError("partial 出场预留，P3 不实现（06 §3.3）")
    count = int(t_entries.shape[0])
    for name, array in (
        ("down", down),
        ("up", up),
        ("r_dist", r_dist),
        ("min_move", min_move),
        ("p_fill", p_fill),
        ("atr_abs", atr_abs),
        ("slip_stop", slip_stop),
        ("slip_exit", slip_exit),
        ("half_spread", half_spread),
    ):
        if int(array.shape[0]) != count:
            raise ValueError(
                f"simulate_exit_batch 的 {name} 长度 {int(array.shape[0])} 与 "
                f"t_entries 长度 {count} 不一致（06 §3.4）"
            )
    n = int(open_.shape[0])
    if count and (int(t_entries.min()) < 0 or int(t_entries.max()) >= n):
        raise ValueError(f"simulate_exit_batch 的 t_entries 越界（bars 行数 {n}）")
    values = _simulate_exit_batch_nb(
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        is_filled,
        t_entries,
        down,
        up,
        r_dist,
        min_move,
        p_fill,
        atr_abs,
        slip_stop,
        slip_exit,
        half_spread,
        tick_size,
        exit_mode(cfg),
        cfg.be_trigger_r,
        cfg.be_offset,
        cfg.trail_trigger_r,
        cfg.k_trail,
        cfg.stall_bars,
        cfg.stall_r,
        H_max,
        cfg.tp_penetration,
        fee_taker,
        fee_maker,
    )
    return ExitBatch(
        exit_idx=values[0],
        exit_price=values[1],
        exit_type=values[2],
        stop_updates=values[3],
        mfe_r=values[4],
        mae_r=values[5],
        bars_held=values[6],
        data_gaps=values[7],
        fee_rate=values[8],
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
    stop, cap, R, min_move = trade_anchors_py(
        p_fill=p_fill,
        sl=sl,
        tick_size=tick_size,
        mode=mode,
        rr=rr,
        tp_cap_r=tp_cap_r,
        min_stop_move_r=min_stop_move_r,
    )

    hh = float("-inf")
    min_lo = float("inf")
    phase = _EXIT_INITIAL_STOP
    made_progress = False
    stop_updates = 0
    data_gaps = 0

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
        stop, phase, updates, progress_now = update_stop_after_bar(
            stop,
            phase,
            hh,
            p_fill,
            R,
            atr_abs,
            tick_size,
            min_move,
            j - t_entry + 1,
            mode,
            be_trigger_r,
            be_offset,
            trail_trigger_r,
            k_trail,
            stall_bars,
            stall_r,
        )
        stop_updates += updates
        made_progress = made_progress or progress_now
        if mode == _MODE_TRAIL and j - t_entry + 1 == stall_bars and not made_progress:
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
