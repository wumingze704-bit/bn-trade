"""批量出场内核入口（回测逐笔预计算与 `exit_sim` 标签共用）。

规格：docs/06-回测引擎.md §3.4；docs/04-特征与标签.md §7.3

同一币的全部候选共用 `trade.common.anchors.anchor_series` 算出的锚点数组；本模块把
「信号 K 线位置 → 该笔的锚点与入场成本切片」集中在一处，再交给
`trade.common.exits.simulate_exit_batch` 用一次 numba 调用推进状态机。回测与标签
走同一条路径，避免两处各写一份取样逻辑（AGENTS.md §6 红线 4）。
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from trade.common.anchors import AnchorSeries
from trade.common.exits import ExitBatch, ExitConfig, simulate_exit_batch


def batch_exit(
    *,
    open_: npt.NDArray[np.float64],
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    p_entry: npt.NDArray[np.float64],
    hi_post: npt.NDArray[np.float64],
    lo_post: npt.NDArray[np.float64],
    kline_missing: npt.NDArray[np.int64],
    aggtrades_missing: npt.NDArray[np.int64],
    anchors: AnchorSeries,
    signal_bars: npt.NDArray[np.int64],
    atr_abs: npt.NDArray[np.float64],
    h_max: int,
    cfg: ExitConfig,
    tick_size: float,
    fee_taker: float,
    fee_maker: float,
) -> ExitBatch:
    """按信号 K 线位置从锚点数组取样，批量推进出场状态机（06 §3.4）。

    Args:
        open_/high/low/close: 该币分钟 K 线数组。
        p_entry: 每根 K 线的入场参考价（p_1s 或 p_3s）。
        hi_post/lo_post: 每根 K 线开盘延迟后的最高/最低价。
        kline_missing/aggtrades_missing: K 线缺失与逐笔缺失标记（1=缺失，03 §2.3）。
        anchors: `anchor_series` 算出的锚点与入场成本数组。
        signal_bars: 每笔的信号 K 线位置 t（入场 K 线 = t+1）。
        atr_abs: 每根 K 线的 ATR14 绝对值（持仓期固定用）。
        h_max: 最长持仓根数。
        cfg: 出场策略配置。
        tick_size: 价格步长。
        fee_taker/fee_maker: 吃单/挂单费率。

    Returns:
        `ExitBatch`，长度等于 `signal_bars`。

    Raises:
        ValueError: 信号位置为空数组以外的越界情形（红线 13）。
    """
    slots = np.asarray(signal_bars, dtype="int64")
    return simulate_exit_batch(
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        kline_missing,
        aggtrades_missing,
        t_entries=slots + 1,
        down=anchors.down[slots],
        up=anchors.up[slots],
        r_dist=anchors.r_dist[slots],
        min_move=anchors.min_move[slots],
        p_fill=anchors.p_fill[slots],
        atr_abs=atr_abs[slots],
        slip_stop=anchors.slip_stop[slots],
        slip_exit=anchors.slip[slots],
        half_spread=anchors.half_spread[slots],
        H_max=h_max,
        cfg=cfg,
        tick_size=tick_size,
        fee_taker=fee_taker,
        fee_maker=fee_maker,
    )
