"""三重障碍标签与样本独特性权重。

规格：docs/04-特征与标签.md §7
"""

from __future__ import annotations

import numba
import numpy as np
import pandas as pd

from trade.features.kline import atr_pct

# outcome 编码
_SL = 0
_TP = 1
_TO = 2

_OUTCOME_TO_STR = {_SL: "SL", _TP: "TP", _TO: "TO"}


@numba.njit(cache=False)
def _triple_barrier_nb(
    sample_idx: np.ndarray,
    atr_pct_arr: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    p_entry: np.ndarray,
    lo_post: np.ndarray,
    hi_post: np.ndarray,
    is_filled: np.ndarray,
    open_time: np.ndarray,
    k_sl: float,
    rr: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    fill_ratio_max: float,
) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray
]:
    """numba 版三重障碍（与纯 Python 参考实现逐字段一致，04 §7.1）。"""
    m = sample_idx.shape[0]
    n = open_.shape[0]
    outcome = np.empty(m, dtype=np.int64)
    exit_price = np.empty(m, dtype=np.float64)
    entry_price = np.empty(m, dtype=np.float64)
    sl_out = np.empty(m, dtype=np.float64)
    tp_out = np.empty(m, dtype=np.float64)
    bars_held = np.empty(m, dtype=np.int64)
    t_exit = np.empty(m, dtype=np.int64)
    keep = np.empty(m, dtype=np.bool_)

    for k in range(m):
        t = sample_idx[k]
        outcome[k] = _TO
        exit_price[k] = np.nan
        entry_price[k] = np.nan
        sl_out[k] = np.nan
        tp_out[k] = np.nan
        bars_held[k] = 0
        t_exit[k] = 0
        keep[k] = False
        # 数据末尾不足 H_label 根、或入场 K 线不存在：丢弃（04 §7.1/§7.2）
        if t + 1 >= n or t + H_label >= n:
            continue
        # 入场 K 线不得为补齐分钟（与 06 §3.1 开仓条件一致）
        if is_filled[t + 1] == 1:
            continue
        sigma = atr_pct_arr[t]
        if not np.isfinite(sigma):
            continue
        sl = k_sl * sigma
        if sl < sl_min:
            sl = sl_min
        elif sl > sl_max:
            sl = sl_max
        tp = rr * sl
        p0 = p_entry[t + 1]
        if not np.isfinite(p0):
            continue
        upper = p0 * (1.0 + tp)
        lower = p0 * (1.0 - sl)
        fill_count = 0
        res = _TO
        exit_p = close[t + H_label]
        exit_j = t + H_label
        for j in range(t + 1, t + H_label + 1):
            if is_filled[j] == 1:
                fill_count += 1
            if j == t + 1:
                lo = lo_post[j]
                hi = hi_post[j]
            else:
                lo = low[j]
                hi = high[j]
            if lo <= lower:
                res = _SL
                exit_p = lower
                # 跳空：j > t+1 且开盘价低于止损位时，出场价按开盘价（04 §7.1）
                if j > t + 1 and open_[j] < lower:
                    exit_p = open_[j]
                exit_j = j
                break
            if hi >= upper:
                res = _TP
                exit_p = upper
                exit_j = j
                break
        if fill_count / H_label > fill_ratio_max:
            continue
        outcome[k] = res
        exit_price[k] = exit_p
        entry_price[k] = p0
        sl_out[k] = sl
        tp_out[k] = tp
        bars_held[k] = exit_j - t
        t_exit[k] = open_time[exit_j]
        keep[k] = True

    return outcome, exit_price, entry_price, sl_out, tp_out, bars_held, t_exit, keep


def _triple_barrier_py(
    sample_idx: np.ndarray,
    atr_pct_arr: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    p_entry: np.ndarray,
    lo_post: np.ndarray,
    hi_post: np.ndarray,
    is_filled: np.ndarray,
    open_time: np.ndarray,
    k_sl: float,
    rr: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    fill_ratio_max: float,
) -> tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray
]:
    """纯 Python 参考实现（与 numba 版逐字段一致，用于测试比对，AGENTS.md §6）。"""
    m = sample_idx.shape[0]
    n = open_.shape[0]
    outcome = np.zeros(m, dtype=np.int64)
    exit_price = np.full(m, np.nan, dtype="float64")
    entry_price = np.full(m, np.nan, dtype="float64")
    sl_out = np.full(m, np.nan, dtype="float64")
    tp_out = np.full(m, np.nan, dtype="float64")
    bars_held = np.zeros(m, dtype=np.int64)
    t_exit = np.zeros(m, dtype=np.int64)
    keep = np.zeros(m, dtype=bool)

    for k, t in enumerate(sample_idx):
        outcome[k] = _TO
        if t + 1 >= n or t + H_label >= n:
            continue
        if is_filled[t + 1] == 1:
            continue
        sigma = float(atr_pct_arr[t])
        if not np.isfinite(sigma):
            continue
        sl = k_sl * sigma
        sl = sl_min if sl < sl_min else (sl_max if sl > sl_max else sl)
        tp = rr * sl
        p0 = float(p_entry[t + 1])
        if not np.isfinite(p0):
            continue
        upper = p0 * (1.0 + tp)
        lower = p0 * (1.0 - sl)
        fill_count = 0
        res = _TO
        exit_p = float(close[t + H_label])
        exit_j = t + H_label
        for j in range(t + 1, t + H_label + 1):
            if is_filled[j] == 1:
                fill_count += 1
            if j == t + 1:
                lo = lo_post[j]
                hi = hi_post[j]
            else:
                lo = low[j]
                hi = high[j]
            if lo <= lower:
                res = _SL
                exit_p = lower
                if j > t + 1 and open_[j] < lower:
                    exit_p = open_[j]
                exit_j = j
                break
            if hi >= upper:
                res = _TP
                exit_p = upper
                exit_j = j
                break
        if fill_count / H_label > fill_ratio_max:
            continue
        outcome[k] = res
        exit_price[k] = exit_p
        entry_price[k] = p0
        sl_out[k] = sl
        tp_out[k] = tp
        bars_held[k] = exit_j - t
        t_exit[k] = open_time[exit_j]
        keep[k] = True

    return outcome, exit_price, entry_price, sl_out, tp_out, bars_held, t_exit, keep


def _entry_columns(entry_latency_ms: int) -> tuple[str, str, str]:
    if entry_latency_ms == 1000:
        return "p_1s", "hi_post_1s", "lo_post_1s"
    if entry_latency_ms == 3000:
        return "p_3s", "hi_post_3s", "lo_post_3s"
    raise ValueError(f"不支持的入场延迟：{entry_latency_ms}（04 §7.1 仅 1000/3000）")


def triple_barrier(
    bars: pd.DataFrame,
    sample_idx: np.ndarray,
    *,
    k_sl: float,
    rr: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    entry_latency_ms: int,
    c_rt: float,
    r_mult_clip: tuple[float, float],
    fill_ratio_max: float,
    use_numba: bool = True,
) -> tuple[pd.DataFrame, np.ndarray]:
    """对样本做三重障碍标签，返回 (labels 表, 保留下来的样本 bar 位置)。

    规格：docs/04-特征与标签.md §7.1、§7.2
    """
    p_col, hi_col, lo_col = _entry_columns(entry_latency_ms)
    atr = atr_pct(
        bars["high"].to_numpy(dtype="float64"),
        bars["low"].to_numpy(dtype="float64"),
        bars["close"].to_numpy(dtype="float64"),
    )
    n = len(bars)
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
    sample_idx = np.asarray(sample_idx, dtype="int64")
    args = (
        sample_idx,
        atr,
        bars["open"].to_numpy(dtype="float64"),
        bars["high"].to_numpy(dtype="float64"),
        bars["low"].to_numpy(dtype="float64"),
        bars["close"].to_numpy(dtype="float64"),
        bars[p_col].to_numpy(dtype="float64"),
        bars[lo_col].to_numpy(dtype="float64"),
        bars[hi_col].to_numpy(dtype="float64"),
        is_filled,
        bars["open_time"].to_numpy(dtype="int64"),
        k_sl,
        rr,
        sl_min,
        sl_max,
        H_label,
        fill_ratio_max,
    )
    if use_numba:
        outcome, exit_price, entry_price, sl, tp, bars_held, t_exit, keep = _triple_barrier_nb(
            *args
        )
    else:
        outcome, exit_price, entry_price, sl, tp, bars_held, t_exit, keep = _triple_barrier_py(
            *args
        )

    kept = np.nonzero(keep)[0]
    r_mult_lo, r_mult_hi = r_mult_clip
    ret_gross = exit_price[kept] / entry_price[kept] - 1.0
    r_mult = np.clip((ret_gross - c_rt) / sl[kept], r_mult_lo, r_mult_hi)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    frame = pd.DataFrame(
        {
            "open_time": open_time[sample_idx[kept]],
            "y": (outcome[kept] == _TP).astype("int64"),
            "outcome": [_OUTCOME_TO_STR[int(code)] for code in outcome[kept]],
            "tp": tp[kept],
            "sl": sl[kept],
            "ret_gross": ret_gross,
            "r_mult": r_mult,
            "bars_held": bars_held[kept],
            "t_exit": t_exit[kept],
        }
    )
    return frame, sample_idx[kept]


def uniqueness_weights(sample_idx: np.ndarray, bars_held: np.ndarray) -> np.ndarray:
    """López de Prado 平均独特性权重（04 §7.3）。

    Args:
        sample_idx: 样本的 bar 位置（升序，同币内）。
        bars_held: 每个样本的持有根数（标签窗口 = [t+1, t+bars_held]）。

    Returns:
        每个样本的权重 = 其标签窗口内每根 K 线的 1/并发样本数 的平均值。
        不重叠样本的权重为 1。
    """
    sample_idx = np.asarray(sample_idx, dtype="int64")
    bars_held = np.asarray(bars_held, dtype="int64")
    if sample_idx.shape[0] == 0:
        return np.empty(0, dtype="float64")
    max_bar = int(np.max(sample_idx + bars_held))
    concurrent = np.zeros(max_bar + 2, dtype="int64")
    for start, held in zip(sample_idx, bars_held, strict=True):
        concurrent[start + 1 : start + held + 1] += 1
    weights = np.empty(sample_idx.shape[0], dtype="float64")
    for i, (start, held) in enumerate(zip(sample_idx, bars_held, strict=True)):
        weights[i] = float(np.mean(1.0 / concurrent[start + 1 : start + held + 1]))
    return weights
