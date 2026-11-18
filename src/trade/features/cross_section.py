"""跨市场（横截面）特征：同一分钟全市场 r_15 的宽度、均值与离散度。

规格：docs/04-特征与标签.md §4.7

实现要点：逐币累加同一分钟网格上的计数/和/平方和/上涨数，只保留 4 个长度等于分钟数的
数组，因此内存与币数无关；不使用任何未来分钟。
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from trade.features.kline import r_15

XS_COLUMNS = ("xs_breadth", "xs_mean_r15", "xs_disp_r15")


def build_market_series(
    series: Iterable[tuple[np.ndarray, np.ndarray]],
    *,
    start_ms: int,
    n_minutes: int,
) -> pd.DataFrame:
    """按分钟统计全市场 r_15 的宽度、均值与离散度（04 §4.7）。

    Args:
        series: 每项为 `(open_time int64 数组, close float64 数组)`，两数组等长且按时间升序。
        start_ms: 分钟网格起点（数据窗口起点，UTC 毫秒）。
        n_minutes: 分钟网格长度。

    Returns:
        DataFrame，列 `open_time`/`xs_breadth`/`xs_mean_r15`/`xs_disp_r15`；
        该分钟没有币有有效 r_15 时为 NaN。
    """
    if n_minutes <= 0:
        raise ValueError("n_minutes 必须为正（04 §4.7）")
    count = np.zeros(n_minutes, dtype="float64")
    total = np.zeros(n_minutes, dtype="float64")
    total_sq = np.zeros(n_minutes, dtype="float64")
    positive = np.zeros(n_minutes, dtype="float64")
    for open_time, close in series:
        times = np.asarray(open_time, dtype="int64")
        returns = r_15(np.asarray(close, dtype="float64"))
        indexes = (times - start_ms) // 60_000
        valid = (indexes >= 0) & (indexes < n_minutes) & np.isfinite(returns)
        if not valid.any():
            continue
        np.add.at(count, indexes[valid], 1.0)
        np.add.at(total, indexes[valid], returns[valid])
        np.add.at(total_sq, indexes[valid], returns[valid] ** 2)
        np.add.at(positive, indexes[valid], (returns[valid] > 0.0).astype("float64"))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
        variance = np.maximum(total_sq / count - mean**2, 0.0)
        breadth = positive / count
    frame = pd.DataFrame(
        {
            "open_time": start_ms + np.arange(n_minutes, dtype="int64") * 60_000,
            "xs_breadth": np.where(count > 0, breadth, np.nan),
            "xs_mean_r15": np.where(count > 0, mean, np.nan),
            "xs_disp_r15": np.where(count > 0, np.sqrt(variance), np.nan),
        }
    )
    return frame


def align_market_series(open_time: np.ndarray, market: pd.DataFrame) -> dict[str, np.ndarray]:
    """把市场序列按 `open_time` 对齐到本币分钟（缺失分钟取上一值，与 BTC 对齐一致）。

    Args:
        open_time: 本币 `open_time`（UTC 毫秒，升序）。
        market: `build_market_series` 的输出。

    Returns:
        列名 → 与 `open_time` 等长的数组。
    """
    left = pd.DataFrame({"open_time": np.asarray(open_time, dtype="int64")})
    merged = pd.merge_asof(
        left, market.sort_values("open_time"), on="open_time", direction="backward"
    )
    return {name: merged[name].to_numpy(dtype="float64") for name in XS_COLUMNS}
