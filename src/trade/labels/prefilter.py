"""事件预过滤（事件采样）。

规格：docs/04-特征与标签.md §6
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.features.kline import EPS, brk_60, log_returns, rolling_std, vol_z_60


def prefilter_mask(
    bars: pd.DataFrame,
    vol_z_60_thr: float,
    r1_sigma: float,
    brk_60_thr: float,
) -> np.ndarray:
    """预过滤三条件（任一满足即采样，04 §6）：vol_z_60 ≥ 阈值 / |r_1| ≥ 2σ / brk_60 > 阈值。

    NaN（lookback 不足）自然判为不满足。σ 加 ε 防除零（04 §1），避免零波动时 0≥0 恒真。
    """
    r = log_returns(bars["close"].to_numpy(dtype="float64"))
    v = vol_z_60(bars["quote_volume"].to_numpy(dtype="float64"))
    b = brk_60(bars["high"].to_numpy(dtype="float64"), bars["close"].to_numpy(dtype="float64"))
    r_std = rolling_std(r, 60)
    return (v >= vol_z_60_thr) | (np.abs(r) >= r1_sigma * (r_std + EPS)) | (b > brk_60_thr)


def is_sample_mask(
    bars: pd.DataFrame,
    vol_z_60_thr: float,
    r1_sigma: float,
    brk_60_thr: float,
    warmup_bars: int,
) -> np.ndarray:
    """样本掩码：预过滤三条件 且 非补齐分钟 且 已过预热期。"""
    mask = prefilter_mask(bars, vol_z_60_thr, r1_sigma, brk_60_thr)
    n = len(bars)
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
    warmed = np.arange(n) >= warmup_bars
    return mask & (is_filled == 0) & warmed
