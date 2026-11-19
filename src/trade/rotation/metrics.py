"""轮动回测指标：日收益、夏普、回撤、月/季收益、换手与 bootstrap 置信区间。

规格：docs/11-轮动策略.md §6
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.rotation.data import MS_DAY
from trade.rotation.engine import RotationTrade, SimResult

# 日收益分块 bootstrap（08 §3 G2 同口径，11 §6）
BOOTSTRAP_ITERS = 10_000
BOOTSTRAP_SEED = 42


def _utc_day(ts_ms: int) -> pd.Timestamp:
    """毫秒时间戳 → UTC 自然日（无时区）。"""
    return pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_localize(None).normalize()


def daily_equity(result: SimResult, initial_equity: float) -> pd.Series:
    """按 UTC 自然日补齐的权益序列（缺失日沿用上一值）。"""
    if not result.daily:
        return pd.Series(dtype="float64")
    index = [_utc_day(ts) for ts, _ in result.daily]
    series = pd.Series([equity for _, equity in result.daily], index=pd.DatetimeIndex(index))
    series = series[~series.index.duplicated(keep="last")].sort_index()
    calendar = pd.date_range(series.index.min(), series.index.max(), freq="D")
    return series.reindex(calendar).ffill()


def daily_returns(result: SimResult, initial_equity: float) -> pd.Series:
    """日收益（首日相对初始权益），无采样点时返回空序列。"""
    series = daily_equity(result, initial_equity)
    if series.empty or series.shape[0] < 2:
        return pd.Series(dtype="float64")
    base = pd.Series([float(initial_equity)], index=[series.index[0] - pd.Timedelta(days=1)])
    joined = pd.concat([base, series])
    return joined.pct_change().dropna()


def sharpe(returns: pd.Series) -> float:
    """年化夏普（×√365）。"""
    if returns.shape[0] < 2 or returns.std() <= 0:
        return 0.0
    return float(returns.mean() / returns.std() * math.sqrt(365.0))


def max_drawdown(series: pd.Series) -> float:
    """日末盯市权益的最大回撤。"""
    if series.empty:
        return 0.0
    peak = series.cummax()
    return float(((peak - series) / peak.where(peak > 0)).max())


def monthly_returns(series: pd.Series, initial_equity: float, months: Sequence[str]) -> pd.Series:
    """UTC 自然月收益（`months` 为月份网格，无数据的月份补 0）。"""
    out: dict[str, float] = {}
    prev = float(initial_equity)
    if series.empty:
        return pd.Series({month: 0.0 for month in months}, dtype="float64")
    ends = series.groupby(series.index.strftime("%Y-%m")).last()
    for month in months:
        end = float(ends.get(month, prev))
        out[month] = end / prev - 1.0 if prev else 0.0
        prev = end
    return pd.Series(out, dtype="float64")


def quarter_returns(series: pd.Series, initial_equity: float) -> dict[str, float]:
    """自然季度收益（首季度相对初始权益）。"""
    if series.empty:
        return {}
    quarters = series.groupby(series.index.to_period("Q").astype(str)).last()
    out: dict[str, float] = {}
    prev = float(initial_equity)
    for quarter, end in quarters.items():
        out[str(quarter)] = float(end) / prev - 1.0 if prev else 0.0
        prev = float(end)
    return out


def segment_returns(
    series: pd.Series, initial_equity: float, segments: Sequence[tuple[str, str]]
) -> dict[str, float]:
    """按给定月份区间计算的阶段收益（首段相对初始权益）。"""
    out: dict[str, float] = {}
    prev = float(initial_equity)
    for label, (first_month, last_month) in enumerate(segments):
        keys = series.index.strftime("%Y-%m")
        mask = (keys >= first_month) & (keys <= last_month)
        window = series[mask]
        if window.empty:
            out[f"segment_{label + 1}"] = 0.0
            continue
        end = float(window.iloc[-1])
        out[f"segment_{label + 1}"] = end / prev - 1.0 if prev else 0.0
        prev = end
    return out


def mean_bootstrap_ci(
    returns: pd.Series,
    *,
    iters: int = BOOTSTRAP_ITERS,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """日收益均值的整日 bootstrap 置信区间（11 §6）。

    Args:
        returns: UTC 日收益序列。
        iters: 重抽样次数。
        seed: 随机种子。
        alpha: 显著性水平（0.05 → 95% 区间）。

    Returns:
        (下界, 上界, 点估计) 的日收益均值。
    """
    values = returns.to_numpy(dtype="float64")
    if values.size < 2:
        return (0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.size, size=(iters, values.size))
    means = values[idx].mean(axis=1)
    return (
        float(np.quantile(means, alpha / 2.0)),
        float(np.quantile(means, 1.0 - alpha / 2.0)),
        float(values.mean()),
    )


@dataclass
class MetricsBundle:
    """一个情景的全部指标（写 summary.json 用）。"""

    values: dict[str, float]
    monthly: dict[str, float]
    quarters: dict[str, float]
    segments: dict[str, float]
    exit_reasons: dict[str, int]
    holding_days_quantiles: dict[str, float]
    skipped: dict[str, int]
    risk_events: dict[str, int]


def _cost_ratio(trades: Sequence[RotationTrade]) -> float:
    """成本占毛收益比例（毛收益按 p_1s 计）。"""
    gross = sum(trade.gross_pnl_p1s for trade in trades)
    if gross <= 0.0:
        return float("nan")
    return sum(trade.cost for trade in trades) / gross


def compute_metrics(
    result: SimResult,
    *,
    initial_equity: float,
    months: Sequence[str],
    segments: Sequence[tuple[str, str]],
    eval_years: float,
) -> MetricsBundle:
    """计算单个情景的全部指标（11 §6）。

    Args:
        result: 一个情景的模拟结果。
        initial_equity: 初始权益，USDT。
        months: 评估段 UTC 月份网格（补零月）。
        segments: 4 段（起月, 止月）区间，用于“至少 3 段为正”判定。
        eval_years: 评估区间年数（换手率年化用）。

    Returns:
        MetricsBundle。
    """
    equity = daily_equity(result, initial_equity)
    returns = daily_returns(result, initial_equity)
    monthly = monthly_returns(equity, initial_equity, months)
    trades = result.trades
    low, high, mean = mean_bootstrap_ci(returns)
    n_trades = len(trades)
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = -sum(trade.net_pnl for trade in losses)
    traded_notional = sum(trade.notional * 2.0 for trade in trades)
    mean_equity = float(equity.mean()) if not equity.empty else initial_equity
    holding_days = np.array([trade.holding_hours / 24.0 for trade in trades], dtype="float64")
    segments_return = segment_returns(equity, initial_equity, segments)
    values = {
        "final_equity": float(result.final_equity),
        "net_return": float(result.final_equity) / initial_equity - 1.0,
        "sharpe": sharpe(returns),
        "max_drawdown": max_drawdown(equity),
        "n_trades": float(n_trades),
        "win_rate": len(wins) / n_trades if n_trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else math.inf,
        "losing_months": float((monthly < 0).sum()) if not monthly.empty else 0.0,
        "worst_month": float(monthly.min()) if not monthly.empty else 0.0,
        "median_month": float(monthly.median()) if not monthly.empty else 0.0,
        "positive_segments": float(sum(1 for v in segments_return.values() if v > 0)),
        "turnover_annual": (
            traded_notional / mean_equity / eval_years
            if mean_equity > 0 and eval_years > 0
            else 0.0
        ),
        "cost_ratio": _cost_ratio(trades),
        "total_cost": float(sum(trade.cost for trade in trades)),
        "bootstrap_mean_low": low,
        "bootstrap_mean_high": high,
        "bootstrap_mean": mean,
        "filter_off_decisions": float(result.filter_off_minutes),
        "risk_events": float(len(result.risk_events)),
        "holding_days_p50": float(np.median(holding_days)) if holding_days.size else 0.0,
    }
    exit_reasons = Counter(trade.exit_reason for trade in trades)
    holding_quantiles = {
        "p10": float(np.quantile(holding_days, 0.10)) if holding_days.size else 0.0,
        "p50": float(np.quantile(holding_days, 0.50)) if holding_days.size else 0.0,
        "p90": float(np.quantile(holding_days, 0.90)) if holding_days.size else 0.0,
    }
    return MetricsBundle(
        values=values,
        monthly={str(k): float(v) for k, v in monthly.items()},
        quarters=quarter_returns(equity, initial_equity),
        segments=segments_return,
        exit_reasons=dict(exit_reasons),
        holding_days_quantiles=holding_quantiles,
        skipped=dict(result.skipped),
        risk_events=dict(Counter(event.rule for event in result.risk_events)),
    )


def equity_frame(result: SimResult, initial_equity: float) -> pd.DataFrame:
    """权益曲线（日末盯市）DataFrame：ts_ms、equity、drawdown。"""
    if not result.daily:
        return pd.DataFrame({"ts_ms": [], "equity": [], "drawdown": []})
    ts = np.array([item[0] for item in result.daily], dtype="int64")
    equity = np.array([item[1] for item in result.daily], dtype="float64")
    peak = np.maximum.accumulate(equity)
    drawdown = np.where(peak > 0, (peak - equity) / peak, 0.0)
    frame = pd.DataFrame({"ts_ms": ts, "equity": equity, "drawdown": drawdown})
    frame.attrs["initial_equity"] = initial_equity
    return frame


def trades_frame(result: SimResult) -> pd.DataFrame:
    """逐笔交易表（trades.parquet）。"""
    return pd.DataFrame(
        [
            {
                "symbol": trade.symbol,
                "entry_ts_ms": trade.entry_ts_ms,
                "exit_ts_ms": trade.exit_ts_ms,
                "entry_p1s": trade.entry_p1s,
                "exit_p1s": trade.exit_p1s,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "qty": trade.qty,
                "notional": trade.notional,
                "stop_pct": trade.stop_pct,
                "entry_fee": trade.entry_fee,
                "exit_fee": trade.exit_fee,
                "funding": trade.funding,
                "gross_pnl_p1s": trade.gross_pnl_p1s,
                "net_pnl": trade.net_pnl,
                "exit_reason": trade.exit_reason,
                "entry_rank": trade.entry_rank,
                "entry_score": trade.entry_score,
                "holding_hours": trade.holding_hours,
            }
            for trade in result.trades
        ]
    )


def eval_segments(months: Sequence[str]) -> list[tuple[str, str]]:
    """把评估月份按顺序切成 4 段（11 §6）。"""
    usable = list(months)
    if not usable:
        return []
    if len(usable) < 4:
        return [(usable[0], usable[-1])]
    bounds = np.linspace(0, len(usable), 5).round().astype(int)
    return [(usable[bounds[i]], usable[bounds[i + 1] - 1]) for i in range(4)]


def evaluate(
    result: SimResult,
    *,
    initial_equity: float,
    months: Sequence[str],
    eval_start_ms: int,
    eval_end_ms: int,
) -> MetricsBundle:
    """按评估窗口计算指标（自动切 4 段并换算年数）。"""
    if not months:
        raise ValueError("评估月份列表为空（11 §6）")
    years = (eval_end_ms - eval_start_ms) / (365.0 * MS_DAY)
    return compute_metrics(
        result,
        initial_equity=initial_equity,
        months=list(months),
        segments=eval_segments(months),
        eval_years=years,
    )
