"""执行模拟测试：下一根开盘成交、止损、资金费方向与冷却（docs/13 §4）。"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pytest

from tests.research.helpers import (
    START_MS,
    make_bars,
    make_market,
    make_research_config,
)
from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.data import HOUR_MS, MarkBars, MarketData
from trade.research.engine import Engine, ScenarioOptions
from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision, Target
from trade.research.view import View


class AlwaysLong:
    """测试用：每个调仓时刻都做多同一个币。"""

    name = "test_always_long"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 8

    def __init__(self, symbol: str = "AAAUSDT", sl: float = 0.03) -> None:
        self.symbol = symbol
        self.sl = sl

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        if view.close(self.symbol) is None:
            return Decision((), EXPOSURE_DIRECTIONAL)
        return Decision((Target(self.symbol, 1, self.sl),), EXPOSURE_DIRECTIONAL)


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=1000.0,
        mode="fixed_risk",
        position_frac=0.08,
        leverage=5,
        max_positions=10,
        base="equity",
        risk_per_trade=0.0075,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def _risk() -> RiskLimits:
    return RiskLimits(
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        halt_mode="strict",
        halt_reset_days=1,
    )


def _engine(market: MarketData, strategy, options: ScenarioOptions | None = None) -> Engine:
    config = make_research_config()
    return Engine(
        market=market,
        strategy=strategy,
        sizing_cfg=_sizing(),
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=0.0005,
        risk_limits=_risk(),
        options=options,
    )


def test_决策时刻所在K线开盘成交且含成本() -> None:
    """决策时刻 t 的订单在 open_time = t 的 K 线开盘成交（1 号第五部分 B 第 4 条）。"""
    market = make_market(n=160, drift_by_symbol={"AAAUSDT": 0.0005})
    engine = _engine(market, AlwaysLong())
    start = START_MS + 72 * HOUR_MS
    result = engine.run(start, START_MS + 140 * HOUR_MS)
    assert result.trades
    first = result.trades[0]
    bars = market.bars("AAAUSDT")
    entry_index = int(np.searchsorted(bars.open_time, first.entry_ts))
    assert first.entry_ts == start
    assert first.entry_price > float(bars.open[entry_index])


def test_止损触发按止损价加滑点成交并进入冷却() -> None:
    market = make_market(n=160)
    bars = make_bars("AAAUSDT", n=160, drift=0.0, amplitude=0.002)
    # 跌到 90：低于 3% 止损价（97）但高于逐仓强平价（81），止损按标记价格触发
    bars.close[100:] = 90.0
    bars.open[100:] = 90.0
    bars.high[100:] = 90.2
    bars.low[100:] = 89.8
    market.hourly["AAAUSDT"] = bars
    # D6：止损按标记价格触发（与实盘 working_type=MARK_PRICE 一致），因此标记价格同步替换
    market.mark["AAAUSDT"] = MarkBars(
        symbol="AAAUSDT",
        open_time=bars.open_time,
        open=bars.open,
        high=bars.high,
        low=bars.low,
        close=bars.close,
    )
    engine = _engine(market, AlwaysLong(sl=0.03))
    start = START_MS + 72 * HOUR_MS
    result = engine.run(start, START_MS + 150 * HOUR_MS)
    stops = [trade for trade in result.trades if trade.reason == "stop"]
    assert stops
    stopped = stops[0]
    assert stopped.gross_pnl < 0.0
    assert engine.cooldown.get("AAAUSDT") == stopped.exit_ts + 24 * HOUR_MS


def test_资金费方向为多头支付正费率() -> None:
    market = make_market(n=160, funding_by_symbol={"AAAUSDT": 0.001})
    engine = _engine(market, AlwaysLong())
    result = engine.run(START_MS + 72 * HOUR_MS, START_MS + 130 * HOUR_MS)
    assert result.trades
    assert result.trades[0].funding > 0.0


def test_未收盘K线不可见() -> None:
    market = make_market(n=100)
    view = View(market, START_MS + 80 * HOUR_MS)
    with pytest.raises(RuntimeError):
        view.require_closed(START_MS + 80 * HOUR_MS)
