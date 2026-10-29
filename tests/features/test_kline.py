"""K 线类特征手算样例测试（#1~#22）。

规格：docs/04-特征与标签.md §4.1；08 §2 P2 验收
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from trade.features import kline


def test_r_1() -> None:
    close = np.array([100.0, 101.0, 103.0])
    out = kline.r_1(close)
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(math.log(101.0 / 100.0))
    assert out[2] == pytest.approx(math.log(103.0 / 101.0))


def test_r_5() -> None:
    close = np.array([100.0, 101, 102, 103, 104])  # 5 根不足 → 全 NaN
    assert np.isnan(kline.r_5(close)).all()
    close = np.array([100.0, 101, 102, 103, 104, 106.0])
    assert kline.r_5(close)[5] == pytest.approx(math.log(106.0 / 100.0))


def test_r_15() -> None:
    close = np.arange(1, 18, dtype="float64") * 10.0
    out = kline.r_15(close)
    assert out[15] == pytest.approx(math.log(160.0 / 10.0))


def test_r_60() -> None:
    close = np.arange(1, 63, dtype="float64") * 10.0
    out = kline.r_60(close)
    assert out[60] == pytest.approx(math.log(610.0 / 10.0))


def test_rng() -> None:
    high = np.array([0.0, 110.0])
    low = np.array([0.0, 90.0])
    close = np.array([100.0, 100.0])
    out = kline.rng(high, low, close)
    assert np.isnan(out[0])
    assert out[1] == pytest.approx((110.0 - 90.0) / 100.0)


def test_body() -> None:
    out = kline.body(np.array([100.0]), np.array([110.0]), np.array([90.0]), np.array([105.0]))
    assert out[0] == pytest.approx((105.0 - 100.0) / (110.0 - 90.0))


def test_up_wick() -> None:
    # h=110, max(o,c)=105, l=90
    out = kline.up_wick(np.array([110.0]), np.array([90.0]), np.array([100.0]), np.array([105.0]))
    assert out[0] == pytest.approx((110.0 - 105.0) / (110.0 - 90.0))


def test_vol_z_60() -> None:
    qv = np.arange(1, 61, dtype="float64")
    x = np.log1p(qv)
    mu = float(x.mean())
    sd = float(x.std(ddof=0))
    out = kline.vol_z_60(qv)
    assert np.isnan(out[:59]).all()
    assert out[59] == pytest.approx((x[59] - mu) / (sd + kline.EPS))


def test_vol_ratio_1440() -> None:
    qv = np.ones(1440, dtype="float64") * 100.0
    qv[1439] = 200.0
    out = kline.vol_ratio_1440(qv)
    mean = float(qv.mean())
    assert out[1439] == pytest.approx(math.log1p(200.0 / mean))


def test_tbr() -> None:
    out = kline.tbr(np.array([30.0]), np.array([100.0]))
    assert out[0] == pytest.approx(30.0 / 100.0)


def test_tbr_5() -> None:
    tbq = np.array([10.0, 20, 30, 40, 50])
    qv = np.array([100.0, 100, 100, 100, 100])
    out = kline.tbr_5(tbq, qv)
    assert out[4] == pytest.approx(150.0 / 500.0)


def test_cvd_norm() -> None:
    qv = np.ones(60, dtype="float64") * 100.0
    tbq = np.ones(60, dtype="float64") * 60.0
    out = kline.cvd_norm(tbq, qv)
    assert out[59] == pytest.approx((2.0 * 60.0 - 100.0) / 100.0)


def test_cvd_15() -> None:
    qv = np.ones(60, dtype="float64") * 100.0
    tbq = np.ones(60, dtype="float64") * 60.0
    out = kline.cvd_15(tbq, qv)
    assert out[59] == pytest.approx((15.0 * (2.0 * 60.0 - 100.0)) / (15.0 * 100.0))


def test_velocity() -> None:
    count = np.ones(60, dtype="float64") * 200.0
    count[59] = 400.0
    out = kline.velocity(count)
    mean = (59 * 200.0 + 400.0) / 60.0
    assert out[59] == pytest.approx(math.log1p(400.0 / mean))


def test_avg_trade_ratio() -> None:
    qv = np.ones(60, dtype="float64") * 1000.0
    count = np.ones(60, dtype="float64") * 100.0
    out = kline.avg_trade_ratio(qv, count)
    assert out[59] == pytest.approx(math.log1p((1000.0 / 100.0) / (1000.0 / 100.0)))


def test_rv_15() -> None:
    close = np.array([100.0, 101, 100, 102, 99, 103, 98, 104, 97, 105, 96, 106, 95, 107, 94, 108])
    r = np.log(close[1:] / close[:-1])
    out = kline.rv_15(close)
    assert out[15] == pytest.approx(float(r.std(ddof=0)))


def test_rv_ratio() -> None:
    close = np.arange(1, 242, dtype="float64") * 10.0
    r = np.log(close[1:] / close[:-1])
    std15 = float(r[-15:].std(ddof=0))
    std240 = float(r[-240:].std(ddof=0))
    out = kline.rv_ratio(close)
    assert out[240] == pytest.approx(std15 / (std240 + kline.EPS))


def test_atr_pct() -> None:
    n = 20
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close = np.full(n, 100.0)
    out = kline.atr_pct(high, low, close)
    # TR 恒为 1，ATR14 种子与递推恒为 1
    assert out[13] == pytest.approx(1.0 / 100.0)
    assert out[19] == pytest.approx(1.0 / 100.0)


def test_brk_60() -> None:
    n = 62
    high = np.ones(n) * 100.0
    high[60] = 105.0
    close = np.ones(n) * 101.0
    out = kline.brk_60(high, close)
    assert out[61] == pytest.approx((101.0 - 105.0) / 101.0)


def test_brk_240() -> None:
    n = 242
    high = np.ones(n) * 100.0
    high[240] = 105.0
    close = np.ones(n) * 101.0
    out = kline.brk_240(high, close)
    assert out[241] == pytest.approx((101.0 - 105.0) / 101.0)


def test_dd_60() -> None:
    n = 62
    low = np.ones(n) * 100.0
    low[60] = 95.0
    close = np.ones(n) * 99.0
    out = kline.dd_60(low, close)
    assert out[61] == pytest.approx((99.0 - 95.0) / 99.0)


def test_vwap_dev_60() -> None:
    qv = np.ones(60, dtype="float64") * 1000.0
    volume = np.ones(60, dtype="float64") * 10.0
    close = np.ones(60, dtype="float64") * 101.0
    out = kline.vwap_dev_60(qv, volume, close)
    vwap = 1000.0 / 10.0
    assert out[59] == pytest.approx((101.0 - vwap) / 101.0)
