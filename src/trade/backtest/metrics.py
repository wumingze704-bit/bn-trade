"""回测指标（06 §6）。

所有指标按 全部币/训练组/留出组/新进入组 分组时，用 group 过滤 trades（03 §4.5）。
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
        group: 只统计该币种组（train/holdout/new_entry）；None 表示全部。

    Returns:
        指标字典（键为指标名，值为 float；无交易时为 NaN/0）。
    """
    if group == "new_entry":
        trades = [t for t in result.trades if t.new_entry]
    else:
        trades = [t for t in result.trades if group is None or t.group == group]
    final_equity = _final_equity(trades, initial_equity)

    gross_pnl = sum(t.gross_pnl for t in trades)
    fees = sum(t.entry_fee + t.exit_fee for t in trades)
    funding_fee = sum(t.funding_fee for t in trades)
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
        "funding_fee": funding_fee,
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
        "max_drawdown": _portfolio_max_drawdown(result, trades, initial_equity, group),
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


def _portfolio_max_drawdown(
    result: PortfolioResult,
    trades: list[ExecutedTrade],
    initial_equity: float,
    group: str | None,
) -> float:
    """最大回撤：组合层（group=None）用盯市权益曲线，分组层用已实现序列（06 §5.1、§6）。"""
    if group is None and result.equity_curve is not None and not result.equity_curve.empty:
        return float(result.equity_curve["drawdown"].max())
    return _max_drawdown(_equity_series(trades, initial_equity))


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


def daily_block_bootstrap_ci(
    trades: list[ExecutedTrade],
    *,
    n_iters: int = 10000,
    seed: int = 42,
    alpha: float = 0.05,
) -> dict[str, float]:
    """单笔净期望（bp）的 UTC 日分块 bootstrap 95% 置信区间（08 §3 G2、06 §6）。

    整日重抽样以保留日内交易相关性：每笔交易按 UTC 日（entry_open_time）分块，
    单笔期望 bp = 净盈亏 / 名义价值 × 10000；每次 bootstrap 有放回重抽整日，
    按重抽后（Σ 各笔 bp）/（Σ 笔数）得到单笔期望，取 alpha/2 与 1−alpha/2 分位。

    Args:
        trades: 已按 group 过滤的交易列表。
        n_iters: bootstrap 重复次数（08 G2 要求 10000）。
        seed: 随机种子（来自配置）。
        alpha: 置信水平（0.05 → 95%）。

    Returns:
        {lower_bp, upper_bp, mean_bp, n_iters, seed, n_days, n_trades, passed}；
        lower_bp > 0 即 passed=True（08 §3 G2 统计显著性）。
    """
    valid = [t for t in trades if t.notional > 0.0]
    if not valid:
        return {
            "lower_bp": 0.0,
            "upper_bp": 0.0,
            "mean_bp": 0.0,
            "n_iters": float(n_iters),
            "seed": float(seed),
            "n_days": 0.0,
            "n_trades": 0.0,
            "passed": False,
        }

    by_day: dict[str, list[float]] = {}
    for t in valid:
        day = _utc_day(t.entry_open_time)
        by_day.setdefault(day, []).append(t.net_pnl / t.notional * 10000.0)

    day_bp_totals = np.asarray([sum(v) for v in by_day.values()], dtype="float64")
    day_counts = np.asarray([len(v) for v in by_day.values()], dtype="float64")
    n_days = len(day_bp_totals)

    mean_bp = float(day_bp_totals.sum() / day_counts.sum()) if day_counts.sum() else 0.0

    rng = np.random.default_rng(seed)
    means = np.empty(n_iters, dtype="float64")
    for i in range(n_iters):
        idx = rng.integers(0, n_days, size=n_days)
        total_bp = day_bp_totals[idx].sum()
        total_n = day_counts[idx].sum()
        means[i] = total_bp / total_n if total_n > 0 else 0.0

    lower = float(np.quantile(means, alpha / 2.0))
    upper = float(np.quantile(means, 1.0 - alpha / 2.0))
    return {
        "lower_bp": lower,
        "upper_bp": upper,
        "mean_bp": mean_bp,
        "n_iters": float(n_iters),
        "seed": float(seed),
        "n_days": float(n_days),
        "n_trades": float(len(valid)),
        "passed": lower > 0.0,
    }


def _utc_day(ts_ms: int) -> str:
    """UTC 日（08 §3 G2 分块 bootstrap）。"""
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


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
