"""合约类特征手算样例测试（#34~#37）。

规格：docs/04-特征与标签.md §4.4
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from trade.features import flow, futures


def test_oi_chg_15() -> None:
    oi = np.ones(16, dtype="float64") * 1000.0
    oi[15] = 2000.0
    out = futures.oi_chg_15(oi)
    assert out[15] == pytest.approx(math.log(2000.0 / 1000.0))


def test_oi_chg_60() -> None:
    oi = np.ones(61, dtype="float64") * 1000.0
    oi[60] = 4000.0
    out = futures.oi_chg_60(oi)
    assert out[60] == pytest.approx(math.log(4000.0 / 1000.0))


def test_funding() -> None:
    out = futures.funding(np.array([0.0001, 0.0002]))
    assert out[1] == pytest.approx(0.0002)


def test_futures_missing() -> None:
    oi = np.array([1.0, np.nan, 1.0])
    fr = np.array([0.0001, 0.0001, np.nan])
    out = futures.futures_missing(oi, fr)
    assert out.tolist() == [0.0, 1.0, 1.0]


def test_whale_streak_5() -> None:
    """近 5 根大单净方向均值（04 §4.6 #42）。"""
    n = 5
    buy = np.ones(n)
    sell = np.ones(n)
    whale_buy = np.array([2.0, 0.0, 2.0, 0.0, 2.0])
    whale_sell = np.array([0.0, 2.0, 0.0, 2.0, 0.0])
    out = flow.whale_streak_5(buy, sell, whale_buy, whale_sell)
    assert out[4] == pytest.approx(0.2)  # 方向 [+1, −1, +1, −1, +1] → 1/5


def test_oi_price_div_15() -> None:
    """持仓量变化与价格方向的一致性（04 §4.6 #43）。"""
    n = 16
    close = np.ones(n) * 100.0
    close[-1] = 101.0  # r_15 > 0
    oi = np.ones(n) * 1000.0
    oi[-1] = 1100.0
    out = futures.oi_price_div_15(close, oi)
    assert out[-1] == pytest.approx(np.log(1100.0 / 1000.0))
    close_down = np.ones(n) * 100.0
    close_down[-1] = 99.0
    out_down = futures.oi_price_div_15(close_down, oi)
    assert out_down[-1] == pytest.approx(-np.log(1100.0 / 1000.0))
