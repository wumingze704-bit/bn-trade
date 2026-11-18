"""R2-5 多周期动量与流动性特征的手算测试。

规格：docs/04-特征与标签.md §4.8
"""

from __future__ import annotations

import numpy as np

from trade.features.context import weekday_cos, weekday_sin
from trade.features.kline import bars_since_brk_60, dist_24h_high, log_qv_60, r_240, r_1440


def test_r_240与r_1440为对数变化() -> None:
    close = np.exp(np.arange(1500) * 1e-4)
    got = r_240(close)
    assert abs(float(got[300]) - float(np.log(close[300] / close[60]))) < 1e-12
    assert np.isnan(got[239])
    got_1440 = r_1440(close)
    assert abs(float(got_1440[1450]) - float(np.log(close[1450] / close[10]))) < 1e-12


def test_距24小时高点距离为负() -> None:
    high = np.full(1600, 100.0)
    high[1500] = 120.0
    close = np.full(1600, 110.0)
    got = dist_24h_high(high, close)
    # index 1600-1 的 1440 窗口包含 1500 的 120 → c/120-1 < 0
    assert float(got[1599]) < 0.0
    assert abs(float(got[1599]) - (110.0 / 120.0 - 1.0)) < 1e-12


def test_成交额水平取log1p均值() -> None:
    qv = np.full(100, 99.0)
    got = log_qv_60(qv)
    assert abs(float(got[99]) - np.log1p(99.0)) < 1e-12


def test_距上次突破的分钟数() -> None:
    high = np.full(200, 100.0)
    close = np.full(200, 100.0)
    close[100] = 101.0  # 该分钟 brk_60 > 0
    high[100] = 101.0
    got = bars_since_brk_60(high, close)
    assert float(got[100]) == 0.0
    assert float(got[103]) == 3.0
    assert float(got[150]) == 50.0


def test_星期编码为周期信号() -> None:
    open_time = np.arange(14, dtype="int64") * 86_400_000
    s = weekday_sin(open_time)
    c = weekday_cos(open_time)
    assert abs(float(s[0] ** 2 + c[0] ** 2) - 1.0) < 1e-12
    # 相隔 7 天应完全相同
    np.testing.assert_allclose(s[:7], s[7:], rtol=0.0, atol=0.0)
