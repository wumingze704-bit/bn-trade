"""回测指标测试（06 §6）。"""

from __future__ import annotations

import pytest

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.metrics import compute_metrics, daily_block_bootstrap_ci


def _trade(symbol="A", exit_type="tp", net_pnl=1.0, gross=1.5, exit_code=3) -> ExecutedTrade:
    return ExecutedTrade(
        symbol=symbol,
        group="train",
        signal_open_time=0,
        entry_open_time=60_000,
        exit_open_time=120_000,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.008,
        exit_price=101.0,
        exit_type=exit_type,
        exit_type_code=exit_code,
        notional=10.0,
        qty=0.1,
        margin=2.0,
        bars_held=2,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.005,
        exit_fee=0.002,
        gross_pnl=gross,
        net_pnl=net_pnl,
        capped_by=None,
    )


def test_指标基本计算():
    trades = [_trade("A", "tp", 1.0, 1.5, 3), _trade("B", "initial_stop", -0.5, -0.3, 0)]
    result = PortfolioResult(trades=trades)
    m = compute_metrics(result, initial_equity=10.0)
    assert m["n_trades"] == 2
    assert m["net_return_usdt"] == pytest.approx(0.5)
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(1.0 / 0.5)
    assert m["exit_initial_stop"] == 1
    assert m["exit_tp"] == 1


def test_空交易指标为0():
    m = compute_metrics(PortfolioResult(), initial_equity=10.0)
    assert m["n_trades"] == 0
    assert m["net_return_usdt"] == 0.0


def _day_trade(day: int, bp: float) -> ExecutedTrade:
    """构造某 UTC 日、单笔净期望为 bp 的交易（bp = net_pnl/notional × 10000）。"""
    notional = 100.0
    net_pnl = bp / 10000.0 * notional
    return ExecutedTrade(
        symbol="A",
        group="train",
        signal_open_time=day * 86_400_000,
        entry_open_time=day * 86_400_000 + 60_000,
        exit_open_time=day * 86_400_000 + 120_000,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.008,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        notional=notional,
        qty=1.0,
        margin=20.0,
        bars_held=2,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        capped_by=None,
    )


def test_daily_block_bootstrap_ci固定种子可复现():
    trades = [_day_trade(d, 1.0) for d in range(10)]
    a = daily_block_bootstrap_ci(trades, n_iters=1000, seed=7)
    b = daily_block_bootstrap_ci(trades, n_iters=1000, seed=7)
    assert a["lower_bp"] == b["lower_bp"]
    assert a["upper_bp"] == b["upper_bp"]


def test_daily_block_bootstrap_ci正期望且样本足够判通过():
    trades = [_day_trade(d, 1.0) for d in range(20)]
    out = daily_block_bootstrap_ci(trades, n_iters=1000, seed=7)
    assert out["mean_bp"] > 0
    assert out["lower_bp"] > 0
    assert out["passed"] is True


def test_daily_block_bootstrap_ci均值正但天数少判不通过():
    trades = [_day_trade(0, 1.0), _day_trade(1, -0.5)]
    out = daily_block_bootstrap_ci(trades, n_iters=1000, seed=7)
    assert out["mean_bp"] > 0
    assert out["lower_bp"] <= 0
    assert out["passed"] is False


def test_daily_block_bootstrap_ci空交易():
    out = daily_block_bootstrap_ci([], n_iters=10, seed=1)
    assert out["passed"] is False
    assert out["n_trades"] == 0.0
