"""资金费计入测试（T0.8，06 §3.3、§6）。

资金费：持仓跨越结算时刻时 `PnL -= funding_rate × 名义价值`（多头费率为正时支付）。
bars 的 `funding_rate`/`funding_time` 已按「最近已结算一期」向后对齐（03 §2.3）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    PrecomputedTrade,
    TradeRule,
    precompute_trades,
    simulate_portfolio,
)
from trade.common.exits import ExitConfig
from trade.common.funding import funding_rate_between, funding_settlements
from trade.common.sizing import SizingConfig

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _sizing(**kw) -> SizingConfig:
    base = dict(
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
    base.update(kw)
    return SizingConfig(**base)


def _fees() -> FeeSchedule:
    return FeeSchedule(maker=0.0002, taker=0.0005)


def test_结算事件去重排序() -> None:
    """多根 K 线共享同一 funding_time 时去重，只保留结算时刻与当期费率。"""
    bars = pd.DataFrame(
        {
            "funding_time": [_T0] * 5 + [_T0 + 8 * 3_600_000] * 5,
            "funding_rate": [0.0001] * 5 + [0.0002] * 5,
        }
    )
    times, rates = funding_settlements(bars)
    assert times.tolist() == [_T0, _T0 + 8 * 3_600_000]
    assert rates.tolist() == [0.0001, 0.0002]


def test_无资金费列时计零() -> None:
    bars = pd.DataFrame({"close": [100.0]})
    times, rates = funding_settlements(bars)
    assert times.size == 0 and rates.size == 0
    assert funding_rate_between(times, rates, _T0, _T0 + 10 * _MIN) == 0.0


def test_跨结算与边界() -> None:
    """结算时刻在 (entry, exit] 区间内才计入；恰在入场时点不计、恰在出场时点计入。"""
    ft = np.array([_T0 + 2 * _MIN, _T0 + 4 * _MIN], dtype="int64")
    fr = np.array([0.0001, 0.0002], dtype="float64")
    # entry 在第一次结算之前、exit 在两次结算之后 → 两期都计
    assert funding_rate_between(ft, fr, _T0, _T0 + 6 * _MIN) == pytest.approx(0.0003)
    # exit 恰在第一次结算时点 → 只计第一期
    assert funding_rate_between(ft, fr, _T0, _T0 + 2 * _MIN) == pytest.approx(0.0001)
    # entry 恰在第一次结算时点 → 该期不计（严格大于 entry）
    assert funding_rate_between(ft, fr, _T0 + 2 * _MIN, _T0 + 6 * _MIN) == pytest.approx(0.0002)
    # 持仓未跨结算 → 0
    assert funding_rate_between(ft, fr, _T0 + 5 * _MIN, _T0 + 6 * _MIN) == 0.0


def test_组合模拟扣资金费() -> None:
    """simulate_portfolio 中 funding_fee = notional × funding_rate_sum，并从净盈亏扣除。"""
    tr = PrecomputedTrade(
        symbol="A",
        group="train",
        signal_open_time=_T0 - _MIN,
        entry_open_time=_T0,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.008,
        loss_rate=0.01,
        mean_qv_60=1e6,
        exit_open_time=_T0 + 10 * _MIN,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        bars_held=11,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        gross_return=0.01,
        exit_fee_rate=0.0002,
        funding_rate_sum=0.0003,
        tick_size=0.01,
        step_size=0.01,
        min_notional=5.0,
    )
    result = simulate_portfolio(
        [tr], [], sizing_cfg=_sizing(), backtest_cfg=BacktestConfig(threshold=0.5), fees=_fees()
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.funding_fee == pytest.approx(trade.notional * 0.0003)
    expected_net = trade.notional * (0.01 - 0.0005 - 0.0002) - trade.funding_fee
    assert trade.net_pnl == pytest.approx(expected_net)


def test_precompute_trades记录资金费率() -> None:
    """precompute_trades 从 bars 提取结算事件，跨结算的持仓 funding_rate_sum 非零。"""
    n = 200
    t_signal = 100  # 过了 60 根 lookback，mean_qv_60/rv15 有限
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 100.0)
    bars = pd.DataFrame(
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
    settle_ts = int(open_time[t_signal + 5])  # 在入场（t_signal+1）之后的持仓期内结算
    # 第一次结算在入场前（不计），第二次在持仓中（计）
    bars["funding_time"] = [_T0 - 8 * 3_600_000] * 5 + [settle_ts] * (n - 5)
    bars["funding_rate"] = [0.0001] * 5 + [0.0002] * (n - 5)
    signals = pd.DataFrame(
        {"open_time": [open_time[t_signal]], "p": [1.0], "m": [0.0], "is_sample": [1]}
    )
    trades, _ = precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=TradeRule(tick_size=0.01, step_size=0.01, min_notional=5.0),
        scen=scenario("base"),
        fees=_fees(),
        backtest_cfg=BacktestConfig(threshold=0.5),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
    )
    assert len(trades) == 1
    assert trades[0].funding_rate_sum == pytest.approx(0.0002)
