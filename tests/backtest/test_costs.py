"""成本模型与情景测试（06 §4）。"""

from __future__ import annotations

import math

import pytest

from trade.backtest.costs import (
    breakeven_slip,
    exit_fee_rate_on_entry,
    half_spread,
    net_return_after_fees,
    scenario,
    slip,
)


def test_出场手续费按出场名义价值计算():
    """出场手续费 = notional × (1 + gross_return) × 费率，折算到入场名义价值乘 (1 + gross_return)。

    手算样例：gross_return = +2%、出场费率 2bp → 折算比例 0.0002 × 1.02 = 0.000204；
    gross_return = −2% → 0.0002 × 0.98 = 0.000196（06 §4.1）。
    """
    assert exit_fee_rate_on_entry(0.0002, 0.02) == pytest.approx(0.000204)
    assert exit_fee_rate_on_entry(0.0002, -0.02) == pytest.approx(0.000196)
    net = net_return_after_fees(
        gross_return=0.02, entry_fee_rate=0.0005, exit_fee_rate=0.0002, funding_rate=0.0001
    )
    assert net == pytest.approx(0.02 - 0.0005 - 0.000204 - 0.0001)


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


def test_实测点差情景按币覆盖半价差():
    """spread_measured 情景：有实测覆盖的币用实测半价差，其余用全局值（06 §4.3）。"""
    from trade.backtest.costs import spread_measured_scenario

    scen = spread_measured_scenario({"C01": 0.0003, "C02": 0.00025})
    assert scen.name == "spread_measured"
    assert scen.half_spread_min_for("C01", 0.0001) == pytest.approx(0.0003)
    assert scen.half_spread_min_for("C02", 0.0001) == pytest.approx(0.00025)
    assert scen.half_spread_min_for("C03", 0.0001) == pytest.approx(0.0001)
    # 未注入覆盖时与 base 口径一致
    assert scenario("spread_measured").half_spread_by_symbol == ()


def test_相对点差与分位手算样例():
    """相对点差 = (ask − bid) / mid；分位忽略非有限值（06 §4.3）。"""
    import numpy as np

    from scripts.spread_profile import half_spread_overrides, relative_spread, spread_quantiles

    bid = np.array([100.0, 100.0, 100.0])
    ask = np.array([100.02, 100.00, 100.04])
    spread = relative_spread(bid, ask)
    expected = np.array([0.02, 0.0, 0.04]) / np.array([100.01, 100.0, 100.02])
    assert np.allclose(spread, expected)
    quantiles = spread_quantiles(spread)
    assert quantiles["p50"] == pytest.approx(float(np.quantile(expected, 0.50)))
    assert quantiles["p95"] == pytest.approx(float(np.quantile(expected, 0.95)))
    # 全部无效时返回 NaN
    assert np.isnan(spread_quantiles(np.array([np.nan, np.nan]))["p50"])

    import pandas as pd

    profile = pd.DataFrame({"alias": ["C01", "C02"], "signal_p75": [0.0004, np.nan]})
    overrides = half_spread_overrides(profile)
    assert overrides["C01"] == pytest.approx(0.0002)
    assert overrides["C02"] == pytest.approx(0.0002)  # 用全币中位数 0.0004 / 2
    # net_return(x) = 0.002 - x，零点在 0.002
    root = breakeven_slip(net_return=lambda x: 0.002 - x)
    assert root == pytest.approx(0.002, abs=1e-6)


def test_breakeven_slip无零点返回0():
    # 滑点为 0 时仍亏损 → 返回 0
    assert breakeven_slip(net_return=lambda x: -0.001) == pytest.approx(0.0)
