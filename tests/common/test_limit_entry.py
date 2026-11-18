"""回踩挂单入场（`entry_mode=limit_pullback`）的共用实现测试。

规格：docs/06-回测引擎.md §3.2、§3.4；AGENTS.md §6 红线 4（标签与回测同一份入场代码）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import BacktestConfig, TradeRule, precompute_trades
from trade.common.anchors import plan_limit_entry
from trade.common.exits import ExitConfig, exit_anchor_spec
from trade.common.sizing import SizingConfig
from trade.labels.exit_sim import exit_sim

_T0 = 1_767_225_600_000  # 2026-01-01T00:00:00Z
_MIN = 60_000


def _spec() -> object:
    return exit_anchor_spec(
        ExitConfig(type="trail"),
        k_sl=2.5,
        sl_min=0.01,
        sl_max=0.025,
        half_spread_min=0.0001,
        slip_min=0.0001,
        Y=1.0,
        fixed_slip=None,
        fee_taker=0.0005,
        notional_ref=90.0,
    )


def _bars(n: int = 200, dip_at: int = 60, dip_bp: float = 40.0) -> pd.DataFrame:
    """构造单调上涨 + 在 `dip_at` 处回踩 `dip_bp` 的分钟 bars。"""
    open_time = np.arange(n) * _MIN + _T0
    close = 100.0 * np.exp(np.arange(n) * 1e-5)
    high = close * 1.0005
    low = close * 0.9995
    low[dip_at] = close[dip_at - 1] * (1.0 - dip_bp / 1e4) - 0.01
    v = close.copy()
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": v,
            "high": np.maximum(high, v),
            "low": np.minimum(low, v),
            "close": v,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "kline_missing": np.zeros(n, dtype="int64"),
            "aggtrades_missing": np.zeros(n, dtype="int64"),
            "p_1s": v,
            "p_1s_filled": np.zeros(n, dtype="int64"),
            "hi_post_1s": np.maximum(high, v),
            "lo_post_1s": np.minimum(low, v),
        }
    )


def _plan(bars: pd.DataFrame, *, offset_bp: float = 30.0, expiry: int = 5):
    return plan_limit_entry(
        close=bars["close"].to_numpy(dtype="float64"),
        low=bars["low"].to_numpy(dtype="float64"),
        kline_missing=bars["kline_missing"].to_numpy(dtype="int64"),
        atr_pct=np.full(len(bars), 0.004),
        tick_size=0.01,
        offset_bp=offset_bp,
        expiry_bars=expiry,
        spec=_spec(),  # type: ignore[arg-type]
    )


def test_回踩挂单在穿越一tick后成交() -> None:
    bars = _bars(dip_at=60, dip_bp=40.0)
    plan = _plan(bars)
    t = 57
    assert plan.entry_bar[t] == 60
    expected_limit = np.floor(bars["close"].iloc[t] * (1 - 30 / 1e4) / 0.01 + 1e-9) * 0.01
    assert abs(plan.p_fill[t] - expected_limit) < 1e-12
    assert plan.sl[t] == 0.01
    assert abs(plan.down[t] - np.floor(expected_limit * 0.99 / 0.01 + 1e-9) * 0.01) < 1e-12


def test_没有穿越一tick时不成交() -> None:
    bars = _bars(dip_at=60, dip_bp=0.0)
    t = 57
    limit = np.floor(bars["close"].iloc[t] * (1 - 30 / 1e4) / 0.01 + 1e-9) * 0.01
    bars.loc[60, "low"] = limit - 0.005  # 只到挂单价上方 0.5 tick，未穿越 1 tick
    plan = _plan(bars)
    assert plan.entry_bar[t] == -1


def test_挂单价只用信号时刻收盘价() -> None:
    bars = _bars(dip_at=60, dip_bp=40.0)
    t = 57
    price_before = _plan(bars).p_fill[t]
    mutated = bars.copy()
    mutated.loc[t + 1 :, ["open", "high", "low", "close", "p_1s"]] *= 1.2
    # 强制在 t+1 成交（价格远低于挂单价），成交价仍必须等于用 close[t] 算出的挂单价
    mutated.loc[t + 1, "low"] = price_before * 0.9
    assert abs(_plan(mutated).p_fill[t] - price_before) < 1e-12


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=100.0,
        mode="fixed_fraction",
        position_frac=0.18,
        leverage=5,
        max_positions=5,
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


def test_标签与回测的挂单成交价一致() -> None:
    """红线 4：`exit_sim` 与 `precompute_trades` 的成交根、成交价、止损一致。"""
    bars = _bars(dip_at=123, dip_bp=40.0)
    cfg = BacktestConfig(
        threshold=0.5,
        entry_mode="limit_pullback",
        entry_limit_offset_bp=30.0,
        entry_limit_expiry_bars=5,
    )
    signals = pd.DataFrame(
        {
            "symbol": ["A"],
            "open_time": [int(bars["open_time"].iloc[120])],
            "p": [1.0],
            "m": [0.0],
            "is_sample": [1],
            "theta": [0.5],
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
        backtest_cfg=cfg,
        exit_cfg=ExitConfig(type="trail", H_max=40),
        sizing_cfg=_sizing(),
        k_sl=2.5,
        sl_min=0.01,
        sl_max=0.025,
        H_label=60,
    )
    assert not skipped
    assert len(trades) == 1
    labels, kept = exit_sim(
        bars,
        np.array([120], dtype="int64"),
        k_sl=2.5,
        sl_min=0.01,
        sl_max=0.025,
        H_label=60,
        entry_latency_ms=1000,
        y_min_r=0.25,
        exit_cfg=ExitConfig(type="trail", H_max=40),
        fees_taker=0.0005,
        fees_maker=0.0002,
        half_spread_min=0.0001,
        slip_min=0.0001,
        Y=1.0,
        notional_ref=90.0,
        max_chase=0.003,
        tick_size=0.01,
        entry_mode="limit_pullback",
        entry_limit_offset_bp=30.0,
        entry_limit_expiry_bars=5,
    )
    assert kept.tolist() == [120]
    trade = trades[0]
    label = labels.iloc[0]
    assert trade.entry_open_time == int(bars["open_time"].iloc[123])
    expected = np.floor(bars["close"].iloc[120] * 0.997 / 0.01 + 1e-9) * 0.01
    assert abs(trade.p_fill - expected) < 1e-12
    assert abs(trade.sl - float(label["sl"])) < 1e-12
