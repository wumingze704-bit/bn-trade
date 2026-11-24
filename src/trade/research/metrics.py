"""研究指标：夏普、实现 β、alpha 夏普、回撤、月度、bootstrap 与 Deflated Sharpe（docs/13 §7）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.research.config import BootstrapConfig
from trade.research.engine import RunResult, Trade


def utc_day(ts_ms: int) -> str:
    """UTC 日 `YYYY-MM-DD`。"""
    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


def daily_equity(result: RunResult) -> pd.Series:
    """按 UTC 日取最后权益（自然日补齐）。"""
    series = pd.Series(result.equity, index=pd.to_datetime(result.equity_ts, unit="ms", utc=True))
    daily = series.resample("1D").last().dropna()
    if daily.empty:
        return daily
    return daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D")).ffill()


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（正数，0.2 = 20%）。"""
    if equity.size < 2:
        return 0.0
    peak = equity.cummax()
    return float(((peak - equity) / peak).max())


def monthly_returns(equity: pd.Series) -> pd.Series:
    """月度收益（补零月）。"""
    if equity.size < 2:
        return pd.Series(dtype="float64")
    monthly = equity.resample("1ME").last().dropna()
    if monthly.size < 2:
        return pd.Series(dtype="float64")
    returns = monthly.pct_change().dropna()
    full = pd.date_range(returns.index.min(), returns.index.max(), freq="1ME")
    return returns.reindex(full).fillna(0.0)


def yearly_returns(equity: pd.Series) -> dict[str, float]:
    """每年收益。"""
    if equity.size < 2:
        return {}
    yearly = equity.resample("1YE").last().dropna()
    if yearly.size < 2:
        return {}
    returns = yearly.pct_change().dropna()
    return {str(index.year): float(value) for index, value in returns.items()}


def bootstrap_trade_bp(
    trades: list[Trade], *, iters: int, seed: int, alpha: float = 0.05
) -> dict[str, float]:
    """单笔净期望（bp）的 UTC 日分块 bootstrap 95% 置信区间（docs/13 §7）。"""
    valid = [trade for trade in trades if trade.notional > 0.0]
    if not valid:
        return {"lower_bp": 0.0, "upper_bp": 0.0, "mean_bp": 0.0, "n_days": 0.0, "n_trades": 0.0}
    by_day: dict[str, list[float]] = {}
    for trade in valid:
        by_day.setdefault(utc_day(trade.entry_ts), []).append(
            trade.net_pnl / trade.notional * 10_000.0
        )
    totals = np.asarray([sum(values) for values in by_day.values()], dtype="float64")
    counts = np.asarray([len(values) for values in by_day.values()], dtype="float64")
    n_days = int(totals.size)
    mean_bp = float(totals.sum() / counts.sum()) if counts.sum() > 0 else 0.0
    rng = np.random.default_rng(seed)
    means = np.empty(iters, dtype="float64")
    for index in range(iters):
        pick = rng.integers(0, n_days, size=n_days)
        total = float(totals[pick].sum())
        count = float(counts[pick].sum())
        means[index] = total / count if count > 0 else 0.0
    return {
        "lower_bp": float(np.quantile(means, alpha / 2.0)),
        "upper_bp": float(np.quantile(means, 1.0 - alpha / 2.0)),
        "mean_bp": mean_bp,
        "n_days": float(n_days),
        "n_trades": float(len(valid)),
    }


def fold_returns(result: RunResult, start_ms: int, end_ms: int) -> pd.Series:
    """取 [start_ms, end_ms) 内的日收益（滚动验证拼接用）。"""
    daily = result.daily_returns()
    if daily.empty:
        return daily
    mask = (daily.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")) & (
        daily.index < pd.Timestamp(end_ms, unit="ms", tz="UTC")
    )
    return daily[mask]


def annualized_sharpe(returns: pd.Series) -> float:
    """日收益年化夏普。"""
    if returns.size < 2:
        return 0.0
    std = float(returns.std(ddof=1))
    return float(returns.mean() / std * np.sqrt(365)) if std > 0.0 else 0.0


def benchmark_daily_returns(result: RunResult) -> pd.Series:
    """BTCUSDT 日收益（与结果同一天然日口径，docs/13 §7）。

    Raises:
        ValueError: 结果里没有 BTCUSDT 日线（红线 12：β 与 alpha 夏普不得静默跳过）。
    """
    if result.benchmark_ts.size < 2:
        raise ValueError("结果缺少 BTCUSDT 日线，无法计算实现 β 与 alpha 夏普（红线 12）")
    index = pd.to_datetime(result.benchmark_ts, unit="ms", utc=True)
    series = pd.Series(result.benchmark_close, index=index)
    return series.pct_change().dropna()


def realized_beta(returns: pd.Series, benchmark: pd.Series) -> float:
    """日收益对 BTCUSDT 日收益的实现 β = cov(r, b) ÷ var(b)（docs/13 §7）。

    BTC 日收益方差为 0（例如测试用常数价格）时无法定义 β，返回 0.0。
    """
    frame = pd.concat(
        [returns.rename("strategy"), benchmark.rename("benchmark")], axis=1, join="inner"
    ).dropna()
    if frame.shape[0] < 2:
        return 0.0
    variance = float(frame["benchmark"].var(ddof=1))
    if variance <= 0.0:
        return 0.0
    return float(frame["strategy"].cov(frame["benchmark"]) / variance)


def alpha_sharpe(returns: pd.Series, benchmark: pd.Series) -> float:
    """剔除 BTC 收益后的年化夏普：超额收益 = 日收益 − β × BTC 日收益（docs/13 §7）。"""
    frame = pd.concat(
        [returns.rename("strategy"), benchmark.rename("benchmark")], axis=1, join="inner"
    ).dropna()
    if frame.shape[0] < 2:
        return annualized_sharpe(returns)
    beta = realized_beta(frame["strategy"], frame["benchmark"])
    excess = frame["strategy"] - beta * frame["benchmark"]
    return annualized_sharpe(excess)


def trades_between(trades: list[Trade], start_ms: int, end_ms: int) -> int:
    """[start_ms, end_ms) 内开仓的交易数。"""
    return sum(1 for trade in trades if start_ms <= trade.entry_ts < end_ms)


def summarize(
    result: RunResult, *, bootstrap: BootstrapConfig, trial_sharpes: list[float]
) -> dict[str, object]:
    """把回测结果整理成报告指标（docs/13 §7）。"""
    returns = result.daily_returns()
    equity = daily_equity(result)
    annualized = annualized_sharpe(returns)
    benchmark = benchmark_daily_returns(result)
    beta = realized_beta(returns, benchmark)
    monthly = monthly_returns(equity)
    total_costs = sum(trade.fees for trade in result.trades)
    gross = sum(trade.gross_pnl for trade in result.trades)
    net = sum(trade.net_pnl for trade in result.trades)
    exposures = np.asarray(result.net_exposure, dtype="float64")
    reasons: dict[str, int] = {}
    for trade in result.trades:
        reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
    return {
        "annualized_sharpe": annualized,
        "daily_sharpe": result.daily_sharpe(),
        "realized_beta": beta,
        "alpha_sharpe": alpha_sharpe(returns, benchmark),
        "max_drawdown": max_drawdown(equity),
        "monthly_returns": {str(index.date()): float(value) for index, value in monthly.items()},
        "loss_months": int((monthly < 0.0).sum()),
        "worst_month": float(monthly.min()) if monthly.size else 0.0,
        "yearly_returns": yearly_returns(equity),
        "trades": len(result.trades),
        "turnover": result.turnover / float(result.trades[0].entry_equity)
        if result.trades
        else 0.0,
        "cost_total": total_costs,
        "cost_ratio": total_costs / abs(gross) if gross != 0.0 else 0.0,
        "funding_total": result.funding_total,
        "gross_pnl": gross,
        "net_pnl": net,
        "long_gross": result.long_gross,
        "short_gross": result.short_gross,
        "long_net": sum(trade.net_pnl for trade in result.trades if trade.side > 0),
        "short_net": sum(trade.net_pnl for trade in result.trades if trade.side < 0),
        "dropped": result.dropped,
        "dropped_min_notional": result.dropped_min_notional,
        "attempted": result.attempted,
        "funding_estimated": result.funding_estimated,
        "funding_missing": result.funding_missing,
        "mark_fallback": result.mark_fallback,
        "forced_liquidations": result.forced_liquidations,
        "net_exposure_mean": float(exposures.mean()) if exposures.size else 0.0,
        "net_exposure_abs_p95": float(np.quantile(np.abs(exposures), 0.95))
        if exposures.size
        else 0.0,
        "exit_reasons": reasons,
        "bootstrap": bootstrap_trade_bp(result.trades, iters=bootstrap.iters, seed=bootstrap.seed),
        "deflated_sharpe": result.deflated(trial_sharpes),
        "risk_events": len(result.risk_events),
    }
