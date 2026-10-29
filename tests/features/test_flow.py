"""订单流类特征手算样例测试（#23~#27）。

规格：docs/04-特征与标签.md §4.2
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from trade.features import flow


def test_whale_ratio() -> None:
    out = flow.whale_ratio(
        np.array([500.0]), np.array([500.0]), np.array([100.0]), np.array([50.0])
    )
    assert out[0] == pytest.approx(150.0 / 1000.0)


def test_whale_net() -> None:
    out = flow.whale_net(np.array([500.0]), np.array([500.0]), np.array([100.0]), np.array([50.0]))
    assert out[0] == pytest.approx(50.0 / 1000.0)


def test_whale_net_15() -> None:
    buy = np.ones(15, dtype="float64") * 500.0
    sell = np.ones(15, dtype="float64") * 500.0
    wb = np.ones(15, dtype="float64") * 100.0
    ws = np.ones(15, dtype="float64") * 50.0
    out = flow.whale_net_15(buy, sell, wb, ws)
    assert out[14] == pytest.approx((15.0 * 50.0) / (15.0 * 1000.0))


def test_burst_1s() -> None:
    qv = np.ones(60, dtype="float64") * 120.0
    max_buy_1s = np.ones(60, dtype="float64") * 2.0
    max_buy_1s[59] = 4.0
    out = flow.burst_1s(max_buy_1s, qv)
    # mean_60(qv) = 120，mean/60 = 2
    assert out[59] == pytest.approx(math.log1p(4.0 / 2.0))


def test_max_trade_rel() -> None:
    out = flow.max_trade_rel(np.array([200.0]), np.array([1000.0]))
    assert out[0] == pytest.approx(math.log1p(200.0 / 1000.0))
