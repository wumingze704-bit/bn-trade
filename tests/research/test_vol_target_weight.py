"""模式 B 的名义按“策略权重 ÷ 波动率”分配（docs/13 §3 第 2 条；R12 的 BTC 对冲腿依赖此规则）。"""

from __future__ import annotations

import numpy as np
import pytest

from tests.research.helpers import (
    START_MS,
    execution_config,
    make_daily_returns_bars,
    make_market,
    portfolio_config,
)
from trade.common.sizing import SizingConfig
from trade.research.data import HOUR_MS
from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision, Target
from trade.research.portfolio import plan_positions
from trade.research.view import View


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=10_000.0,
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
        sizing_mode="vol_target",
        target_vol=0.05,
        vol_lookback_days=30,
        cov_lookback_days=60,
        beta_lookback_days=60,
        beta_abs_max=0.10,
        ls_ratio_range=(0.5, 2.0),
    )


def _oscillating(amplitude: float, days: int) -> np.ndarray:
    """交替正负的日对数收益（标准差约等于 amplitude）。"""
    return np.asarray([amplitude if index % 2 == 0 else -amplitude for index in range(days)])


def test_模式B的名义与策略权重成正比() -> None:
    market = make_market(n=240, history_days=60)
    market.daily["AAAUSDT"] = make_daily_returns_bars("AAAUSDT", _oscillating(0.010, 90))
    market.daily["BBBUSDT"] = make_daily_returns_bars("BBBUSDT", _oscillating(0.020, 90))
    market.daily["BTCUSDT"] = make_daily_returns_bars("BTCUSDT", _oscillating(0.030, 90))
    view = View(market, START_MS + 10 * HOUR_MS)
    decision = Decision(
        (
            Target("AAAUSDT", -1, 0.15, weight=1.0),
            Target("BBBUSDT", -1, 0.15, weight=4.0),
        ),
        EXPOSURE_DIRECTIONAL,
    )
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    by_symbol = {position.symbol: position.notional for position in planned}
    assert set(by_symbol) == {"AAAUSDT", "BBBUSDT"}
    # 权重 1 ÷ σ(1%) = 100，权重 4 ÷ σ(2%) = 200 → 名义比 0.5
    assert by_symbol["AAAUSDT"] / by_symbol["BBBUSDT"] == pytest.approx(0.5, rel=0.05)
    assert stats.dropped == 0


def test_权重为零的目标被丢弃并计数() -> None:
    market = make_market(n=240, history_days=60)
    market.daily["AAAUSDT"] = make_daily_returns_bars("AAAUSDT", _oscillating(0.010, 90))
    market.daily["BBBUSDT"] = make_daily_returns_bars("BBBUSDT", _oscillating(0.010, 90))
    market.daily["BTCUSDT"] = make_daily_returns_bars("BTCUSDT", _oscillating(0.030, 90))
    view = View(market, START_MS + 10 * HOUR_MS)
    decision = Decision((Target("AAAUSDT", 1, 0.15, weight=0.0),), EXPOSURE_DIRECTIONAL)
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned == []
    assert stats.dropped == 1
