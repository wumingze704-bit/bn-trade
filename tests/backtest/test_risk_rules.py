"""回测风控 R5~R8 与共用规则测试（06 §3.1 条件 4、§3.4；07 §7.1）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from tests.backtest.test_engine import _backtest, _fees, _sizing, _trade
from trade.backtest import engine as engine_module
from trade.backtest.engine import simulate_portfolio
from trade.common import risk_rules
from trade.live import risk as live_risk

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _bars(symbol: str, n: int = 60, *, close: np.ndarray | None = None) -> pd.DataFrame:
    """构造风控强平用 bars（含 p_1s；06 §3.4）。"""
    open_time = np.arange(n) * _MIN + _T0
    prices = np.full(n, 100.0) if close is None else np.asarray(close, dtype="float64")
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": prices,
            "high": prices * 1.001,
            "low": prices * 0.999,
            "close": prices,
            "quote_volume": np.full(n, 1e6),
            "p_1s": prices,
            "p_1s_filled": np.zeros(n, dtype="int64"),
        }
    )


def test_回测与实盘调用同一风控函数():
    """风控判定只有一份实现（AGENTS.md §6 红线 4）。"""
    assert engine_module.risk_step is risk_rules.risk_step
    assert engine_module.register_exit is risk_rules.register_exit
    assert live_risk.risk_rules is risk_rules
    # 行为一致：当日亏 9%（阈值 8%）在两边都判为触发
    assert risk_rules.daily_loss_pnl(-9.0, 100.0, 0.08)
    assert live_risk.check_daily_halt(-9.0, 100.0, 0.08)
    assert not live_risk.check_daily_loss(-9.0, 100.0, 0.05)
    # 回撤 21%（阈值 20%）
    assert risk_rules.drawdown_hit(79.0, 100.0, 0.20)
    assert live_risk.check_drawdown(79.0, 100.0, 0.20)


def test_当日亏损达熔断阈值时全平持仓():
    """R6：当日盯市亏损达 daily_loss_halt 后下一分钟平掉全部持仓（06 §3.4）。"""
    # A 亏损 9%（毛收益 −9%），B 仍持仓；A 的离场把当日权益打到 −9% → R6 强平 B
    bars_a = _bars("A")
    bars_b = _bars("B")
    trades = [
        _trade(
            symbol="A",
            entry=_T0,
            exit=_T0 + 2 * _MIN,
            exit_code=0,
            exit_price=91.0,
            gross_return=-0.09,
        ),
        _trade(symbol="B", entry=_T0, exit=_T0 + 30 * _MIN, exit_code=3, gross_return=0.01),
    ]
    result = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=_backtest(),
        fees=_fees(),
        bars_by_symbol={"A": bars_a, "B": bars_b},
    )
    assert [event.rule for event in result.risk_events] == ["R6"]
    b_trade = next(item for item in result.trades if item.symbol == "B")
    assert b_trade.exit_type == "risk_halt"
    assert b_trade.exit_open_time == _T0 + 3 * _MIN  # 触发后下一分钟平仓
    assert result.max_drawdown_halt is False


def test_峰值回撤达阈值时全平持仓():
    """R8：盯市权益从峰值回撤 ≥ max_drawdown_halt 时强平（06 §3.4）。

    每日跌 5% 累计回撤 20%，单日亏损低于 R6 的 8%，只有 R8 能触发。
    """
    day = 1440
    n = 5 * day
    close = np.empty(n, dtype="float64")
    for d in range(5):
        close[d * day : (d + 1) * day] = 100.0 * (0.945**d)
    bars = _bars("A", n=n, close=close)
    trades = [_trade(symbol="A", entry=_T0, exit=_T0 + (4 * day + 100) * _MIN, exit_code=3)]
    result = simulate_portfolio(
        trades,
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=_backtest(),
        fees=_fees(),
        bars_by_symbol={"A": bars},
    )
    rules = [event.rule for event in result.risk_events]
    assert "R8" in rules
    assert "R6" not in rules
    assert result.trades[0].exit_type == "risk_halt"
    assert result.max_drawdown_halt is True


def test_连续止损达上限后暂停开仓():
    """R7：连续 6 次止损出场后暂停开仓 consecutive_loss_pause_min 分钟（07 §7.1）。"""
    losses = [
        _trade(
            symbol=f"S{i}",
            entry=_T0 + i * 20 * _MIN,
            exit=_T0 + (i * 20 + 1) * _MIN,
            exit_code=0,
            gross_return=-0.002,
        )
        for i in range(6)
    ]
    # 第 6 笔在 t+101 分钟离场，暂停到 t+221 分钟；第 7 个候选落在暂停窗口内
    candidate = _trade(symbol="S7", entry=_T0 + 110 * _MIN, exit=_T0 + 130 * _MIN)
    result = simulate_portfolio(
        [*losses, candidate],
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=_backtest(),
        fees=_fees(),
    )
    assert [event.rule for event in result.risk_events] == ["R7"]
    assert any(item.reason == "consecutive_loss_pause" for item in result.skipped)
    assert len(result.trades) == 6


def test_停机后严格模式不再开仓_复位模式按当时权益继续():
    """R6 停机处理：strict 停止交易至运行结束；reset 停 1 天后继续（06 §2 risk.halt_mode）。"""
    losses = [
        _trade(
            symbol="A",
            entry=_T0,
            exit=_T0 + 2 * _MIN,
            exit_code=0,
            exit_price=91.0,
            gross_return=-0.09,
        )
    ]
    later = _trade(
        symbol="B", entry=_T0 + 2 * 24 * 60 * _MIN, exit=_T0 + 2 * 24 * 60 * _MIN + 5 * _MIN
    )
    bars = {"A": _bars("A"), "B": _bars("B", n=3 * 24 * 60)}
    strict = simulate_portfolio(
        [*losses, later],
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=_backtest(),
        fees=_fees(),
        bars_by_symbol=bars,
    )
    assert any(item.reason == "halted" for item in strict.skipped)
    assert all(item.symbol != "B" for item in strict.trades)

    reset_cfg = _backtest()
    reset_cfg.risk.halt_mode = "reset"
    reset_cfg.risk.halt_reset_days = 1
    reset = simulate_portfolio(
        [*losses, later],
        [],
        sizing_cfg=_sizing(base="initial"),
        backtest_cfg=reset_cfg,
        fees=_fees(),
        bars_by_symbol=bars,
    )
    assert any(item.symbol == "B" for item in reset.trades)
