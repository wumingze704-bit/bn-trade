"""风控规则 R1~R14 单测。

规格：docs/07-实盘与风控.md §7.1
"""

from __future__ import annotations

from trade.common.sizing import SizingConfig
from trade.live.config import HealthConfig, RiskConfig
from trade.live.risk import (
    RiskContext,
    RiskEngine,
    check_chase,
    check_consecutive_losses,
    check_cooldown,
    check_daily_loss,
    check_drawdown,
    check_max_positions,
    check_spread,
)


def _ctx(**overrides: object) -> RiskContext:
    base = dict(
        n_open_positions=0,
        n_positions_for_symbol=0,
        bars_since_stop=100,
        chase_ratio=0.001,
        daily_pnl=0.0,
        day_start_equity=1000.0,
        consecutive_losses=0,
        current_equity=1000.0,
        peak_equity=1000.0,
        spread_bp=1.0,
        median_spread_bp=1.0,
        clock_drift_ms=0,
        order_errors_5m=0,
        equity_floor=False,
        notional_capped=False,
    )
    base.update(overrides)
    return RiskContext(**base)  # type: ignore[arg-type]


def _engine() -> RiskEngine:
    sizing = SizingConfig(
        initial_equity=1000,
        mode="fixed_fraction",
        position_frac=0.2,
        leverage=5,
        max_positions=3,
        base="equity",
        risk_per_trade=0.005,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )
    return RiskEngine(sizing, RiskConfig(), HealthConfig())


def test_check_max_positions() -> None:
    assert check_max_positions(2, 3) is True
    assert check_max_positions(3, 3) is False


def test_check_chase() -> None:
    assert check_chase(0.003, 0.003) is True
    assert check_chase(0.004, 0.003) is False


def test_check_cooldown() -> None:
    assert check_cooldown(15, 15) is True
    assert check_cooldown(14, 15) is False


def test_check_daily_loss() -> None:
    assert check_daily_loss(-49.0, 1000.0, 0.05) is True
    assert check_daily_loss(-51.0, 1000.0, 0.05) is False


def test_check_consecutive_losses() -> None:
    assert check_consecutive_losses(5, 6) is True
    assert check_consecutive_losses(6, 6) is False


def test_check_drawdown() -> None:
    assert check_drawdown(800.0, 1000.0, 0.2) is True
    assert check_drawdown(810.0, 1000.0, 0.2) is False


def test_check_spread() -> None:
    assert check_spread(2.0, 1.0, 3.0) is True
    assert check_spread(4.0, 1.0, 3.0) is False


def test_engine_rejects_r2_max_positions() -> None:
    decision = _engine().check_open(_ctx(n_open_positions=3))
    assert decision.allowed is False
    assert decision.reason == "R2_max_positions"


def test_engine_allows_clean_context() -> None:
    decision = _engine().check_open(_ctx())
    assert decision.allowed is True


def test_engine_halt_r6() -> None:
    decision = _engine().check_halt(_ctx(daily_pnl=-90.0, day_start_equity=1000.0))
    assert decision.halt is True
    assert decision.reason == "R6_daily_loss_halt"


def test_engine_halt_r8() -> None:
    decision = _engine().check_halt(_ctx(current_equity=790.0, peak_equity=1000.0))
    assert decision.halt is True
    assert decision.reason == "R8_max_drawdown"
