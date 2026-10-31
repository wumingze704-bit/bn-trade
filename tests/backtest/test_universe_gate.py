"""交易范围闸门测试（T0.15，06 §3.1 条件 8）。

不在当月交易范围 `U(R)` 的分钟不开仓；新进入币前 2 天不开仓；跌出范围的持仓
正常出场（出场不受闸门约束）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import BacktestConfig, TradeRule, precompute_trades
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=10,
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


def _flat_bars(n: int) -> pd.DataFrame:
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": close,
            "hi_post_1s": close * 1.001,
            "lo_post_1s": close * 0.999,
        }
    )


def test_不在交易范围的分钟不开仓() -> None:
    n = 200
    bars = _flat_bars(n)
    # 只有 [60, 120) 在当月交易范围
    trade_mask = (np.arange(n) >= 60) & (np.arange(n) < 120)
    signals = pd.DataFrame(
        {
            "open_time": [bars.loc[70, "open_time"], bars.loc[140, "open_time"]],
            "p": [1.0, 1.0],
            "m": [0.0, 0.0],
            "is_sample": [1, 1],
        }
    )
    trades, skipped = precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=TradeRule(tick_size=0.01, step_size=0.01, min_notional=5.0),
        scen=scenario("base"),
        fees=FeeSchedule(maker=0.0002, taker=0.0005),
        backtest_cfg=BacktestConfig(threshold=0.5),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        trade_mask=trade_mask,
    )
    # 只有 t=70 在范围内，且其出场（timeout）可落在范围外 —— 跌出范围持仓正常出场
    assert len(trades) == 1
    assert trades[0].signal_open_time == int(bars.loc[70, "open_time"])
    reasons = {s.reason for s in skipped}
    assert "not_in_universe" in reasons
