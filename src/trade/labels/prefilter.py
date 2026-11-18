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
    *,
    direction: str = "any",
) -> np.ndarray:
    """预过滤三条件（任一满足即采样，04 §6）：vol_z_60 ≥ 阈值 / |r_1| ≥ 2σ / brk_60 > 阈值。

    NaN（lookback 不足）自然判为不满足。σ 加 ε 防除零（04 §1），避免零波动时 0≥0 恒真。
    `direction="up"` 时只保留上涨方向事件：三个条件取或后，再要求
    `brk_60 > 阈值`（突破）或 `r_1 > 0 且 body > 0`（实体阳线），剔除砸盘与卖压放量。
    """
    if direction not in {"any", "up"}:
        raise ValueError(f"prefilter.direction 只能是 any / up，收到 {direction}")
    close = bars["close"].to_numpy(dtype="float64")
    r = log_returns(close)
    v = vol_z_60(bars["quote_volume"].to_numpy(dtype="float64"))
    b = brk_60(bars["high"].to_numpy(dtype="float64"), close)
    r_std = rolling_std(r, 60)
    mask = (v >= vol_z_60_thr) | (np.abs(r) >= r1_sigma * (r_std + EPS)) | (b > brk_60_thr)
    if direction == "up":
        body = close - bars["open"].to_numpy(dtype="float64")
        mask &= (b > brk_60_thr) | ((r > 0.0) & (body > 0.0))
    return mask


def is_sample_mask(
    bars: pd.DataFrame,
    vol_z_60_thr: float,
    r1_sigma: float,
    brk_60_thr: float,
    warmup_bars: int,
    segments: np.ndarray | None = None,
    *,
    direction: str = "any",
    cadence_min: int = 1,
) -> np.ndarray:
    """样本掩码：预过滤三条件 且 非补齐分钟 且 已过预热期（按数据段，03 §6）。

    `cadence_min > 1` 时只在 `open_time` 的分钟序号能被其整除的分钟产生样本（信号节流，
    用于把决策周期放大到 5/15 分钟）。
    """
    n = len(bars)
    if segments is None:
        segments = np.zeros(n, dtype="int64")
    segments = np.asarray(segments, dtype="int64")
    if cadence_min < 1:
        raise ValueError(f"cadence_min 必须 ≥ 1，收到 {cadence_min}（04 §6）")
    if "is_filled" not in bars.columns:
        raise ValueError("bars 缺少列 is_filled（03 §2.3、红线 13）")
    is_filled = bars["is_filled"].to_numpy(dtype="int64")
    # 预过滤与预热都按段独立计算：滚动窗口不跨段、段首 warmup_bars 根不产出样本
    mask = np.zeros(n, dtype=bool)
    warmed = np.zeros(n, dtype=bool)
    for seg in np.unique(segments):
        idx = np.nonzero(segments == seg)[0]
        seg_bars = bars.iloc[idx].reset_index(drop=True)
        mask[idx] = prefilter_mask(
            seg_bars, vol_z_60_thr, r1_sigma, brk_60_thr, direction=direction
        )
        warmed[idx] = np.arange(idx.shape[0]) >= warmup_bars
    out = mask & (is_filled == 0) & warmed
    if cadence_min > 1:
        minute_index = bars["open_time"].to_numpy(dtype="int64") // 60_000
        out &= (minute_index % cadence_min) == 0
    return out
