"""出场模拟标签 `exit_sim`（默认，04 §7.5）。

复用与回测完全相同的入场价、成本与 `trade.common.exits.simulate_exit`，不另写一套
出场逻辑（AGENTS.md §6 红线 4；04 §7.5）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.common.entry import compute_entry
from trade.common.exits import ExitConfig, simulate_exit
from trade.common.funding import funding_rate_between, funding_settlements
from trade.features.kline import rv_15, true_range, wilder_atr

# exit_sim 的 r_mult 截断区间（04 §7.5；triple_barrier 用 [-1.5, 3.0]，见 04 §7.2）
_R_MULT_CLIP = (-1.5, 4.0)


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
    use_numba: bool = True,
) -> tuple[pd.DataFrame, np.ndarray]:
    """对样本做出场模拟标签，返回 (labels 表, 保留下来的样本 bar 位置)。

    规格：docs/04-特征与标签.md §7.5。
    """
    p_col, hi_col, lo_col = _entry_columns(entry_latency_ms)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    n = len(bars)
    close = bars["close"].to_numpy(dtype="float64")
    high = bars["high"].to_numpy(dtype="float64")
    low = bars["low"].to_numpy(dtype="float64")
    open_ = bars["open"].to_numpy(dtype="float64")
    p_entry = bars[p_col].to_numpy(dtype="float64")
    hi_post = bars[hi_col].to_numpy(dtype="float64")
    lo_post = bars[lo_col].to_numpy(dtype="float64")
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
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
    for k in range(sample_idx.shape[0]):
        t = int(sample_idx[k])
        if t + 1 >= n or t + H_max >= n:
            continue
        if segments[t + H_max] != segments[t]:
            continue
        if is_filled[t + 1] == 1:
            continue
        p_raw = float(p_entry[t + 1])
        if not np.isfinite(p_raw) or p_raw <= 0:
            continue
        c_t = float(close[t])
        if p_raw > c_t * (1.0 + max_chase):
            continue
        sigma = float(sigma_arr[t])
        if not np.isfinite(sigma):
            continue
        if not np.isfinite(rv15_arr[t]):
            continue
        entry = compute_entry(
            c_t=c_t,
            p_raw=p_raw,
            sigma=sigma,
            atr_abs=float(atr_abs_arr[t]),
            rv15=float(rv15_arr[t]),
            qv=float(qv[t]),
            tick_size=tick_size,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            notional_ref=notional_ref,
            half_spread_min=half_spread_min,
            slip_min=slip_min,
            Y=Y,
            fixed_slip=None,
            fee_taker=fees_taker,
        )
        result = simulate_exit(
            open_,
            high,
            low,
            close,
            p_entry,
            hi_post,
            lo_post,
            is_filled,
            t_entry=t + 1,
            p_fill=entry.p_fill,
            sl=entry.sl,
            atr_abs=entry.atr_abs,
            tick_size=tick_size,
            H_max=H_max,
            cfg=exit_cfg,
            slip_stop=entry.slip_stop,
            slip_exit=entry.slip,
            half_spread=entry.half_spread,
            fee_taker=fees_taker,
            fee_maker=fees_maker,
            use_numba=use_numba,
        )
        exit_fee_rate = result.fee_rate
        fr_sum = funding_rate_between(
            fund_times, fund_rates, int(open_time[t + 1]), int(open_time[result.exit_idx])
        )
        ret_net = result.exit_price / entry.p_fill - 1.0 - fees_taker - exit_fee_rate - fr_sum
        r_net = ret_net / entry.sl
        rows.append(
            {
                "open_time": int(open_time[t]),
                "y": int(r_net >= y_min_r),
                "r_mult": float(np.clip(r_net, _R_MULT_CLIP[0], _R_MULT_CLIP[1])),
                "exit_type": result.exit_type,
                "sl": entry.sl,
                "ret_net": ret_net,
                "bars_held": int(result.bars_held),
                "t_exit": int(open_time[result.exit_idx]),
            }
        )
        kept.append(t)

    frame = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "y",
            "r_mult",
            "exit_type",
            "sl",
            "ret_net",
            "bars_held",
            "t_exit",
        ],
    )
    return frame, np.asarray(kept, dtype="int64")
