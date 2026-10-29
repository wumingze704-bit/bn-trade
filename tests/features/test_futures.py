"""合约类特征手算样例测试（#34~#37）。

规格：docs/04-特征与标签.md §4.4
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from trade.features import futures


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
