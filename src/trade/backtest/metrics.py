"""回测指标（06 §6）。

所有指标按 全部币/训练币/留出币/盲测币 分组时，用 group 过滤 trades。
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.engine import ExecutedTrade, PortfolioResult


def compute_metrics(
    result: PortfolioResult,
    initial_equity: float,
    *,
    group: str | None = None,
) -> dict[str, float]:
    """计算核心指标（06 §6）。

    Args:
        result: 组合模拟结果。
        initial_equity: 初始权益。
        group: 只统计该币种组（train/holdout/blind）；None 表示全部。

    Returns:
        指标字典（键为指标名，值为 float；无交易时为 NaN/0）。
    """
    trades = [t for t in result.trades if group is None or t.group == group]
    final_equity = _final_equity(trades, initial_equity)

    gross_pnl = sum(t.gross_pnl for t in trades)
    fees = sum(t.entry_fee + t.exit_fee for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in losses)

    exit_counter = Counter(t.exit_type for t in trades)
    exit_dist = {name: exit_counter.get(name, 0) for name in _EXIT_NAMES}
    stop_updates = sum(t.stop_updates for t in trades)
    mfe = [t.mfe_r for t in trades]
    mae = [t.mae_r for t in trades]

    return {
        "n_trades": float(n),
        "final_equity": final_equity,
        "net_return_usdt": net_pnl,
        "net_return_pct": net_pnl / initial_equity if initial_equity else 0.0,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "cost_ratio": fees / gross_pnl if gross_pnl else 0.0,
        "win_rate": float(len(wins)) / n if n else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else math.inf,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "avg_pnl_per_trade": net_pnl / n if n else 0.0,
        "avg_holding_minutes": float(np.mean([t.bars_held for t in trades])) if n else 0.0,
        "avg_stop_updates": float(stop_updates) / n if n else 0.0,
        "avg_mfe_r": float(np.mean(mfe)) if mfe else 0.0,
        "avg_mae_r": float(np.mean(mae)) if mae else 0.0,
        "giveback_ratio": _giveback_ratio(trades),
        "max_drawdown": _max_drawdown(_equity_series(trades, initial_equity)),
        **{f"exit_{k}": float(v) for k, v in exit_dist.items()},
    }


_EXIT_NAMES = (
    "initial_stop",
    "breakeven",
    "trail",
    "tp",
    "stall",
    "timeout",
    "eod",
)


def _final_equity(trades: list[ExecutedTrade], initial_equity: float) -> float:
    return initial_equity + sum(t.net_pnl for t in trades)


def _equity_series(trades: list[ExecutedTrade], initial_equity: float) -> list[float]:
    equity = initial_equity
    series = [equity]
    for t in sorted(trades, key=lambda tr: tr.exit_open_time):
        equity += t.net_pnl
        series.append(equity)
    return series


def _max_drawdown(series: list[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return max_dd


def _giveback_ratio(trades: list[ExecutedTrade]) -> float:
    """追踪出场收益 / MFE 的回吐比例（06 §6 出场质量）。"""
    trail_trades = [t for t in trades if t.exit_type == "trail" and t.mfe_r > 1e-9]
    if not trail_trades:
        return 0.0
    ratios = []
    for t in trail_trades:
        realized_r = t.gross_pnl / (t.notional * t.sl) if t.notional else 0.0
        ratios.append(max(0.0, 1.0 - realized_r / t.mfe_r))
    return float(np.mean(ratios))


def daily_returns(equity_curve: pd.DataFrame, initial_equity: float) -> pd.Series:
    """从权益曲线计算日收益（UTC 日），用于夏普/索提诺（06 §6）。"""
    if equity_curve.empty or len(equity_curve) < 2:
        return pd.Series(dtype="float64")
    frame = equity_curve.copy()
    frame["day"] = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True).dt.date
    daily = frame.groupby("day")["equity"].last()
    if daily.iloc[0] != initial_equity:
        daily = pd.concat([pd.Series({None: initial_equity}), daily])
    rets = daily.pct_change().dropna()
    return rets


def sharpe(returns: pd.Series) -> float:
    """日收益年化夏普（×√365，06 §6）。"""
    if len(returns) < 2 or returns.std() <= 0:
        return 0.0
    return float(returns.mean() / returns.std() * math.sqrt(365))


def sortino(returns: pd.Series) -> float:
    """日收益年化索提诺（下行标准差，06 §6）。"""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if downside.empty or downside.std() <= 0:
        return 0.0
    return float(returns.mean() / downside.std() * math.sqrt(365))
