"""回测指标（06 §6）。

所有指标按 全部币/训练组/留出组/新进入组 分组时，用 group 过滤 trades（03 §4.5）。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.engine import ExecutedTrade, PortfolioResult


def compute_metrics(
    result: PortfolioResult,
    initial_equity: float,
    *,
    group: str | None = None,
    months: Sequence[str] | None = None,
) -> dict[str, float]:
    """计算核心指标（06 §6）。

    Args:
        result: 组合模拟结果。
        initial_equity: 初始权益。
        group: 只统计该币种组（train/holdout/new_entry）；None 表示全部。
        months: 评估段的 UTC 月份列表（`YYYY-MM`，用于月度统计补零，06 §6）。

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

    rebate_total = sum(t.rebate for t in trades)
    net_pnl_rebated = net_pnl + rebate_total
    wins_rebated = [t for t in trades if t.net_pnl + t.rebate > 0]
    losses_rebated = [t for t in trades if t.net_pnl + t.rebate < 0]
    profit_rebated = sum(t.net_pnl + t.rebate for t in wins_rebated)
    loss_rebated = -sum(t.net_pnl + t.rebate for t in losses_rebated)

    return {
        "n_trades": float(n),
        "final_equity": final_equity,
        "net_return_usdt": net_pnl,
        "net_return_pct": net_pnl / initial_equity if initial_equity else 0.0,
        "rebate_usdt": rebate_total,
        "net_return_usdt_rebated": net_pnl_rebated,
        "net_return_pct_rebated": net_pnl_rebated / initial_equity if initial_equity else 0.0,
        "profit_factor_rebated": profit_rebated / loss_rebated if loss_rebated > 0 else math.inf,
        "avg_pnl_per_trade_rebated": net_pnl_rebated / n if n else 0.0,
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
        **(
            monthly_return_stats(result.equity_curve, initial_equity, months=months)
            if group is None
            else {}
        ),
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
    "risk_halt",
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
    """从权益曲线计算日收益（UTC 日，按自然日补齐零收益日），用于夏普/索提诺（06 §6）。

    权益曲线只在持仓 K 线收盘与离场时刻采样；空仓日没有采样点，若直接对采样日
    求收益会把夏普放大约 1/√(持仓日占比)。这里按 UTC 自然日补齐：首末采样日之间
    的每一天都出现在序列里，没有采样点的日子沿用上一日权益（收益 0），首个交易日
    的收益相对 `initial_equity` 计算。

    Args:
        equity_curve: 引擎输出，含 `ts_ms` 与 `equity` 列。
        initial_equity: 初始权益（首日基准）。

    Returns:
        以 `datetime.date` 为索引的日收益序列；无采样点时返回空序列。
    """
    if equity_curve is None or equity_curve.empty or len(equity_curve) < 2:
        return pd.Series(dtype="float64")
    frame = equity_curve.copy()
    frame = frame[frame["ts_ms"] > 0]
    if frame.empty:
        return pd.Series(dtype="float64")
    frame["day"] = (
        pd.to_datetime(frame["ts_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    )
    daily = frame.groupby("day")["equity"].last().sort_index()
    calendar = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(calendar).ffill()
    series = pd.concat(
        [pd.Series([float(initial_equity)], index=[calendar[0] - pd.Timedelta(days=1)]), daily]
    )
    return series.pct_change().dropna()


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


def sharpe_daily(returns: pd.Series) -> float:
    """日收益的非年化夏普（06 §7 S11：Deflated Sharpe 必须与 `n_obs` 同周期）。

    非年化夏普 = 日收益均值 / 日收益标准差；用于 S11 的 `deflated_sharpe`，
    与年化夏普混用会把 z 值放大 √365 倍，DSR 虚高。
    """
    if len(returns) < 2 or returns.std() <= 0:
        return 0.0
    return float(returns.mean() / returns.std())


def sortino(returns: pd.Series) -> float:
    """日收益年化索提诺（下行标准差，06 §6）。"""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if downside.empty or downside.std() <= 0:
        return 0.0
    return float(returns.mean() / downside.std() * math.sqrt(365))


def skew(returns: pd.Series) -> float:
    """日收益偏度 γ₃（无量纲，06 §7 S11）。

    用于 Deflated Sharpe 的 PSR 方差项 `1 − γ₃·SR + (γ₄−1)/4·SR²`。总体三阶
    标准矩 E[(x−μ)³]/σ³，与 `deflated_sharpe` 的 `skew` 参数口径一致。

    Args:
        returns: 日收益序列（`daily_returns` 的输出）。

    Returns:
        偏度；样本不足 3 个或标准差为 0 时返回 0.0（正态分布为 0）。
    """
    values = returns.to_numpy(dtype="float64")
    if values.size < 3:
        return 0.0
    sigma = values.std()
    if sigma <= 0.0:
        return 0.0
    return float(((values - values.mean()) ** 3).mean() / sigma**3)


def kurtosis(returns: pd.Series) -> float:
    """日收益峰度 γ₄（无量纲，正态为 3，06 §7 S11）。

    用于 Deflated Sharpe 的 PSR 方差项 `1 − γ₃·SR + (γ₄−1)/4·SR²`。总体四阶
    标准矩 E[(x−μ)⁴]/σ⁴，与 `deflated_sharpe` 的 `kurtosis` 参数口径一致。

    Args:
        returns: 日收益序列（`daily_returns` 的输出）。

    Returns:
        峰度；样本不足 4 个或标准差为 0 时返回 3.0（正态分布为 3）。
    """
    values = returns.to_numpy(dtype="float64")
    if values.size < 4:
        return 3.0
    sigma = values.std()
    if sigma <= 0.0:
        return 3.0
    return float(((values - values.mean()) ** 4).mean() / sigma**4)


def monthly_returns(
    equity_curve: pd.DataFrame,
    initial_equity: float,
    *,
    months: Sequence[str] | None = None,
) -> pd.Series:
    """UTC 自然月收益序列（08 §3 G2-U）。

    每行取该月最后一个盯市权益；分母为上一月最后盯市权益，首月用 `initial_equity`。
    空仓月没有采样点，按上一月末权益补齐（收益 0）；`months` 给出评估段月份时
    直接以该列表为月份网格，避免只用有交易的月份做统计。

    Args:
        equity_curve: 引擎输出，含 `ts_ms` 与 `equity` 列。
        initial_equity: 初始权益（首月基准）。
        months: 评估段 UTC 月份列表（`YYYY-MM`）；None 时用首末采样月之间的全部月份。

    Returns:
        `YYYY-MM` -> 月收益率（比例，0.01 = 1%）。
    """
    if equity_curve is None or equity_curve.empty:
        grid = list(months) if months else []
        return pd.Series(0.0, index=grid, dtype="float64") if grid else pd.Series(dtype="float64")
    frame = equity_curve.loc[:, ["ts_ms", "equity"]].drop_duplicates("ts_ms").sort_values("ts_ms")
    frame = frame[frame["ts_ms"] > 0]
    month_keys = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    end_by_month: dict[str, float] = {
        str(month): float(group["equity"].iloc[-1])
        for month, group in frame.groupby(month_keys.to_numpy(), sort=True)
    }
    if months:
        grid = list(months)
    elif end_by_month:
        grid = _month_range(min(end_by_month), max(end_by_month))
    else:
        return pd.Series(dtype="float64")
    out: dict[str, float] = {}
    prev = float(initial_equity)
    for month in grid:
        end = end_by_month.get(month, prev)
        out[month] = end / prev - 1.0 if prev else 0.0
        prev = end
    return pd.Series(out, dtype="float64")


def _month_range(first: str, last: str) -> list[str]:
    """闭区间月份列表（`YYYY-MM`）。"""
    year, month = (int(part) for part in first.split("-"))
    end_year, end_month = (int(part) for part in last.split("-"))
    out: list[str] = []
    while (year, month) <= (end_year, end_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def monthly_return_stats(
    equity_curve: pd.DataFrame,
    initial_equity: float,
    *,
    months: Sequence[str] | None = None,
) -> dict[str, float]:
    """月收益统计：月数、月收益中位数、最差月、亏损月数（08 §3 G2-U）。"""
    returns = monthly_returns(equity_curve, initial_equity, months=months)
    if returns.empty:
        return {
            "months": 0.0,
            "median_monthly_return": float("nan"),
            "worst_monthly_return": float("nan"),
            "losing_months": 0.0,
        }
    return {
        "months": float(returns.size),
        "median_monthly_return": float(returns.median()),
        "worst_monthly_return": float(returns.min()),
        "losing_months": float((returns < 0).sum()),
    }
