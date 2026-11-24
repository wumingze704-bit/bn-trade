"""实现 β、alpha 夏普与达标项 j 测试（docs/13 §7；1 号第七部分第 5 条 j 款）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.research.metrics import alpha_sharpe, annualized_sharpe, realized_beta
from trade.research.pipeline import _checks, _delay1_max_excess

_INDEX = pd.date_range("2024-01-01", periods=240, freq="D", tz="UTC")


def test_实现β按BTC日收益回归手算() -> None:
    """β = cov(r, b) ÷ var(b)，与直接手算一致。"""
    rng = np.random.default_rng(3)
    benchmark = pd.Series(rng.normal(0.0, 0.02, _INDEX.size), index=_INDEX)
    strategy = 1.7 * benchmark + pd.Series(rng.normal(0.0, 0.005, _INDEX.size), index=_INDEX)
    expected = float(np.cov(strategy, benchmark, ddof=1)[0, 1] / np.var(benchmark, ddof=1))
    assert realized_beta(strategy, benchmark) == pytest.approx(expected, rel=1e-12)


def test_alpha夏普用剔除β后的超额收益手算() -> None:
    rng = np.random.default_rng(5)
    benchmark = pd.Series(rng.normal(0.0, 0.02, _INDEX.size), index=_INDEX)
    strategy = 1.3 * benchmark + pd.Series(rng.normal(0.001, 0.004, _INDEX.size), index=_INDEX)
    beta = realized_beta(strategy, benchmark)
    excess = strategy - beta * benchmark
    assert alpha_sharpe(strategy, benchmark) == pytest.approx(annualized_sharpe(excess), rel=1e-12)


def test_BTC方差为零时β返回零() -> None:
    """常数价格的 BTC 日收益方差为 0，β 无法定义，按 0 处理（不抛异常）。"""
    benchmark = pd.Series(np.zeros(_INDEX.size), index=_INDEX)
    strategy = pd.Series(np.linspace(-0.01, 0.01, _INDEX.size), index=_INDEX)
    assert realized_beta(strategy, benchmark) == 0.0


def _metrics(beta: float, alpha: float) -> dict[str, object]:
    return {
        "bootstrap": {"lower_bp": 1.0},
        "deflated_sharpe": 0.9,
        "max_drawdown": 0.1,
        "realized_beta": beta,
        "alpha_sharpe": alpha,
    }


def _fold_stats() -> list[dict[str, object]]:
    return [{"net_return": 0.01, "long_gross": 1.0, "short_gross": 0.5} for _ in range(5)]


def _scenarios() -> dict[str, dict[str, float]]:
    return {
        "cost2x": {"net_return": 0.1},
        "shuffle": {"annualized_sharpe": 0.1},
        "delay1": {"annualized_sharpe": 0.5},
        "long_only": {"net_pnl": 1.0},
        "short_only": {"net_pnl": 0.0},
    }


def test_达标项j只对中性策略判定() -> None:
    base = dict(
        base_sharpe=1.2,
        fold_stats=_fold_stats(),
        scenario_stats=_scenarios(),
        exec_stats={},
    )
    neutral = _checks(exposure="neutral", metrics=_metrics(0.05, 1.0), **base)
    assert neutral["beta_abs_le_030"] is True
    assert neutral["alpha_sharpe_ge_08"] is True
    failing = _checks(exposure="neutral", metrics=_metrics(0.40, 0.7), **base)
    assert failing["beta_abs_le_030"] is False
    assert failing["alpha_sharpe_ge_08"] is False
    directional = _checks(exposure="directional", metrics=_metrics(0.05, 1.0), **base)
    assert "beta_abs_le_030" not in directional
    assert "alpha_sharpe_ge_08" not in directional


def test_delay1条件在容忍度为0时要求严格低于base() -> None:
    """未登记例外的轮次沿用 g 款的严格口径（docs/13 §7）。"""
    scenarios = _scenarios()
    scenarios["delay1"] = {"annualized_sharpe": 0.55}
    checks = _checks(
        exposure="directional",
        base_sharpe=0.5,
        metrics=_metrics(0.05, 1.0),
        fold_stats=_fold_stats(),
        scenario_stats=scenarios,
        exec_stats={},
    )
    assert checks["delay1_lt_base"] is False


def test_delay1条件按网格容忍度判定() -> None:
    """R11、R12 登记 delay1 ≤ base + 0.10（1 号第八部分）。"""
    scenarios = _scenarios()
    scenarios["delay1"] = {"annualized_sharpe": 0.55}
    passing = _checks(
        exposure="directional",
        base_sharpe=0.5,
        metrics=_metrics(0.05, 1.0),
        fold_stats=_fold_stats(),
        scenario_stats=scenarios,
        exec_stats={},
        delay1_max_excess=0.10,
    )
    assert passing["delay1_lt_base"] is True
    scenarios["delay1"] = {"annualized_sharpe": 0.65}
    failing = _checks(
        exposure="directional",
        base_sharpe=0.5,
        metrics=_metrics(0.05, 1.0),
        fold_stats=_fold_stats(),
        scenario_stats=scenarios,
        exec_stats={},
        delay1_max_excess=0.10,
    )
    assert failing["delay1_lt_base"] is False


def test_网格delay1容忍度必须是非负数() -> None:
    """非法容忍度直接抛错，不静默回退（红线 12）。"""
    with pytest.raises(ValueError, match="delay1_max_excess"):
        _delay1_max_excess({"round": "R11", "delay1_max_excess": -0.1})
    assert _delay1_max_excess({"round": "R11", "delay1_max_excess": 0.1}) == pytest.approx(0.1)
    assert _delay1_max_excess({"round": "R1"}) == 0.0
