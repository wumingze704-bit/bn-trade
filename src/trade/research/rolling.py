"""滚动验证：折切分、折内选参、拼接样本外曲线（docs/13 §6、§7）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd  # type: ignore[import-untyped]

from trade.research.engine import Trade
from trade.research.metrics import annualized_sharpe

_MIN_TRAIN_TRADES = 30


@dataclass
class ParamRun:
    """一组参数在全研究期的运行结果（供折内选参与拼接）。"""

    key: str
    params: dict[str, object]
    daily: pd.Series
    trades: list[Trade]
    turnover: float
    dropped: int
    forced: int
    cost_total: float
    funding_total: float
    long_gross: float
    short_gross: float
    risk_events: int
    net_exposure_p95: float = 0.0
    params_meta: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Fold:
    """一个验证折（UTC 毫秒，右开区间）。"""

    index: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class FoldChoice:
    """某折选中的参数与训练期夏普。"""

    fold: Fold
    params_key: str
    train_sharpe: float
    train_trades: int


def _month_start_ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def fold_windows(
    research_start_ms: int, research_end_ms: int, *, train_months: int, folds: int
) -> list[Fold]:
    """训练期之后按整月切成 `folds` 个等长折（余数并入最后一折）。"""
    start = datetime.fromtimestamp(research_start_ms // 1000, tz=UTC)
    end = datetime.fromtimestamp((research_end_ms - 1) // 1000, tz=UTC)
    end_month = end.year * 12 + end.month
    if end.day > 1:
        end_month += 1
    total_months = end_month - (start.year * 12 + start.month)
    if total_months <= train_months + folds - 1:
        raise ValueError(
            f"研究期只有 {total_months} 个月，不足以在 {train_months} 个月训练期后切 {folds} 折"
        )
    span = total_months - train_months
    base = span // folds
    windows: list[Fold] = []
    cursor_year, cursor_month = _add_months(start.year, start.month, train_months)
    for index in range(folds):
        months = base if index < folds - 1 else span - base * (folds - 1)
        next_year, next_month = _add_months(cursor_year, cursor_month, months)
        windows.append(
            Fold(
                index=index,
                start_ms=_month_start_ms(cursor_year, cursor_month),
                end_ms=_month_start_ms(next_year, next_month),
            )
        )
        cursor_year, cursor_month = next_year, next_month
    return windows


def _slice(daily: pd.Series, start_ms: int, end_ms: int) -> pd.Series:
    if daily.empty:
        return daily
    mask = (daily.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")) & (
        daily.index < pd.Timestamp(end_ms, unit="ms", tz="UTC")
    )
    return daily[mask]


def _trades_between(trades: list[Trade], start_ms: int, end_ms: int) -> int:
    return sum(1 for trade in trades if start_ms <= trade.entry_ts < end_ms)


def select_for_fold(runs: Mapping[str, ParamRun], fold: Fold, research_start_ms: int) -> FoldChoice:
    """折开始之前的全部数据上按年化夏普选参，要求训练期交易数 ≥ 30（docs/13 §7）。"""
    best: tuple[float, int, str] | None = None
    for key, run in runs.items():
        trades = _trades_between(run.trades, research_start_ms, fold.start_ms)
        if trades < _MIN_TRAIN_TRADES:
            continue
        sharpe = annualized_sharpe(_slice(run.daily, research_start_ms, fold.start_ms))
        candidate = (sharpe, -trades, key)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise ValueError(f"第 {fold.index + 1} 折没有满足训练期交易数 ≥ {_MIN_TRAIN_TRADES} 的参数")
    sharpe, negative_trades, key = best
    return FoldChoice(fold=fold, params_key=key, train_sharpe=sharpe, train_trades=-negative_trades)


def concatenated_returns(
    runs: Mapping[str, ParamRun], folds: list[Fold], research_start_ms: int
) -> tuple[pd.Series, list[FoldChoice]]:
    """按折选参并把各折样本外日收益拼成一条曲线（docs/13 §7 第 10 条）。"""
    pieces: list[pd.Series] = []
    choices: list[FoldChoice] = []
    for fold in folds:
        choice = select_for_fold(runs, fold, research_start_ms)
        choices.append(choice)
        piece = _slice(runs[choice.params_key].daily, fold.start_ms, fold.end_ms)
        if not piece.empty:
            pieces.append(piece)
    if not pieces:
        raise ValueError("拼接曲线为空：检查研究期与折切分")
    return pd.concat(pieces).sort_index(), choices


def selected_trades(
    runs: Mapping[str, ParamRun], folds: list[Fold], choices: list[FoldChoice]
) -> list[Trade]:
    """选参之后各折内实际发生的交易（用于拼接曲线的 bootstrap 与分项统计）。"""
    trades: list[Trade] = []
    for fold, choice in zip(folds, choices, strict=True):
        run = runs[choice.params_key]
        trades.extend(
            trade for trade in run.trades if fold.start_ms <= trade.entry_ts < fold.end_ms
        )
    return trades
