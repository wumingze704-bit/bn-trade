"""事件驱动参考模拟器（06 §3.4，用于 S9 对拍）。

逐分钟回放 bars，复用与实盘共用的出场/仓位/成本代码（06 §3.4 注、AGENTS.md §6 红线 4）。
它是“实盘到底会怎么做”的慢速标准答案；P5 实盘引擎落地后与它进一步对齐。
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    ExecutedTrade,
    PortfolioResult,
    PrecomputedTrade,
    SkippedSignal,
    TradeRule,
    _close_lookup,
    _mark_to_market_curve,
    precompute_trades,
)
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig, calc_notional, equity_floor_hit


def event_sim(
    bars_by_symbol: dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    *,
    scen_name: str,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    initial_equity: float | None = None,
    trade_masks: dict[str, np.ndarray] | None = None,
    new_entry_symbols: set[str] | None = None,
) -> PortfolioResult:
    """事件驱动参考模拟：逐分钟回放，按分钟时钟结算离场与开仓（06 §3.4）。"""
    scen = scenario(scen_name)
    all_trades: list[PrecomputedTrade] = []
    all_skipped: list[SkippedSignal] = []
    for symbol, bars in bars_by_symbol.items():
        sym_signals = signals[signals["symbol"] == symbol]
        if sym_signals.empty:
            continue
        trades, skipped = precompute_trades(
            bars,
            sym_signals,
            symbol=symbol,
            group=groups[symbol],
            rule=rules[symbol],
            trade_mask=trade_masks.get(symbol) if trade_masks else None,
            new_entry=bool(new_entry_symbols and symbol in new_entry_symbols),
            scen=scen,
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=H_label,
        )
        all_trades.extend(trades)
        all_skipped.extend(skipped)
    return _event_combine(
        all_trades,
        all_skipped,
        sizing_cfg=sizing_cfg,
        backtest_cfg=backtest_cfg,
        fees=fees,
        initial_equity=initial_equity,
        bars_by_symbol=bars_by_symbol,
    )


def _event_combine(
    all_trades: list[PrecomputedTrade],
    all_skipped: list[SkippedSignal],
    *,
    sizing_cfg: SizingConfig,
    backtest_cfg: BacktestConfig,
    fees: FeeSchedule,
    initial_equity: float | None,
    bars_by_symbol: dict[str, pd.DataFrame],
) -> PortfolioResult:
    """按分钟时钟推进的组合模拟（独立于 engine.simulate_portfolio 的实现）。"""
    start_equity = sizing_cfg.initial_equity if initial_equity is None else initial_equity
    equity = start_equity
    positions: dict[str, ExecutedTrade] = {}
    cooldown_until: dict[str, int] = {}
    daily_realized: dict[str, float] = {}
    result = PortfolioResult(skipped=list(all_skipped))
    min_min_notional = min((t.min_notional for t in all_trades), default=1e18)
    close_lookup = _close_lookup(bars_by_symbol)

    by_entry: dict[int, list[PrecomputedTrade]] = defaultdict(list)
    for tr in all_trades:
        by_entry[tr.entry_open_time].append(tr)
    for ts in by_entry:
        by_entry[ts].sort(key=lambda tr: (-tr.p, -tr.m))

    times = sorted({t.entry_open_time for t in all_trades} | {t.exit_open_time for t in all_trades})

    for ts in times:
        for sym in sorted(
            (s for s, t in positions.items() if t.exit_open_time <= ts),
            key=lambda s: positions[s].exit_open_time,
        ):
            trade = positions[sym]
            equity += trade.net_pnl
            day = _utc_day(trade.exit_open_time)
            daily_realized[day] = daily_realized.get(day, 0.0) + trade.net_pnl
            if trade.exit_type_code in (0, 1, 2):
                cooldown_until[sym] = (
                    trade.exit_open_time + backtest_cfg.risk.cooldown_bars * 60_000
                )
            del positions[sym]

        for tr in by_entry.get(ts, []):
            if tr.symbol in positions:
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "already_in_position")
                )
                continue
            if tr.entry_open_time < cooldown_until.get(tr.symbol, 0):
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "cooldown")
                )
                continue
            if len(positions) >= sizing_cfg.max_positions:
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "max_positions")
                )
                continue
            day = _utc_day(tr.entry_open_time)
            if daily_realized.get(day, 0.0) < -sizing_cfg.daily_loss_stop * equity:
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "daily_loss_stop")
                )
                continue
            base = equity if sizing_cfg.base == "equity" else sizing_cfg.initial_equity
            if equity_floor_hit(base, sizing_cfg, min_min_notional):
                result.equity_floor_hit = True
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "equity_floor")
                )
                continue
            available_margin = equity - sum(t.margin for t in positions.values())
            sz = calc_notional(
                base=base,
                sl=tr.sl,
                loss_rate=tr.loss_rate,
                entry_price=tr.p_fill,
                mean_qv_60=tr.mean_qv_60,
                available_margin=available_margin,
                step_size=tr.step_size,
                min_notional=tr.min_notional,
                cfg=sizing_cfg,
            )
            if not sz.accepted:
                result.skipped.append(
                    SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, sz.status)
                )
                continue
            funding_fee = sz.notional * tr.funding_rate_sum
            net_pnl = sz.notional * (tr.gross_return - fees.taker - tr.exit_fee_rate) - funding_fee
            trade = ExecutedTrade(
                symbol=tr.symbol,
                group=tr.group,
                signal_open_time=tr.signal_open_time,
                entry_open_time=tr.entry_open_time,
                exit_open_time=tr.exit_open_time,
                p=tr.p,
                m=tr.m,
                p_fill=tr.p_fill,
                sl=tr.sl,
                exit_price=tr.exit_price,
                exit_type=tr.exit_type,
                exit_type_code=tr.exit_type_code,
                notional=sz.notional,
                qty=sz.qty,
                margin=sz.margin,
                bars_held=tr.bars_held,
                stop_updates=tr.stop_updates,
                mfe_r=tr.mfe_r,
                mae_r=tr.mae_r,
                entry_fee=sz.notional * fees.taker,
                exit_fee=sz.notional * tr.exit_fee_rate,
                funding_fee=funding_fee,
                gross_pnl=sz.notional * tr.gross_return,
                net_pnl=net_pnl,
                capped_by=sz.capped_by,
                new_entry=tr.new_entry,
            )
            positions[tr.symbol] = trade
            result.trades.append(trade)

    for sym in sorted(positions, key=lambda s: positions[s].exit_open_time):
        trade = positions[sym]
        equity += trade.net_pnl
    positions.clear()

    result.final_equity = equity
    result.equity_curve = _mark_to_market_curve(result.trades, close_lookup, start_equity)
    if (
        not result.equity_curve.empty
        and float(result.equity_curve["drawdown"].max()) >= sizing_cfg.max_drawdown_halt
    ):
        result.max_drawdown_halt = True
    return result


def _utc_day(ts_ms: int) -> str:
    """UTC 日（06 §3.1 条件 4）。"""
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"
