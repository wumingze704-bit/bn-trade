"""跨市场（横截面）特征测试。

规格：docs/04-特征与标签.md §4.7
"""

from __future__ import annotations

import numpy as np
import pytest

from trade.features.cross_section import align_market_series, build_market_series

_START = 1_700_000_000_000


def _series(values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    open_time = _START + np.arange(len(values), dtype="int64") * 60_000
    return open_time, np.asarray(values, dtype="float64")


def test_跨市场宽度均值与离散度() -> None:
    """同一分钟上 r_15 > 0 的占比、均值与总体标准差（04 §4.7）。"""
    up = [100.0] * 15 + [110.0]
    down = [100.0] * 15 + [90.0]
    short = [100.0] * 5  # r_15 全为 NaN，不参与统计
    market = build_market_series(
        [_series(up), _series(down), _series(short)], start_ms=_START, n_minutes=16
    )
    r_up = np.log(110.0 / 100.0)
    r_down = np.log(90.0 / 100.0)
    last = market.iloc[15]
    assert last["xs_breadth"] == pytest.approx(0.5)
    assert last["xs_mean_r15"] == pytest.approx((r_up + r_down) / 2.0)
    assert last["xs_disp_r15"] == pytest.approx(abs(r_up - r_down) / 2.0)
    # 前 15 分钟没有足够的 r_15 → 统计缺失
    assert np.isnan(market["xs_breadth"].iloc[0])


def test_跨市场序列对齐到本币分钟() -> None:
    """对齐后在每分钟取到同一分钟的统计值；缺该分钟时取上一值（04 §4.7）。"""
    up = [100.0] * 15 + [110.0]
    market = build_market_series([_series(up)], start_ms=_START, n_minutes=16)
    open_time, _ = _series([100.0] * 16)
    aligned = align_market_series(open_time, market)
    assert aligned["xs_breadth"][15] == pytest.approx(1.0)
    # 本币少了第 15 分钟 → 用第 14 分钟的值（缺失为 NaN）
    aligned_gap = align_market_series(open_time[:15], market)
    assert np.isnan(aligned_gap["xs_breadth"][14])
