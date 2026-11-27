"""指标与达标项测试（docs/13 §7；1 号第七部分第 5 条 a~k）。

阈值来自 docs/13 §7：夏普 ≥ 0.8、5 折中 3 折为正、90% bootstrap 下限 > 0、
DSR ≥ 0.50、cost2x 净收益 > 0、shuffle 全部种子低于 base、delay1 ≤ base + 0.30、
中性多空价差毛收益 ≥ 3 折为正、四个档位至少一档满足可执行性、β ≤ 0.30 且 alpha 夏普 ≥ 0.6、
只做空方向扣除 X0 的超额夏普 ≥ 0.6。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.research.dsr import deflated_sharpe_research
from trade.research.metrics import (
    alpha_sharpe,
    annualized_sharpe,
    block_bootstrap_mean,
    compound_monthly,
    longest_drawdown_days,
    realized_beta,
    worst_week,
)
from trade.research.pipeline import _checks

_INDEX = pd.date_range("2024-01-01", periods=240, freq="D", tz="UTC")

_PASSING_TIER = {
    "attempted": 100.0,
    "dropped_min_notional": 10.0,
    "net_return": 0.2,
    "annualized_sharpe": 0.8,
}


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


def test_复合月化按期末比期初手算() -> None:
    """240 天、期末 1.2 倍的复合月化 = 1.2^(1/月数) − 1。"""
    equity = pd.Series(np.linspace(100.0, 120.0, _INDEX.size), index=_INDEX)
    months = (_INDEX[-1] - _INDEX[0]).days / 30.4375
    assert compound_monthly(equity) == pytest.approx(1.2 ** (1.0 / months) - 1.0, rel=1e-12)


def test_最长回撤持续天数按水下连续天数计() -> None:
    """先涨后连跌 5 天：最长回撤持续天数 = 5。"""
    values = [100.0, 110.0, 108.0, 106.0, 104.0, 102.0, 100.0, 120.0]
    equity = pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"))
    assert longest_drawdown_days(equity) == pytest.approx(5.0)


def test_最差单周取周收益最小值() -> None:
    """两周各 −5% 与 +2%：最差单周 = −5%。"""
    index = pd.date_range("2024-01-01", periods=15, freq="D")
    values = [100.0] * 7 + [95.0] * 7 + [96.9]
    equity = pd.Series(values, index=index)
    assert worst_week(equity) == pytest.approx(0.95 / 1.0 - 1.0, abs=1e-9)


def test_bootstrap用移动块且输出90与95区间() -> None:
    """块长 7 天的移动块 bootstrap：90% 区间比 95% 区间窄，且上下界顺序正确。"""
    rng = np.random.default_rng(7)
    values = rng.normal(0.001, 0.01, 400)
    out = block_bootstrap_mean(values, block_days=7, iters=500, seed=42)
    assert out["lower_95"] <= out["lower_90"] <= out["upper_90"] <= out["upper_95"]
    assert out["mean"] == pytest.approx(float(values.mean()), rel=1e-12)
    with pytest.raises(ValueError, match="块长"):
        block_bootstrap_mean(values, block_days=0, iters=10, seed=1)


def test_DSR基准的均值项不为负() -> None:
    """试验夏普均值为负时，基准按 max(0, 均值) 计（1 号 B 第 15 条 l 款）。"""
    negative = deflated_sharpe_research(0.05, [-0.3, -0.2, -0.1], skew=0.0, kurtosis=3.0, n_obs=500)
    shifted = deflated_sharpe_research(0.05, [-0.1, 0.0, 0.1], skew=0.0, kurtosis=3.0, n_obs=500)
    # 两组的标准差相同、均值项都被截到 0，因此 DSR 相同
    assert negative == pytest.approx(shifted, rel=1e-12)


def _metrics(beta: float, alpha: float) -> dict[str, object]:
    return {
        "bootstrap": {"lower_90": 0.0001, "lower_95": 0.00005},
        "deflated_sharpe": 0.6,
        "max_drawdown": 0.4,
        "realized_beta": beta,
        "alpha_sharpe": alpha,
    }


def _fold_stats(*, spread_positive_folds: int = 5) -> list[dict[str, object]]:
    return [
        {
            "net_return": 0.01,
            "long_gross": 1.0 if index < spread_positive_folds else -3.0,
            "short_gross": 0.5,
        }
        for index in range(5)
    ]


def _scenarios() -> dict[str, dict[str, float]]:
    return {
        "cost2x": {"net_return": 0.1},
        "delay1": {"annualized_sharpe": 0.5},
        "long_only": {"net_pnl": 1.0},
        "short_only": {"net_pnl": 0.0},
    }


def _checks_for(exposure: str, *, family: str = "xs_momentum_ls", **overrides: object):
    base: dict[str, object] = {
        "family": family,
        "exposure": exposure,
        "base_sharpe": 1.2,
        "metrics": _metrics(0.05, 1.0),
        "fold_stats": _fold_stats(),
        "scenario_stats": _scenarios(),
        "shuffle_sharpes": [0.1, 0.2, 0.3],
        "exec_tiers": {"standard": _PASSING_TIER},
        "short_excess_sharpe": None,
    }
    base.update(overrides)
    return _checks(**base)


def test_达标项按第三批阈值判定() -> None:
    """夏普 0.8、3 折为正、90% 下限 > 0、DSR 0.50，回撤不设门槛。"""
    checks = _checks_for("directional")
    assert checks["sharpe_ge_080"] is True
    assert checks["folds_positive_ge_3"] is True
    assert checks["bootstrap_lower_gt_0"] is True
    assert checks["dsr_ge_050"] is True
    assert "mdd_le_020" not in checks
    low = _checks_for(
        "directional",
        base_sharpe=0.7,
        metrics={**_metrics(0.05, 1.0), "deflated_sharpe": 0.4},
        fold_stats=[
            {"net_return": -0.01, "long_gross": -1.0, "short_gross": -1.0},
            {"net_return": -0.01, "long_gross": -1.0, "short_gross": -1.0},
            {"net_return": -0.01, "long_gross": -1.0, "short_gross": -1.0},
            {"net_return": 0.01, "long_gross": 1.0, "short_gross": 1.0},
            {"net_return": 0.01, "long_gross": 1.0, "short_gross": 1.0},
        ],
    )
    assert low["sharpe_ge_080"] is False
    assert low["folds_positive_ge_3"] is False
    assert low["dsr_ge_050"] is False


def test_shuffle全部种子必须低于base且不适用的方向视为通过() -> None:
    """shuffle 的全部种子都要低于 base；X0 的 shuffle 记“不适用”（docs/13 §7）。"""
    above = _checks_for("directional", shuffle_sharpes=[0.1, 1.3])
    assert above["shuffle_all_below_base"] is False
    below = _checks_for("directional", shuffle_sharpes=[0.1, 0.2])
    assert below["shuffle_all_below_base"] is True
    not_applicable = _checks_for(
        "directional", family="short_basket_baseline", shuffle_sharpes=None
    )
    assert not_applicable["shuffle_all_below_base"] is True
    with pytest.raises(ValueError, match="shuffle"):
        _checks_for("directional", shuffle_sharpes=None)


def test_delay1对所有方向都要求不超过base加030() -> None:
    """第三批决定：delay1 ≤ base + 0.30，所有方向统一，不另设例外。"""
    scenarios = _scenarios()
    scenarios["delay1"] = {"annualized_sharpe": 1.49}
    assert (
        _checks_for("directional", base_sharpe=1.2, scenario_stats=scenarios)[
            "delay1_le_base_plus_030"
        ]
        is True
    )
    scenarios["delay1"] = {"annualized_sharpe": 1.51}
    assert (
        _checks_for("directional", base_sharpe=1.2, scenario_stats=scenarios)[
            "delay1_le_base_plus_030"
        ]
        is False
    )


def test_中性策略用多空价差毛收益且alpha阈值为06() -> None:
    """h 款：多空价差毛收益（两腿之和）在 5 折中至少 3 折为正；j 款 alpha 夏普 ≥ 0.6。"""
    passing = _checks_for("neutral", fold_stats=_fold_stats(spread_positive_folds=3))
    assert passing["side_condition"] is True
    failing = _checks_for("neutral", fold_stats=_fold_stats(spread_positive_folds=2))
    assert failing["side_condition"] is False
    assert _checks_for("neutral", metrics=_metrics(0.05, 0.65))["alpha_sharpe_ge_06"] is True
    assert _checks_for("neutral", metrics=_metrics(0.05, 0.55))["alpha_sharpe_ge_06"] is False
    assert _checks_for("neutral", metrics=_metrics(0.40, 1.0))["beta_abs_le_030"] is False
    directional = _checks_for("directional")
    assert "beta_abs_le_030" not in directional
    assert "alpha_sharpe_ge_06" not in directional


def test_可执行性四档至少一档满足() -> None:
    """i 款：丢弃 ≤ 25%、净收益 > 0、夏普 ≥ 信号口径 60%，四个档位中至少一个满足。"""
    failing_tier = {
        "attempted": 100.0,
        "dropped_min_notional": 26.0,
        "net_return": 0.2,
        "annualized_sharpe": 0.9,
    }
    assert (
        _checks_for("directional", exec_tiers={"standard": failing_tier})["exec_tier_ok"] is False
    )
    tiers = {"standard": failing_tier, "extreme": _PASSING_TIER}
    assert _checks_for("directional", exec_tiers=tiers)["exec_tier_ok"] is True


def test_只做空方向要求扣除X0后的超额夏普() -> None:
    """l 款：只做空的方向性策略扣除 X0 后超额夏普 ≥ 0.6；其他方向不判定。"""
    assert (
        _checks_for("directional", family="short_seasoned_listing", short_excess_sharpe=0.61)[
            "short_excess_sharpe_ge_06"
        ]
        is True
    )
    assert (
        _checks_for("directional", family="short_seasoned_listing", short_excess_sharpe=0.59)[
            "short_excess_sharpe_ge_06"
        ]
        is False
    )
    assert "short_excess_sharpe_ge_06" not in _checks_for("directional", family="ts_trend")
