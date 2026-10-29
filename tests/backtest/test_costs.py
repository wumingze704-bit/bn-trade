"""成本模型与情景测试（06 §4）。"""

from __future__ import annotations

import math

import pytest

from trade.backtest.costs import breakeven_slip, half_spread, scenario, slip


def test_half_spread取较大值():
    assert half_spread(0.0004, 0.0001) == pytest.approx(0.0002)  # 0.5*0.0004
    assert half_spread(0.0001, 0.0002) == pytest.approx(0.0002)  # min 生效


def test_slip平方根冲击模型():
    # slip = slip_min + Y * rv * sqrt(notional/qv)
    r = slip(slip_min=0.0001, Y=1.0, rv_15_raw=0.001, notional=100.0, qv=1e6)
    assert r == pytest.approx(0.0001 + 0.001 * math.sqrt(100.0 / 1e6))


def test_情景参数():
    assert scenario("base").latency_ms == 1000
    assert scenario("stress").latency_ms == 3000
    assert scenario("stress").tp_taker is True
    assert scenario("hell").fixed_slip == pytest.approx(0.0015)
    assert scenario("hell").all_taker is True
    with pytest.raises(ValueError):
        scenario("nope")


def test_breakeven_slip二分收敛():
    # net_return(x) = 0.002 - x，零点在 0.002
    root = breakeven_slip(net_return=lambda x: 0.002 - x)
    assert root == pytest.approx(0.002, abs=1e-6)


def test_breakeven_slip无零点返回0():
    # 滑点为 0 时仍亏损 → 返回 0
    assert breakeven_slip(net_return=lambda x: -0.001) == pytest.approx(0.0)
