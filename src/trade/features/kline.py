"""K 线类特征（#1~#22）与滚动统计工具。

规格：docs/04-特征与标签.md §4.1、§1
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.common._numba import njit

# 防除零（04 §1）
EPS = 1e-12


def _as_float(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype="float64")


def shift(x: np.ndarray, n: int) -> np.ndarray:
    """把数组平移 n 个位置（正值右移，前面补 NaN）。"""
    arr = _as_float(x)
    out = np.full(arr.shape, np.nan, dtype="float64")
    if n > 0:
        out[n:] = arr[:-n]
    elif n < 0:
        out[:n] = arr[-n:]
    else:
        out[:] = arr
    return out


def log_returns(close: np.ndarray) -> np.ndarray:
    """对数收益 r_t = ln(c_t / c_{t-1})，首个元素为 NaN（04 §1）。"""
    c = _as_float(close)
    out = np.full(c.shape, np.nan, dtype="float64")
    if c.shape[0] > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = np.log(c[1:] / c[:-1])
    return out


def rolling_sum(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动和，头部不足 n 根为 NaN。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).sum().to_numpy(dtype="float64")


def rolling_mean(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动均值。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).mean().to_numpy(dtype="float64")


def rolling_var(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动方差（总体方差 ddof=0）。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).var(ddof=0).to_numpy(dtype="float64")


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动标准差（总体 ddof=0）。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).std(ddof=0).to_numpy(dtype="float64")


def rolling_cov(x: np.ndarray, y: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动协方差（总体 ddof=0）。"""
    return (
        pd.Series(_as_float(x))
        .rolling(n, min_periods=n)
        .cov(pd.Series(_as_float(y)), ddof=0)
        .to_numpy(dtype="float64")
    )


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动最大值。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).max().to_numpy(dtype="float64")


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    """最近 n 根（含当前）的滚动最小值。"""
    return pd.Series(_as_float(x)).rolling(n, min_periods=n).min().to_numpy(dtype="float64")


def _rolling_max_exclusive(x: np.ndarray, n: int) -> np.ndarray:
    """max_N^-(x)：t-N … t-1（不含当前）的滚动最大值（04 §1）。"""
    return shift(rolling_max(x, n), 1)


def _rolling_min_exclusive(x: np.ndarray, n: int) -> np.ndarray:
    """min_N^-(x)：t-N … t-1（不含当前）的滚动最小值（04 §1）。"""
    return shift(rolling_min(x, n), 1)


def _log_change_n(close: np.ndarray, n: int) -> np.ndarray:
    """ln(c_t / c_{t-n})，头部不足 n 根为 NaN。"""
    c = _as_float(close)
    out = np.full(c.shape, np.nan, dtype="float64")
    if c.shape[0] > n:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[n:] = np.log(c[n:] / c[:-n])
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """真实波幅 TR_t = max(h-l, |h - c_{t-1}|, |l - c_{t-1}|)；首根取 h-l。"""
    h = _as_float(high)
    low_ = _as_float(low)
    c_prev = shift(close, 1)
    tr = np.maximum(h - low_, np.maximum(np.abs(h - c_prev), np.abs(low_ - c_prev)))
    tr[0] = h[0] - low_[0]
    return tr


@njit
def _wilder_atr_nb(tr: np.ndarray, n: int, seed: float) -> np.ndarray:
    """Wilder 平滑 ATR 的 numba 实现（04 §4.1；种子由调用方用同一口径算出）。"""
    out = np.full(tr.shape, np.nan, dtype=np.float64)
    if tr.shape[0] < n:
        return out
    prev = seed
    out[n - 1] = prev
    for i in range(n, tr.shape[0]):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def wilder_atr_py(tr: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑 ATR 的纯 Python 参考实现（与 `_wilder_atr_nb` 逐元素相等）。"""
    tr_arr = _as_float(tr)
    out = np.full(tr_arr.shape, np.nan, dtype="float64")
    if tr_arr.shape[0] < n:
        return out
    prev = float(np.nanmean(tr_arr[:n]))
    out[n - 1] = prev
    for i in range(n, tr_arr.shape[0]):
        prev = (prev * (n - 1) + tr_arr[i]) / n
        out[i] = prev
    return out


def wilder_atr(tr: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑 ATR（α = 1/n），种子为首 n 根 TR 的均值（04 §4.1）。

    走 numba 内核；种子用 `np.nanmean` 在 Python 侧算好传入，保证与
    `wilder_atr_py` 的浮点结果逐元素完全相同（特征与回测共用这一个入口）。
    """
    tr_arr = _as_float(tr)
    if tr_arr.shape[0] < n:
        return np.full(tr_arr.shape, np.nan, dtype="float64")
    return _wilder_atr_nb(tr_arr, n, float(np.nanmean(tr_arr[:n])))


def r_1(close: np.ndarray) -> np.ndarray:
    """#1 r_1 = r_t。"""
    return log_returns(close)


def r_5(close: np.ndarray) -> np.ndarray:
    """#2 r_5 = ln(c_t / c_{t-5})。"""
    return _log_change_n(close, 5)


def r_15(close: np.ndarray) -> np.ndarray:
    """#3 r_15 = ln(c_t / c_{t-15})。"""
    return _log_change_n(close, 15)


def r_60(close: np.ndarray) -> np.ndarray:
    """#4 r_60 = ln(c_t / c_{t-60})。"""
    return _log_change_n(close, 60)


def rng(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#5 rng = (h_t - l_t) / c_{t-1}。"""
    return (_as_float(high) - _as_float(low)) / shift(close, 1)


def body(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#6 body = (c_t - o_t) / (h_t - l_t + ε)，范围 [-1, 1]。"""
    return (_as_float(close) - _as_float(open_)) / (_as_float(high) - _as_float(low) + EPS)


def up_wick(high: np.ndarray, low: np.ndarray, open_: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#7 up_wick = (h_t - max(o_t, c_t)) / (h_t - l_t + ε)。"""
    denom = _as_float(high) - _as_float(low) + EPS
    return (_as_float(high) - np.maximum(_as_float(open_), _as_float(close))) / denom


def vol_z_60(quote_volume: np.ndarray) -> np.ndarray:
    """#8 vol_z_60 = (ln(1+qv_t) - mean_60(ln(1+qv))) / (std_60(ln(1+qv)) + ε)。"""
    x = np.log1p(_as_float(quote_volume))
    mu = rolling_mean(x, 60)
    sd = rolling_std(x, 60)
    return (x - mu) / (sd + EPS)


def vol_ratio_1440(quote_volume: np.ndarray) -> np.ndarray:
    """#9 vol_ratio_1440 = ln(1 + qv_t / (mean_1440(qv) + ε))。"""
    qv = _as_float(quote_volume)
    return np.log1p(qv / (rolling_mean(qv, 1440) + EPS))


def tbr(taker_buy_quote_volume: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#10 tbr = tbq_t / (qv_t + ε)。"""
    return _as_float(taker_buy_quote_volume) / (_as_float(quote_volume) + EPS)


def tbr_5(taker_buy_quote_volume: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#11 tbr_5 = sum_5(tbq) / (sum_5(qv) + ε)。"""
    return rolling_sum(taker_buy_quote_volume, 5) / (rolling_sum(quote_volume, 5) + EPS)


def cvd_norm(taker_buy_quote_volume: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#12 cvd_norm = (2·tbq_t - qv_t) / (mean_60(qv) + ε)。"""
    tbq = _as_float(taker_buy_quote_volume)
    qv = _as_float(quote_volume)
    return (2.0 * tbq - qv) / (rolling_mean(qv, 60) + EPS)


def cvd_15(taker_buy_quote_volume: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#13 cvd_15 = sum_15(2·tbq - qv) / (15·mean_60(qv) + ε)。"""
    tbq = _as_float(taker_buy_quote_volume)
    qv = _as_float(quote_volume)
    num = rolling_sum(2.0 * tbq - qv, 15)
    return num / (15.0 * rolling_mean(qv, 60) + EPS)


def velocity(count: np.ndarray) -> np.ndarray:
    """#14 velocity = ln(1 + n_t / (mean_60(n) + ε))。"""
    n = _as_float(count)
    return np.log1p(n / (rolling_mean(n, 60) + EPS))


def avg_trade_ratio(quote_volume: np.ndarray, count: np.ndarray) -> np.ndarray:
    """#15 avg_trade_ratio = ln(1 + (qv_t/(n_t+ε)) / (mean_60(qv/(n+ε)) + ε))。"""
    qv = _as_float(quote_volume)
    n = _as_float(count)
    per = qv / (n + EPS)
    return np.log1p(per / (rolling_mean(per, 60) + EPS))


def timeframe_atr(
    open_time: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    timeframe_min: int,
    length: int = 14,
) -> np.ndarray:
    """按 `timeframe_min` 分钟聚合后计算 ATR14，并映射回每分钟（只用已完成的时间桶）。

    用途：决策周期放大到 5/15 分钟时，止损尺度必须按该周期的波动来定；用 1 分钟
    ATR 会把止损压得过紧。为了避免未来函数，分钟 t 取的是**在 t 之前已经收盘的
    最后一个时间桶**的 ATR（桶边界按 `open_time // (timeframe_min × 60000)` 对齐）。

    Args:
        open_time: 分钟开盘时间（毫秒，升序）。
        high/low/close: 分钟最高/最低/收盘价。
        timeframe_min: 聚合周期（分钟）；1 表示直接用 1 分钟 ATR。
        length: ATR 的 Wilder 长度（默认 14）。

    Returns:
        与输入等长的 ATR 绝对值数组；时间桶不足 `length` 个的位置为 NaN。

    Raises:
        ValueError: `timeframe_min < 1`（红线 13）。
    """
    if timeframe_min < 1:
        raise ValueError(f"timeframe_min 必须 ≥ 1，收到 {timeframe_min}")
    close = np.asarray(close, dtype="float64")
    if timeframe_min == 1:
        return wilder_atr(true_range(high, low, close), length)

    buckets = np.asarray(open_time, dtype="int64") // (timeframe_min * 60_000)
    # 每个桶的高/低/收：用 numpy 分段归约（桶已按时间升序）
    uniq, starts = np.unique(buckets, return_index=True)
    counts = np.diff(np.append(starts, buckets.shape[0]))
    seg_high = np.maximum.reduceat(np.asarray(high, dtype="float64"), starts)
    seg_low = np.minimum.reduceat(np.asarray(low, dtype="float64"), starts)
    seg_close = close[starts + counts - 1]
    seg_atr = wilder_atr(true_range(seg_high, seg_low, seg_close), length)
    # 分钟 t 用「上一个已完成桶」的 ATR：桶序号在 uniq 中的前一位
    positions = np.searchsorted(uniq, buckets, side="left") - 1
    out = np.full(close.shape[0], np.nan, dtype="float64")
    ok = positions >= 0
    out[ok] = seg_atr[positions[ok]]
    return out


def r_240(close: np.ndarray) -> np.ndarray:
    """`r_240 = log(c_t / c_{t-240})`（4 小时动量）。"""
    return _log_change_n(close, 240)


def r_1440(close: np.ndarray) -> np.ndarray:
    """`r_1440 = log(c_t / c_{t-1440})`（24 小时动量）。"""
    return _log_change_n(close, 1440)


def dist_24h_high(high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """`dist_24h_high = c_t / max(high, 1440) − 1`（距 24 小时高点的距离，≤ 0）。"""
    h = _as_float(high)
    c = _as_float(close)
    top = rolling_max(h, 1440)
    with np.errstate(invalid="ignore", divide="ignore"):
        return c / top - 1.0


def log_qv_60(quote_volume: np.ndarray) -> np.ndarray:
    """`log_qv_60 = log(1 + mean_60(quote_volume))`（60 分钟成交额水平）。"""
    qv = _as_float(quote_volume)
    return np.log1p(rolling_mean(qv, 60))


def bars_since_brk_60(high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """距上次 `brk_60 > 0` 的分钟数（无突破时为 1440，封顶避免无界）。"""
    flag = (brk_60(high, close) > 0.0).astype("int64")
    n = flag.shape[0]
    out = np.full(n, 1440.0, dtype="float64")
    last = -1
    for index in range(n):
        if flag[index]:
            last = index
        if last >= 0:
            out[index] = float(min(index - last, 1440))
    return out


def rv_15(close: np.ndarray) -> np.ndarray:
    """#16 rv_15 = std_15(r)。"""
    return rolling_std(log_returns(close), 15)


def rv_ratio(close: np.ndarray) -> np.ndarray:
    """#17 rv_ratio = std_15(r) / (std_240(r) + ε)。"""
    r = log_returns(close)
    return rolling_std(r, 15) / (rolling_std(r, 240) + EPS)


def atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#18 atr_pct = ATR14_t / c_t（Wilder，α = 1/14）。"""
    return wilder_atr(true_range(high, low, close), 14) / _as_float(close)


def brk_60(high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#19 brk_60 = (c_t - max_60^-(h)) / c_t。"""
    return (_as_float(close) - _rolling_max_exclusive(high, 60)) / _as_float(close)


def brk_240(high: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#20 brk_240 = (c_t - max_240^-(h)) / c_t。"""
    return (_as_float(close) - _rolling_max_exclusive(high, 240)) / _as_float(close)


def dd_60(low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#21 dd_60 = (c_t - min_60^-(l)) / c_t。"""
    return (_as_float(close) - _rolling_min_exclusive(low, 60)) / _as_float(close)


def vwap_dev_60(quote_volume: np.ndarray, volume: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#22 vwap_dev_60 = (c_t - VWAP_60) / c_t，VWAP_60 = sum_60(qv)/(sum_60(v)+ε)。"""
    vwap = rolling_sum(quote_volume, 60) / (rolling_sum(volume, 60) + EPS)
    return (_as_float(close) - vwap) / _as_float(close)


def wick_ratio_5(
    high: np.ndarray, low: np.ndarray, open_: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """#38 wick_ratio_5 = sum_5(上影线价格) / (sum_5(振幅价格) + ε)（04 §4.6）。"""
    wick = _as_float(high) - np.maximum(_as_float(open_), _as_float(close))
    span = _as_float(high) - _as_float(low)
    return rolling_sum(wick, 5) / (rolling_sum(span, 5) + EPS)


def close_pos_5(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#39 close_pos_5 = mean_5((c − l) / (h − l + ε))，近 5 根收盘强度（04 §4.6）。"""
    pos = (_as_float(close) - _as_float(low)) / (_as_float(high) - _as_float(low) + EPS)
    return rolling_mean(pos, 5)


def range_ratio_5(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """#40 range_ratio_5 = rng_t / (mean_5(rng) + ε)，振幅相对近 5 根均值（04 §4.6）。"""
    rng_values = rng(high, low, close)
    return rng_values / (rolling_mean(rng_values, 5) + EPS)


def tbr_div(taker_buy_quote_volume: np.ndarray, quote_volume: np.ndarray) -> np.ndarray:
    """#41 tbr_div = tbr_t − tbr_5_t，短周期与中周期主动买占比之差（04 §4.6）。"""
    return tbr(taker_buy_quote_volume, quote_volume) - tbr_5(taker_buy_quote_volume, quote_volume)
