"""出场模拟标签 `exit_sim`（默认，04 §7.3）。

复用与回测完全相同的锚点、入场成本与出场状态机（`trade.common.anchors`、
`trade.common.exits`），不另写一套出场逻辑（AGENTS.md §6 红线 4；04 §7.3）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.common.anchors import anchor_series
from trade.common.batch import batch_exit
from trade.common.costs import net_return_after_fees
from trade.common.entry import chase_exceeded_array, half_spread_array, stale_entry_mask
from trade.common.exits import EXIT_TYPE_NAMES, ExitConfig, exit_anchor_spec
from trade.common.funding import funding_rate_between, funding_settlements
from trade.features.kline import rv_15, true_range, wilder_atr

# exit_sim 的 r_mult 截断区间（04 §7.3；triple_barrier 用 [-1.5, 3.0]，见 04 §7.4）
_R_MULT_CLIP = (-1.5, 4.0)

# labels 表列顺序（04 §7.3、08 §6）
_LABEL_COLUMNS = (
    "open_time",
    "y",
    "r_mult",
    "exit_type",
    "sl",
    "ret_net",
    "bars_held",
    "t_exit",
)


def _labels_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """按固定列顺序构造 labels 表（04 §7.3）。"""
    return pd.DataFrame(rows, columns=list(_LABEL_COLUMNS))


def _entry_columns(entry_latency_ms: int) -> tuple[str, str, str]:
    """入场延迟 → (入场价列, 入场后最高价列, 入场后最低价列)（03 §2.2）。"""
    if entry_latency_ms == 3000:
        return "p_3s", "hi_post_3s", "lo_post_3s"
    return "p_1s", "hi_post_1s", "lo_post_1s"


def exit_sim(
    bars: pd.DataFrame,
    sample_idx: np.ndarray,
    *,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    entry_latency_ms: int,
    y_min_r: float,
    exit_cfg: ExitConfig,
    fees_taker: float,
    fees_maker: float,
    half_spread_min: float,
    slip_min: float,
    Y: float,
    notional_ref: float,
    max_chase: float,
    tick_size: float,
    segments: np.ndarray | None = None,
) -> tuple[pd.DataFrame, np.ndarray]:
    """对样本做出场模拟标签，返回 (labels 表, 保留下来的样本 bar 位置)。

    逐笔出场由 `simulate_exit_batch` 一次推进（Python 参考实现为
    `trade.common.exits.simulate_exit(..., use_numba=False)`）；规格：04 §7.3、06 §3.4。
    """
    p_col, hi_col, lo_col = _entry_columns(entry_latency_ms)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    n = len(bars)
    close = bars["close"].to_numpy(dtype="float64")
    high = bars["high"].to_numpy(dtype="float64")
    low = bars["low"].to_numpy(dtype="float64")
    open_ = bars["open"].to_numpy(dtype="float64")
    p_entry = bars[p_col].to_numpy(dtype="float64")
    filled_col = "p_3s_filled" if entry_latency_ms == 3000 else "p_1s_filled"
    if filled_col not in bars:
        raise ValueError(f"bars 缺少列 {filled_col}（03 §2.2、06 §3.1）")
    entry_filled = bars[filled_col].to_numpy(dtype="int64")
    hi_post = bars[hi_col].to_numpy(dtype="float64")
    lo_post = bars[lo_col].to_numpy(dtype="float64")
    missing_cols = [name for name in ("kline_missing", "aggtrades_missing") if name not in bars]
    if missing_cols:
        raise ValueError(f"bars 缺少列 {missing_cols}（03 §2.3、红线 13）")
    kline_missing = bars["kline_missing"].to_numpy(dtype="int64")
    aggtrades_missing = bars["aggtrades_missing"].to_numpy(dtype="int64")
    qv = bars["quote_volume"].to_numpy(dtype="float64")

    atr_abs_arr = wilder_atr(true_range(high, low, close), 14)
    sigma_arr = atr_abs_arr / close
    rv15_arr = rv_15(close)
    fund_times, fund_rates = funding_settlements(bars)

    if segments is None:
        segments = np.zeros(n, dtype="int64")
    segments = np.asarray(segments, dtype="int64")

    H_max = exit_cfg.H_max if exit_cfg.type != "fixed" else H_label
    sample_idx = np.asarray(sample_idx, dtype="int64")

    rows: list[dict[str, object]] = []
    kept: list[int] = []
    if sample_idx.size == 0:
        return _labels_frame(rows), np.empty(0, dtype="int64")

    # 分段边界与样本合法性一次筛完（04 §7.3、06 §3.4）：跨段的样本不产生标签
    usable = sample_idx[(sample_idx >= 0) & (sample_idx + H_max < n)]
    if usable.size:
        usable = usable[segments[usable + H_max] == segments[usable]]
        p_raw_arr = p_entry[usable + 1]
        close_at = close[usable]
        # 开仓条件与回测同一份实现（06 §3.1 条件 1、5；红线 4）：陈旧价放弃、按卖一近似判追价
        keep_mask = (
            (kline_missing[usable + 1] == 0)
            & ~stale_entry_mask(entry_filled[usable + 1])
            & np.isfinite(p_raw_arr)
            & (p_raw_arr > 0.0)
            & np.isfinite(close_at)
            & (close_at > 0.0)
            & np.isfinite(sigma_arr[usable])
            & np.isfinite(rv15_arr[usable])
        )
        if keep_mask.any():
            chase = chase_exceeded_array(
                p_raw=p_raw_arr[keep_mask],
                half_spread=half_spread_array(close_at[keep_mask], tick_size, half_spread_min),
                close_t=close_at[keep_mask],
                max_chase=max_chase,
            )
            subset = keep_mask[keep_mask]
            subset[chase] = False
            keep_mask[keep_mask] = subset
        usable = usable[keep_mask]
    if usable.size == 0:
        return _labels_frame(rows), np.empty(0, dtype="int64")

    # 锚点与入场成本按币算一次，与回测 precompute_trades 取同一份（06 §3.4）
    anchors = anchor_series(
        close=close,
        p_entry=p_entry,
        atr_pct=sigma_arr,
        rv15=rv15_arr,
        qv=qv,
        tick_size=tick_size,
        spec=exit_anchor_spec(
            exit_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            half_spread_min=half_spread_min,
            slip_min=slip_min,
            Y=Y,
            fixed_slip=None,
            fee_taker=fees_taker,
            notional_ref=notional_ref,
        ),
    )
    batch = batch_exit(
        open_=open_,
        high=high,
        low=low,
        close=close,
        p_entry=p_entry,
        hi_post=hi_post,
        lo_post=lo_post,
        kline_missing=kline_missing,
        aggtrades_missing=aggtrades_missing,
        anchors=anchors,
        signal_bars=usable,
        atr_abs=atr_abs_arr,
        h_max=H_max,
        cfg=exit_cfg,
        tick_size=tick_size,
        fee_taker=fees_taker,
        fee_maker=fees_maker,
    )

    for slot in range(int(usable.shape[0])):
        t = int(usable[slot])
        p_fill = float(anchors.p_fill[t])
        hs = float(anchors.half_spread[t])
        slip_ = float(anchors.slip[t])
        sl_t = float(anchors.sl[t])
        exit_idx = int(batch.exit_idx[slot])
        exit_type_code = int(batch.exit_type[slot])
        exit_fee_rate = float(batch.fee_rate[slot])
        exit_price = float(batch.exit_price[slot])
        if not np.isfinite(exit_price) or exit_price <= 0.0:
            # 出场分钟缺 aggTrades（p_1s 为空）时按该根 K 线收盘价扣成本出场（06 §3.3）；
            # 与回测 precompute_trades 的同一回退保持一致，否则标签 r_mult 会是 NaN。
            exit_price = float(close[exit_idx]) * (1.0 - hs - slip_)
        fr_sum = funding_rate_between(
            fund_times, fund_rates, int(open_time[t + 1]), int(open_time[exit_idx])
        )
        ret_net = net_return_after_fees(
            gross_return=exit_price / p_fill - 1.0,
            entry_fee_rate=fees_taker,
            exit_fee_rate=exit_fee_rate,
            funding_rate=fr_sum,
        )
        r_net = ret_net / sl_t
        rows.append(
            {
                "open_time": int(open_time[t]),
                "y": int(r_net >= y_min_r),
                "r_mult": float(np.clip(r_net, _R_MULT_CLIP[0], _R_MULT_CLIP[1])),
                "exit_type": EXIT_TYPE_NAMES[exit_type_code],
                "sl": sl_t,
                "ret_net": ret_net,
                "bars_held": int(batch.bars_held[slot]),
                "t_exit": int(open_time[exit_idx]),
            }
        )
        kept.append(t)

    return _labels_frame(rows), np.asarray(kept, dtype="int64")
