"""K 线类特征（#1~#22）与滚动统计工具。

规格：docs/04-特征与标签.md §4.1、§1
"""

from __future__ import annotations

import numpy as np
import pandas as pd

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


def wilder_atr(tr: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑 ATR（α = 1/n），种子为首 n 根 TR 的均值。"""
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
