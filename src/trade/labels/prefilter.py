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
    segments: np.ndarray | None = None,
) -> np.ndarray:
    """样本掩码：预过滤三条件 且 非补齐分钟 且 已过预热期（按数据段，03 §6）。"""
    n = len(bars)
    if segments is None:
        segments = np.zeros(n, dtype="int64")
    segments = np.asarray(segments, dtype="int64")
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
    # 预过滤与预热都按段独立计算：滚动窗口不跨段、段首 warmup_bars 根不产出样本
    mask = np.zeros(n, dtype=bool)
    warmed = np.zeros(n, dtype=bool)
    for seg in np.unique(segments):
        idx = np.nonzero(segments == seg)[0]
        seg_bars = bars.iloc[idx].reset_index(drop=True)
        mask[idx] = prefilter_mask(seg_bars, vol_z_60_thr, r1_sigma, brk_60_thr)
        warmed[idx] = np.arange(idx.shape[0]) >= warmup_bars
    return mask & (is_filled == 0) & warmed
