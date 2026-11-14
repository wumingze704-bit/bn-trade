"""按行阈值 θ 的开仓与 S4 按行扰动测试（06 §3.1 条件 1、§7 S4、§8）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.backtest.test_engine import _MIN, _T0, _backtest, _fees, _rule, _sizing
from trade.backtest.costs import scenario
from trade.backtest.engine import precompute_trades, run_backtest, simulate_portfolio
from trade.common.exits import ExitConfig


def _bars(n: int = 200) -> pd.DataFrame:
    """构造价格恒定的可交易 bars（ATR 与 60 根成交额窗口都可用）。"""
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 100.0)
    high = close * 1.002
    low = close * 0.998
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "kline_missing": 0,
            "aggtrades_missing": 0,
            "p_1s": close,
            "p_1s_filled": np.zeros(n, dtype="int64"),
            "hi_post_1s": high,
            "lo_post_1s": low,
        }
    )


def _signals(thetas: list[float], p: float = 0.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["A"] * len(thetas),
            "open_time": [_T0 + (70 + 50 * i) * _MIN for i in range(len(thetas))],
            "p": [p] * len(thetas),
            "m": [0.0] * len(thetas),
            "is_sample": [1] * len(thetas),
            "theta": thetas,
        }
    )


def _precompute(bars: pd.DataFrame, signals: pd.DataFrame):
    return precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=_rule(),
        scen=scenario("base"),
        fees=_fees(),
        backtest_cfg=_backtest(threshold=0.0),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
    )


def test_按行阈值开仓():
    """p = 0.50 的信号：θ = 0.45 的一折开仓，θ = 0.55 的一折不开（06 §8）。"""
    bars = _bars()
    signals = _signals([0.45, 0.55])
    result, _ = run_backtest(
        {"A": bars},
        signals,
        {"A": _rule()},
        {"A": "train"},
        scen_name="base",
        fees=_fees(),
        backtest_cfg=_backtest(threshold=0.0),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(base="initial"),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        initial_equity=100.0,
    )
    assert [trade.signal_open_time for trade in result.trades] == [_T0 + 70 * _MIN]


def test_缺theta列时报错():
    """signals 缺 theta 列时直接报错，不回退全局阈值（红线 13）。"""
    signals = _signals([0.45]).drop(columns=["theta"])
    with pytest.raises(ValueError, match="theta"):
        _precompute(_bars(), signals)


def test_S4按行扰动():
    """S4 对每行 θ 统一加减 threshold_perturb（06 §7 S4）。"""
    bars = _bars()
    signals = _signals([0.45, 0.55])
    rules = {"A": _rule()}
    groups = {"A": "train"}
    common = dict(
        scen_name="base",
        fees=_fees(),
        backtest_cfg=_backtest(threshold=0.0),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(base="initial"),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        initial_equity=100.0,
    )
    base, _ = run_backtest({"A": bars}, signals, rules, groups, **common)
    below, _ = run_backtest({"A": bars}, signals, rules, groups, theta_offset=-0.06, **common)
    above, _ = run_backtest({"A": bars}, signals, rules, groups, theta_offset=0.06, **common)
    # p = 0.50：θ 下调 0.06 后两行都开仓；上调 0.06 后两行都不开仓
    assert len(base.trades) == 1
    assert len(below.trades) == 2
    assert len(above.trades) == 0


def test_按行阈值的S4可用同一份预计算():
    """同一份 p ≥ θ_min − Δ 的预计算结果可在不同扰动下复用（06 §3.4）。"""
    bars = _bars()
    signals = _signals([0.45, 0.55])
    trades, _ = _precompute(bars, signals)
    assert {trade.theta for trade in trades} == {0.45, 0.55}
    result = simulate_portfolio(
        trades, [], sizing_cfg=_sizing(base="initial"), backtest_cfg=_backtest(), fees=_fees()
    )
    assert result.final_equity > 0.0
