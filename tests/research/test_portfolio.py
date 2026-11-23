"""组合层上限、中性平衡与丢弃统计测试（docs/13 §3）。"""

from __future__ import annotations

import pytest

from tests.research.helpers import (
    START_MS,
    execution_config,
    make_market,
    portfolio_config,
)
from trade.common.sizing import SizingConfig
from trade.research.data import HOUR_MS
from trade.research.interface import EXPOSURE_DIRECTIONAL, EXPOSURE_NEUTRAL, Decision, Target
from trade.research.portfolio import plan_positions
from trade.research.view import View


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=100.0,
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


def test_中性策略多空名义相等() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision(
        (
            Target("AAAUSDT", 1, 0.03),
            Target("BBBUSDT", 1, 0.05),
            Target("CCCUSDT", -1, 0.03),
            Target("DDDUSDT", -1, 0.05),
        ),
        EXPOSURE_NEUTRAL,
    )
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned
    assert stats.long_notional == pytest.approx(stats.short_notional, rel=1e-9)
    assert stats.long_notional + stats.short_notional <= 2.0 * 100.0 + 1e-9
    assert (stats.long_notional + stats.short_notional) / 5.0 <= 0.95 * 100.0 + 1e-9


def test_净敞口与总名义上限() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision(
        tuple(Target(symbol, 1, 0.02) for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")),
        EXPOSURE_DIRECTIONAL,
    )
    small = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(net_exposure_cap=0.1, total_notional_cap=2.0),
        fee_taker=0.0005,
        blocked=set(),
    )[1]
    assert small.long_notional <= 0.1 * 100.0 + 1e-6


def test_冷却中的币被丢弃() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision((Target("AAAUSDT", 1, 0.03),), EXPOSURE_NEUTRAL)
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked={"AAAUSDT"},
    )
    assert planned == []
    assert stats.dropped == 1


def test_止损距离超过上限被拒绝() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision((Target("AAAUSDT", 1, 0.30),), EXPOSURE_NEUTRAL)
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned == []
    assert stats.dropped == 1
