"""R0 缺陷修复回归测试：名义价值取整、冷却条件、持仓释放、盯市起点与月度补零。

规格：docs/06-回测引擎.md §3.1、§3.4、§5.1、§5.3；docs/08 §3 G2-U
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import (
    BacktestConfig,
    CloseSeries,
    ExecutedTrade,
    PrecomputedTrade,
    _mark_to_market_curve,
    _unrealized_before,
    reference_trade_net_pnl,
    simulate_portfolio,
)
from trade.backtest.metrics import daily_returns, monthly_returns, sharpe, sharpe_daily
from trade.common.sizing import SizingConfig

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _sizing(**kw) -> SizingConfig:
    base = dict(
        initial_equity=100.0,
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


def _precomputed(*, symbol: str = "A", entry: int, exit_: int, **kw) -> PrecomputedTrade:
    base = dict(
        symbol=symbol,
        group="train",
        signal_open_time=entry - _MIN,
        entry_open_time=entry,
        p=1.0,
        m=0.0,
        p_fill=0.1234567,
        sl=0.01,
        loss_rate=0.012,
        mean_qv_60=1e7,
        exit_open_time=exit_,
        exit_price=0.1234567 * 1.01,
        exit_type="tp",
        exit_type_code=3,
        bars_held=(exit_ - entry) // _MIN + 1,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        gross_return=0.01,
        exit_fee_rate=0.0002,
        tick_size=0.0001,
        step_size=1.0,
        min_notional=5.0,
    )
    base.update(kw)
    return PrecomputedTrade(**base)


def _bars(n: int = 30) -> dict[str, pd.DataFrame]:
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 0.1234567)
    return {
        "A": pd.DataFrame(
            {
                "open_time": open_time,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "p_1s": close,
                "quote_volume": np.full(n, 1e7),
            }
        )
    }


def _executed(*, entry: int = _T0 + _MIN, exit_: int = _T0 + 4 * _MIN) -> ExecutedTrade:
    return ExecutedTrade(
        symbol="A",
        group="train",
        signal_open_time=entry - _MIN,
        entry_open_time=entry,
        exit_open_time=exit_,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.01,
        exit_price=100.0,
        exit_type="tp",
        exit_type_code=3,
        notional=100.0,
        qty=1.0,
        margin=20.0,
        bars_held=3,
        stop_updates=0,
        mfe_r=0.0,
        mae_r=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        gross_pnl=0.0,
        net_pnl=0.0,
        capped_by=None,
    )


def test_名义价值按取整后数量计算() -> None:
    """R0-4：组合层毛利、手续费与保证金都用 qty × P_fill，不用取整前 notional。"""
    trade = _precomputed(entry=_T0 + _MIN, exit_=_T0 + 5 * _MIN)
    result = simulate_portfolio(
        [trade],
        [],
        sizing_cfg=_sizing(),
        backtest_cfg=BacktestConfig(threshold=0.5),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    assert len(result.trades) == 1
    executed = result.trades[0]
    expected_notional = executed.qty * executed.p_fill
    assert abs(executed.notional - expected_notional) < 1e-12
    assert abs(executed.margin - expected_notional / 5.0) < 1e-12
    assert abs(executed.gross_pnl - expected_notional * trade.gross_return) < 1e-12


def test_reference单笔净盈亏按取整后名义价值() -> None:
    """R0-4：S1 用的 reference 单笔净盈亏同样按 qty × P_fill。"""
    trade = _precomputed(entry=_T0 + _MIN, exit_=_T0 + 5 * _MIN)
    net = reference_trade_net_pnl(trade, sizing_cfg=_sizing(), fees=_fees())
    qty_upper = 100.0 * 0.2 * 5 / trade.p_fill
    assert abs(net) <= qty_upper * trade.p_fill * 0.02 + 1e-9


def test_冷却默认只在亏损止损后触发() -> None:
    """R0-16：cooldown_on=loss_stop 时保本/追踪的盈利出场不触发冷却。"""
    trades = [
        _precomputed(symbol="A", entry=_T0, exit_=_T0 + 5 * _MIN, exit_type_code=1),
        _precomputed(symbol="A", entry=_T0 + 10 * _MIN, exit_=_T0 + 20 * _MIN, exit_type_code=3),
    ]
    result = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(),
        backtest_cfg=BacktestConfig(threshold=0.5),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    assert not any(item.reason == "cooldown" for item in result.skipped)
    assert len(result.trades) == 2


def test_any_stop冷却覆盖保本出场() -> None:
    """R0-16：cooldown_on=any_stop 时保本出场也触发冷却。"""
    trades = [
        _precomputed(symbol="A", entry=_T0, exit_=_T0 + 5 * _MIN, exit_type_code=1),
        _precomputed(symbol="A", entry=_T0 + 10 * _MIN, exit_=_T0 + 20 * _MIN, exit_type_code=3),
    ]
    result = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(),
        backtest_cfg=BacktestConfig(threshold=0.5, risk={"cooldown_on": "any_stop"}),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    assert any(item.reason == "cooldown" for item in result.skipped)


def test_离场后下一分钟才释放名额() -> None:
    """R0-13：盘中离场后同一分钟仍占用名额，下一分钟才释放。"""
    trades = [
        _precomputed(symbol="A", entry=_T0, exit_=_T0 + 5 * _MIN),
        _precomputed(symbol="A", entry=_T0 + 5 * _MIN, exit_=_T0 + 15 * _MIN),
        _precomputed(symbol="A", entry=_T0 + 6 * _MIN, exit_=_T0 + 16 * _MIN),
    ]
    result = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(),
        backtest_cfg=BacktestConfig(threshold=0.5),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    reasons = [item.reason for item in result.skipped]
    assert "already_in_position" in reasons
    assert len(result.trades) == 2


def test_排序分数可切换为回归净R() -> None:
    """R2-1：signal_rank_by=m 时按回归净 R 排序，而不是分类概率。"""
    trades = [
        _precomputed(symbol="A", entry=_T0, exit_=_T0 + 5 * _MIN, p=0.9, m=0.0),
        _precomputed(symbol="B", entry=_T0, exit_=_T0 + 5 * _MIN, p=0.5, m=1.0),
    ]
    by_p = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(max_positions=1),
        backtest_cfg=BacktestConfig(threshold=0.5),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    assert [t.symbol for t in by_p.trades] == ["A"]
    by_m = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(max_positions=1),
        backtest_cfg=BacktestConfig(threshold=0.5, signal_rank_by="m"),
        fees=_fees(),
        bars_by_symbol=_bars(),
    )
    assert [t.symbol for t in by_m.trades] == ["B"]


def test_盯市权益曲线不含1970标记行() -> None:
    """R0-5a：权益曲线不再写入 ts_ms = 0 的初始权益标记。"""
    open_time = np.arange(5) * _MIN + _T0
    arrays = {"A": CloseSeries(open_time=open_time, close=np.full(5, 101.0))}
    curve = _mark_to_market_curve([_executed()], arrays, 100.0)
    assert int(curve["ts_ms"].min()) > 0


def test_风控门控只看上一分钟收盘价() -> None:
    """R0-13：`_unrealized_before` 不使用 ts 分钟自身的收盘价。"""
    open_time = np.arange(5) * _MIN + _T0
    close = np.array([100.0, 101.0, 102.0, 130.0, 131.0])
    arrays = {"A": CloseSeries(open_time=open_time, close=close)}
    value = _unrealized_before({"A": _executed()}, arrays, int(open_time[3]))
    assert abs(value - 100.0 * (102.0 / 100.0 - 1.0)) < 1e-12


def test_日收益补齐零收益日() -> None:
    """R0-5b：空仓日按上一日权益补齐，日收益序列包含 0 收益日。"""
    day0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    ts = [day0, day0 + 60_000, day0 + 4 * 86_400_000]
    curve = pd.DataFrame({"ts_ms": ts, "equity": [100.0, 101.0, 101.0], "drawdown": 0.0})
    returns = daily_returns(curve, 100.0)
    assert len(returns) == 5
    assert abs(float(returns.iloc[1])) < 1e-12
    assert float(returns.iloc[0]) > 0.0


def test_月收益按评估月份补零() -> None:
    """R0-5c：空仓月计入月度统计，收益为 0。"""
    jan = int(datetime(2026, 1, 2, tzinfo=UTC).timestamp() * 1000)
    mar = int(datetime(2026, 3, 2, tzinfo=UTC).timestamp() * 1000)
    curve = pd.DataFrame({"ts_ms": [jan, mar], "equity": [101.0, 102.0], "drawdown": [0.0, 0.0]})
    returns = monthly_returns(curve, 100.0, months=["2026-01", "2026-02", "2026-03"])
    assert list(returns.index) == ["2026-01", "2026-02", "2026-03"]
    assert abs(float(returns["2026-02"])) < 1e-12


def test_DSR用日频非年化夏普() -> None:
    """R0-5d：S11 使用非年化日频夏普（与 n_obs 同周期）。"""
    returns = pd.Series([0.01, -0.01, 0.02, -0.02, 0.0])
    assert abs(sharpe_daily(returns) - float(returns.mean() / returns.std())) < 1e-12
    assert abs(sharpe(returns) - sharpe_daily(returns) * np.sqrt(365)) < 1e-9
