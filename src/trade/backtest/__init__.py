"""回测与策略健全性检验包。

规格：docs/02-系统架构与部署.md §2
"""

from trade.backtest.costs import FeeSchedule, Scenario, breakeven_slip, half_spread, scenario, slip
from trade.backtest.engine import (
    BacktestConfig,
    PortfolioResult,
    PrecomputedTrade,
    TradeRule,
    precompute_trades,
    run_backtest,
    simulate_portfolio,
)
from trade.backtest.r0 import R0Config, r0_signals_for_symbol

__all__ = [
    "BacktestConfig",
    "FeeSchedule",
    "PortfolioResult",
    "PrecomputedTrade",
    "R0Config",
    "Scenario",
    "TradeRule",
    "breakeven_slip",
    "half_spread",
    "precompute_trades",
    "r0_signals_for_symbol",
    "run_backtest",
    "scenario",
    "simulate_portfolio",
    "slip",
]
