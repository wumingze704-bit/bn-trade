"""市场环境类特征（#28~#33，含 BTC 上下文）。

规格：docs/04-特征与标签.md §4.3
"""

from __future__ import annotations

import numpy as np

from trade.features.kline import EPS, log_returns, r_15, rolling_cov, rolling_std, rolling_var

# 每天毫秒数（UTC），用于把 open_time 换算为 UTC 小时（04 §4.3）
_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000


def btc_r_1(btc_close: np.ndarray) -> np.ndarray:
    """#28 btc_r_1 = BTCUSDT 的 r_1。"""
    return log_returns(btc_close)


def btc_r_15(btc_close: np.ndarray) -> np.ndarray:
    """#29 btc_r_15 = BTCUSDT 的 r_15。"""
    return r_15(btc_close)


def btc_rv_60(btc_close: np.ndarray) -> np.ndarray:
    """#30 btc_rv_60 = BTCUSDT 的 std_60(r)。"""
    return rolling_std(log_returns(btc_close), 60)


def ex_r_15(close: np.ndarray, btc_close: np.ndarray) -> np.ndarray:
    """#31 ex_r_15 = r_15 - β_t · btc_r_15，β_t = cov_1440(r, r_btc)/(var_1440(r_btc)+ε)。"""
    r = log_returns(close)
    r_btc = log_returns(btc_close)
    beta = rolling_cov(r, r_btc, 1440) / (rolling_var(r_btc, 1440) + EPS)
    return r_15(close) - beta * r_15(btc_close)


def _utc_hour(open_time: np.ndarray) -> np.ndarray:
    """把 open_time（毫秒）换算为 UTC 小时（含分钟小数）。"""
    ts = np.asarray(open_time, dtype="float64")
    return (ts % _MS_PER_DAY) / _MS_PER_HOUR


def hour_sin(open_time: np.ndarray) -> np.ndarray:
    """#32 hour_sin = sin(2π·(UTC 小时 + 分钟/60) / 24)。"""
    return np.sin(2.0 * np.pi * _utc_hour(open_time) / 24.0)


def hour_cos(open_time: np.ndarray) -> np.ndarray:
    """#33 hour_cos = cos(2π·(UTC 小时 + 分钟/60) / 24)。"""
    return np.cos(2.0 * np.pi * _utc_hour(open_time) / 24.0)
