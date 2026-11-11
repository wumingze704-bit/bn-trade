"""回测引擎组合约束测试（06 §3.1、§3.4、§5.3）。"""

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
    precompute_trades_all,
    run_backtest,
    simulate_portfolio,
)
from trade.common.exits import ExitConfig
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


def _backtest(**kw) -> BacktestConfig:
    base = dict(threshold=0.5)
    base.update(kw)
    return BacktestConfig(**base)


def _rule(**kw) -> TradeRule:
    base = dict(tick_size=0.01, step_size=0.01, min_notional=5.0)
    base.update(kw)
    return TradeRule(**base)


def _fees() -> FeeSchedule:
    return FeeSchedule(maker=0.0002, taker=0.0005)


def _trade(symbol="A", entry=0, exit=10, p=1.0, m=0.0, exit_code=3, **kw) -> PrecomputedTrade:
    base = dict(
        symbol=symbol,
        group="train",
        signal_open_time=entry - _MIN,
        entry_open_time=entry,
        p=p,
        m=m,
        p_fill=100.0,
        sl=0.008,
        loss_rate=0.01,
        mean_qv_60=1e6,
        exit_open_time=exit,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=exit_code,
        bars_held=(exit - entry) // _MIN + 1,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        gross_return=0.01,
        exit_fee_rate=0.0002,
        tick_size=0.01,
        step_size=0.01,
        min_notional=5.0,
    )
    base.update(kw)
    return PrecomputedTrade(**base)


def test_冷却期内不开仓():
    trades = [
        _trade(symbol="A", entry=_T0, exit=_T0 + 5 * _MIN, exit_code=0),
        _trade(symbol="A", entry=_T0 + 10 * _MIN, exit=_T0 + 20 * _MIN, exit_code=3),
    ]
    result = simulate_portfolio(
        trades, [], sizing_cfg=_sizing(), backtest_cfg=_backtest(), fees=_fees()
    )
    assert any(s.reason == "cooldown" for s in result.skipped)
    assert len(result.trades) == 1


def test_组合持仓上限():
    trades = [_trade(symbol=s, entry=_T0, exit=_T0 + 10 * _MIN) for s in ("A", "B", "C", "D")]
    result = simulate_portfolio(
        trades, [], sizing_cfg=_sizing(max_positions=3), backtest_cfg=_backtest(), fees=_fees()
    )
    assert len(result.trades) == 3
    assert any(s.reason == "max_positions" for s in result.skipped)


def test_候选按p降序m降序():
    trades = [
        _trade(symbol="A", entry=_T0, exit=_T0 + 10 * _MIN, p=0.8, m=1.0),
        _trade(symbol="B", entry=_T0, exit=_T0 + 10 * _MIN, p=0.9, m=0.0),
    ]
    result = simulate_portfolio(
        trades, [], sizing_cfg=_sizing(max_positions=1), backtest_cfg=_backtest(), fees=_fees()
    )
    assert [t.symbol for t in result.trades] == ["B"]


def test_权益下限停止开仓():
    trades = [_trade(symbol="A", entry=_T0, exit=_T0 + 10 * _MIN, min_notional=1000.0)]
    result = simulate_portfolio(
        trades, [], sizing_cfg=_sizing(), backtest_cfg=_backtest(), fees=_fees()
    )
    assert result.equity_floor_hit is True
    assert len(result.trades) == 0


def _bars_with_chase() -> pd.DataFrame:
    n = 60
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    p_1s = np.full(n, 100.0)
    p_1s[11] = 101.0  # 信号 t=10 的入场价跳空上涨 → 追价保护
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": np.full(n, 100.0),
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": p_1s,
            "hi_post_1s": high,
            "lo_post_1s": low,
        }
    )


def test_追价保护被放弃():
    bars = _bars_with_chase()
    signals = pd.DataFrame(
        {"symbol": ["A"], "open_time": [_T0 + 10 * _MIN], "p": [1.0], "m": [0.0], "is_sample": [1]}
    )
    trades, skipped = precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=_rule(),
        scen=scenario("base"),
        fees=_fees(),
        backtest_cfg=_backtest(),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
    )
    assert len(trades) == 0
    assert any(s.reason == "chase" for s in skipped)


def test_盯市权益曲线捕获持仓浮亏():
    """T0.18：权益曲线按 c_t 盯市，V 形价格路径下的浮亏计入回撤（实现盈亏≈0）。"""
    n = 30
    open_time = np.arange(n) * _MIN + _T0
    close = np.concatenate([np.linspace(100.0, 80.0, 15), np.linspace(80.0, 100.0, 15)])
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
    trade = _trade(symbol="A", entry=_T0 + _MIN, exit=_T0 + 28 * _MIN, gross_return=0.0)
    result = simulate_portfolio(
        [trade],
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=_backtest(),
        fees=_fees(),
        bars_by_symbol={"A": bars},
    )
    assert len(result.equity_curve) > 5  # 逐分钟盯市，而非只在平仓时记录
    assert result.equity_curve["drawdown"].max() > 0.1  # 浮亏回撤远大于实现回撤


def test_run_backtest端到端():
    rng = np.random.default_rng(3)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    signals_frames = []
    for sym in ("A", "B"):
        n = 200
        open_time = np.arange(n) * _MIN + _T0
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
        open_ = np.concatenate([[100.0], close[:-1]])
        high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0005, n))
        low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0005, n))
        bars_by_symbol[sym] = pd.DataFrame(
            {
                "open_time": open_time,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "quote_volume": np.full(n, 1e6),
                "is_filled": np.zeros(n, dtype="int64"),
                "p_1s": close,
                "hi_post_1s": high,
                "lo_post_1s": low,
            }
        )
        signals_frames.append(
            pd.DataFrame(
                {
                    "symbol": [sym] * n,
                    "open_time": open_time,
                    "p": np.ones(n),
                    "m": np.zeros(n),
                    "is_sample": np.ones(n, dtype="int64"),
                }
            )
        )
    signals = pd.concat(signals_frames, ignore_index=True)
    rules = {"A": _rule(), "B": _rule()}
    result, all_trades = run_backtest(
        bars_by_symbol,
        signals,
        rules,
        {"A": "train", "B": "train"},
        scen_name="base",
        fees=_fees(),
        backtest_cfg=_backtest(),
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        initial_equity=10000.0,
    )
    assert len(all_trades) > 0
    assert result.final_equity > 0
    assert not result.equity_curve.empty


def test_出场分钟缺逐笔价时按收盘价出场且净盈亏有限():
    """06 §3.3：出场分钟缺 aggTrades（p_1s 为空）时按该根 K 线收盘价（扣成本）出场。"""
    n = 140
    open_time = np.arange(n) * _MIN + _T0
    close = 100.0 + 0.01 * np.arange(n)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 0.01
    low = np.minimum(open_, close) - 0.01
    p_1s = close.copy()
    p_1s[121] = np.nan  # 超时出场那根 K 线没有逐笔成交
    bars = pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": p_1s,
            "hi_post_1s": high,
            "lo_post_1s": low,
        }
    )
    signals = pd.DataFrame(
        {"symbol": ["A"], "open_time": [open_time[60]], "p": [1.0], "m": [0.0], "is_sample": [1]}
    )
    trades, _ = precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=_rule(),
        scen=scenario("base"),
        fees=_fees(),
        backtest_cfg=_backtest(),
        exit_cfg=ExitConfig(type="fixed", rr=2.0),
        sizing_cfg=_sizing(),
        k_sl=2.5,
        sl_min=0.01,
        sl_max=0.025,
        H_label=60,
    )
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_type == "timeout"
    assert trade.exit_open_time == int(open_time[121])
    assert np.isfinite(trade.exit_price) and trade.exit_price > 0
    assert np.isfinite(trade.gross_return)


def test_预计算一次与逐阈值回测结果一致():
    """阈值网格优化：最低 θ 预计算一次 + 按 θ 过滤重跑组合层，与逐 θ run_backtest 逐位一致。"""
    n = 200
    open_time = np.arange(n) * _MIN + _T0
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0005, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0005, n))
    bars = pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": close,
            "hi_post_1s": high,
            "lo_post_1s": low,
        }
    )
    signals = pd.DataFrame(
        {
            "symbol": ["A"] * n,
            "open_time": open_time,
            "p": rng.uniform(0.3, 0.8, n),
            "m": np.zeros(n),
            "is_sample": np.ones(n, dtype="int64"),
        }
    )
    rules = {"A": _rule()}
    groups = {"A": "train"}

    def run_old(theta: float):
        cfg = _backtest(threshold=theta)
        result, _ = run_backtest(
            {"A": bars},
            signals,
            rules,
            groups,
            scen_name="base",
            fees=_fees(),
            backtest_cfg=cfg,
            exit_cfg=ExitConfig(type="trail"),
            sizing_cfg=_sizing(),
            k_sl=1.0,
            sl_min=0.005,
            sl_max=0.015,
            H_label=60,
            initial_equity=10000.0,
        )
        return result

    pre_cfg = _backtest(threshold=0.30)
    all_trades, all_skipped = precompute_trades_all(
        {"A": bars},
        signals,
        rules,
        groups,
        scen=scenario("base"),
        fees=_fees(),
        backtest_cfg=pre_cfg,
        exit_cfg=ExitConfig(type="trail"),
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
    )

    for theta in (0.40, 0.55, 0.70):
        trades = [t for t in all_trades if float(t.p) >= theta]
        new_result = simulate_portfolio(
            trades,
            list(all_skipped),
            sizing_cfg=_sizing(),
            backtest_cfg=_backtest(threshold=theta),
            fees=_fees(),
            initial_equity=10000.0,
            bars_by_symbol={"A": bars},
        )
        old_result = run_old(theta)
        assert new_result.final_equity == pytest.approx(old_result.final_equity, abs=1e-6)
        assert len(new_result.trades) == len(old_result.trades)
