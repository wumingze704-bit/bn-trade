"""轮动组合模拟：调仓、成交、资金费、灾难止损与 R5~R8 风控。

规格：docs/11-轮动策略.md §5、§6
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from trade.common.risk_rules import (
    HALT_STRICT,
    RiskEvent,
    RiskLimits,
    RiskState,
    register_exit,
    register_pause_if_needed,
    risk_step,
    utc_day,
)
from trade.common.sizing import SizingConfig, calc_notional, loss_rate
from trade.rotation.config import RotationConfig
from trade.rotation.data import (
    MS_DAY,
    MS_HOUR,
    MS_MIN,
    HourlyPanel,
    MinuteChunk,
    UniverseMonth,
    build_minute_chunk,
    month_key,
)
from trade.rotation.signals import Decision

# 情景定义（11 §7）
SCENARIO_NAMES = ("base", "cost2x", "nohalt", "delay1", "shuffle")
SHUFFLE_SEED = 42


@dataclass(frozen=True)
class Scenario:
    """一个回测情景的开关组合。"""

    name: str
    fee_mult: float = 1.0
    cost_mult: float = 1.0
    halt_enabled: bool = True
    decision_lag_steps: int = 0
    shuffle_seed: int | None = None


def scenario_by_name(name: str) -> Scenario:
    """按名称返回情景（11 §7）。"""
    if name == "base":
        return Scenario("base")
    if name == "cost2x":
        return Scenario("cost2x", fee_mult=2.0, cost_mult=2.0)
    if name == "nohalt":
        return Scenario("nohalt", halt_enabled=False)
    if name == "delay1":
        return Scenario("delay1", decision_lag_steps=1)
    if name == "shuffle":
        return Scenario("shuffle", shuffle_seed=SHUFFLE_SEED)
    if name == "neighbors":
        return Scenario("neighbors")
    raise ValueError(f"未知情景：{name}（11 §7）")


@dataclass(frozen=True)
class SymbolRules:
    """交易规则（exchangeInfo 快照，09 §3）。"""

    tick_size: float
    step_size: float
    min_notional: float


@dataclass(frozen=True)
class CostModel:
    """成本参数（base.yaml 费率 + backtest.yaml 最小值，11 §5.5）。"""

    fee_taker: float
    half_spread_min: float
    slip_min: float
    impact_y: float
    notional_ref: float


@dataclass
class RotationTrade:
    """一笔已完成的持仓。"""

    symbol: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_p1s: float
    exit_p1s: float
    entry_price: float
    exit_price: float
    qty: float
    notional: float
    stop_pct: float
    entry_fee: float
    exit_fee: float
    funding: float
    gross_pnl_p1s: float
    net_pnl: float
    exit_reason: str
    entry_rank: int
    entry_score: float

    @property
    def holding_hours(self) -> float:
        """持仓小时数（含跨越的资金费结算）。"""
        return (self.exit_ts_ms - self.entry_ts_ms) / MS_HOUR

    @property
    def cost(self) -> float:
        """总成本 = 毛收益（按 p_1s 计）− 净收益。"""
        return self.gross_pnl_p1s - self.net_pnl


@dataclass
class Position:
    """持仓状态。"""

    symbol: str
    entry_ts_ms: int
    entry_p1s: float
    entry_price: float
    qty: float
    notional: float
    margin: float
    stop_price: float
    stop_pct: float
    entry_fee: float
    entry_rank: int
    entry_score: float
    funding_paid: float = 0.0
    funding_idx: int = 0
    next_stop_k: int = -1
    last_price: float = float("nan")


@dataclass
class SimResult:
    """一个情景的模拟结果。"""

    scenario: str
    trades: list[RotationTrade]
    daily: list[tuple[int, float]]
    final_equity: float
    skipped: dict[str, int] = field(default_factory=dict)
    risk_events: list[RiskEvent] = field(default_factory=list)
    filter_off_minutes: int = 0


@dataclass
class SimContext:
    """一次回测运行所需的全部输入（多情景共享）。"""

    panel: HourlyPanel
    calendar: dict[str, UniverseMonth]
    cfg: RotationConfig
    sizing: SizingConfig
    limits: RiskLimits
    costs: CostModel
    rules: dict[str, SymbolRules]
    aliases: dict[str, str]
    data_root: Path
    holdout_start_ms: int
    allow_holdout: bool
    initial_equity: float
    months: list[str]
    chunk_symbols: dict[str, tuple[str, ...]]
    eval_start_ms: int
    eval_end_ms: int
    schedules: dict[str, dict[int, Decision]] = field(default_factory=dict)
    exit_schedules: dict[str, dict[int, Decision]] = field(default_factory=dict)

    @property
    def rebalance_ms(self) -> int:
        return self.cfg.rebalance_hours * MS_HOUR

    @property
    def buffer_k(self) -> int:
        return self.cfg.top_k * self.cfg.buffer_mult


def _is_num(value: float) -> bool:
    """有限且为正数。"""
    return bool(np.isfinite(value)) and value > 0.0


def _nohalt_limits(limits: RiskLimits) -> RiskLimits:
    """关闭 R6/R8 停机与 R7 暂停（11 §7 nohalt 情景；R5 仍生效）。"""
    return RiskLimits(
        daily_loss_stop=limits.daily_loss_stop,
        daily_loss_halt=0.99,
        max_drawdown_halt=0.99,
        max_consecutive_losses=10_000,
        consecutive_loss_pause_min=limits.consecutive_loss_pause_min,
        halt_mode=limits.halt_mode,
        halt_reset_days=limits.halt_reset_days,
    )


def build_risk_limits(*, sizing: SizingConfig, halt_mode: str, halt_reset_days: int) -> RiskLimits:
    """由 sizing.yaml 与 backtest.yaml 的 risk 段构造风控参数（06 §3.4）。"""
    return RiskLimits(
        daily_loss_stop=sizing.daily_loss_stop,
        daily_loss_halt=sizing.daily_loss_halt,
        max_drawdown_halt=sizing.max_drawdown_halt,
        max_consecutive_losses=sizing.max_consecutive_losses,
        consecutive_loss_pause_min=sizing.consecutive_loss_pause_min,
        halt_mode=halt_mode if halt_mode in (HALT_STRICT, "reset") else HALT_STRICT,
        halt_reset_days=halt_reset_days,
    )


def _mode_value(value: Any) -> str:
    """把配置里的停字符串规整为 `strict` / `reset`。"""
    return str(value)


class Portfolio:
    """单个情景的组合账户状态机（逐分钟推进）。"""

    def __init__(self, scenario: Scenario, ctx: SimContext) -> None:
        self.scenario = scenario
        self.ctx = ctx
        self.limits = ctx.limits if scenario.halt_enabled else _nohalt_limits(ctx.limits)
        self.cash = ctx.initial_equity
        self.positions: dict[str, Position] = {}
        self.cooldown: dict[str, int] = {}
        self.risk = RiskState(peak_equity=ctx.initial_equity)
        self.trades: list[RotationTrade] = []
        self.daily: list[tuple[int, float]] = []
        self.skipped: dict[str, int] = {}
        self.pending_flatten = False
        self.filter_off = 0
        self.qv_history: dict[str, np.ndarray] = {}
        self.schedule = ctx.schedules.get(scenario.name, {})
        self.exit_schedule = ctx.exit_schedules.get(scenario.name, {})

    # ---- 成本与估值 ----
    def _costs(self, symbol: str, chunk: MinuteChunk, k: int, price: float) -> tuple[float, float]:
        """返回 (半价差, 单边滑点)：复用 trade.common.costs 的公式与最小值（11 §5.5）。"""
        rules = self.ctx.rules[symbol]
        mult = self.scenario.cost_mult
        tick_rel = rules.tick_size / price
        half = max(0.5 * tick_rel, self.ctx.costs.half_spread_min * mult)
        r = chunk.row.get(symbol)
        if r is None:
            return half, self.ctx.costs.slip_min * mult
        lo = max(0, k - 16)
        closes = chunk.close[r, lo:k]
        finite = closes[np.isfinite(closes) & (closes > 0.0)]
        rv = float(np.std(np.diff(np.log(finite)), ddof=0)) if finite.size >= 3 else 0.0
        qv_slice = chunk.qv[r, max(0, k - 1) : k]
        qv = float(qv_slice[0]) if qv_slice.size else float("nan")
        if not _is_num(qv):
            qv = 0.0
        slip_value = self.ctx.costs.slip_min * mult + self.ctx.costs.impact_y * rv * math.sqrt(
            self.ctx.costs.notional_ref / (qv + 1e-12)
        )
        return half, slip_value

    def _mark_price(self, pos: Position, chunk: MinuteChunk, k: int) -> float:
        """持仓盯市价（缺失分钟沿用上一可得收盘价）。"""
        r = chunk.row.get(pos.symbol)
        if r is not None:
            price = float(chunk.close[r, k])
            if _is_num(price):
                pos.last_price = price
                return price
        return pos.last_price

    def equity(self, chunk: MinuteChunk, k: int) -> float:
        """盯市权益 = 已实现现金 + 各持仓未实现盈亏（11 §6）。"""
        total = self.cash
        for pos in self.positions.values():
            total += pos.qty * (self._mark_price(pos, chunk, k) - pos.entry_price)
        return total

    def used_margin(self) -> float:
        """已占用保证金合计。"""
        return sum(pos.margin for pos in self.positions.values())

    def available_margin(self) -> float:
        """可用保证金 = 现金权益 × 95% − 已占用保证金（06 §5.3）。"""
        return max(0.0, self.cash * 0.95 - self.used_margin())

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def _arm_stop(self, pos: Position, chunk: MinuteChunk, after_k: int) -> None:
        """预计算该持仓在当月块内第一次触发止损的分钟下标（不含入场分钟）。"""
        r = chunk.row.get(pos.symbol)
        if r is None:
            pos.next_stop_k = -1
            return
        start = after_k + 1
        if start >= chunk.low.shape[1]:
            pos.next_stop_k = -1
            return
        hits = np.nonzero(chunk.low[r, start:] <= pos.stop_price)[0]
        pos.next_stop_k = int(hits[0]) + start if hits.size else -1

    # ---- 时间推进 ----
    def start_month(self, chunk: MinuteChunk) -> None:
        """进入新的分钟块：为所有持仓重算止损触发下标。

        若某持仓在新月份没有任何分钟数据（该币已不在交易范围且未下载后续数据），
        按上一个可得价格平仓，原因记 `data_end`（11 §5.2）。
        """
        if self.positions:
            ts_ms = int(chunk.minute_ms[0])
            for pos in list(self.positions.values()):
                if pos.symbol in chunk.row:
                    continue
                price = pos.last_price
                if not _is_num(price):
                    self._skip("data_end_no_price")
                    continue
                rules = self.ctx.rules[pos.symbol]
                half = max(
                    0.5 * rules.tick_size / price,
                    self.ctx.costs.half_spread_min * self.scenario.cost_mult,
                )
                slip_value = self.ctx.costs.slip_min * self.scenario.cost_mult
                self._close(pos, price, price * (1.0 - half - slip_value), ts_ms, "data_end")
        for pos in self.positions.values():
            self._arm_stop(pos, chunk, -1)

    def end_month(self, chunk: MinuteChunk) -> None:
        """离开分钟块：保留最后 60 分钟成交额，供下月首根调仓的流动性上限使用。"""
        for symbol, r in chunk.row.items():
            self.qv_history[symbol] = chunk.qv[r, -60:].copy()

    def _qv_mean60(self, symbol: str, chunk: MinuteChunk, k: int) -> float:
        """最近 60 个已收盘分钟的成交额均值（跨月沿用上月末 60 分钟，11 §5.4）。"""
        r = chunk.row.get(symbol)
        if r is None:
            return float("nan")
        window = chunk.qv[r, max(0, k - 60) : k]
        prior = self.qv_history.get(symbol)
        if window.shape[0] < 60 and prior is not None and prior.size:
            need = 60 - window.shape[0]
            window = np.concatenate([prior[-need:], window])
        finite = window[np.isfinite(window)]
        return float(np.mean(finite)) if finite.size else float("nan")

    def step(self, ts_ms: int, chunk: MinuteChunk, k: int) -> None:
        """推进一分钟：止损 → 资金费 → 停机平仓 → 调仓/整点检查 → 风控。"""
        if self.positions:
            for pos in list(self.positions.values()):
                if pos.next_stop_k == k:
                    self._exit_stop(pos, chunk, k, ts_ms)
        self._settle_funding(ts_ms)
        if self.pending_flatten:
            self._flatten(ts_ms, chunk, k)
        if ts_ms % self.ctx.rebalance_ms == 0:
            self._rebalance(ts_ms, chunk, k)
        elif (
            self.ctx.cfg.exit_check_hours and ts_ms % (self.ctx.cfg.exit_check_hours * MS_HOUR) == 0
        ):
            self._exit_check(ts_ms, chunk, k)
        equity = self.equity(chunk, k)
        if risk_step(self.risk, ts_ms=ts_ms, equity=equity, limits=self.limits) == "flatten":
            self.pending_flatten = True
        if ts_ms % MS_DAY == MS_DAY - MS_MIN:
            self.daily.append((ts_ms, equity))

    def _settle_funding(self, ts_ms: int) -> None:
        """按资金费结算事件扣减（多头付正费率，11 §5.6）。"""
        for pos in self.positions.values():
            times = self.ctx.panel.series[pos.symbol].funding_times
            rates = self.ctx.panel.series[pos.symbol].funding_rates
            while pos.funding_idx < times.shape[0] and int(times[pos.funding_idx]) <= ts_ms:
                amount = pos.notional * float(rates[pos.funding_idx])
                self.cash -= amount
                pos.funding_paid += amount
                pos.funding_idx += 1

    # ---- 交易 ----
    def _open(
        self, symbol: str, ts_ms: int, chunk: MinuteChunk, k: int, decision: Decision
    ) -> bool:
        """按目标仓位开仓（fixed_risk + 每仓上限，11 §5.4）。"""
        ctx = self.ctx
        r = chunk.row.get(symbol)
        if r is None:
            self._skip("no_data")
            return False
        p1s = float(chunk.p1s[r, k])
        if not _is_num(p1s):
            self._skip("p1s_missing")
            return False
        atr = ctx.panel.atr_at(symbol, ts_ms)
        if not np.isfinite(atr) or atr <= 0.0:
            self._skip("atr_missing")
            return False
        half, slip_value = self._costs(symbol, chunk, k, p1s)
        entry_price = p1s * (1.0 + half + slip_value)
        stop_pct = min(ctx.cfg.stop_atr_mult * atr / entry_price, ctx.cfg.stop_max_pct)
        if not (stop_pct > 0.0):
            self._skip("stop_zero")
            return False
        fee_rate = ctx.costs.fee_taker * self.scenario.fee_mult
        rate = loss_rate(
            sl=stop_pct, slip_stop=2.0 * slip_value, half_spread=half, fee_taker=fee_rate
        )
        qv_mean = self._qv_mean60(symbol, chunk, k)
        if not _is_num(qv_mean):
            self._skip("qv_missing")
            return False
        rules = ctx.rules[symbol]
        result = calc_notional(
            base=self.cash,
            sl=stop_pct,
            loss_rate=rate,
            entry_price=entry_price,
            mean_qv_60=qv_mean,
            available_margin=self.available_margin(),
            step_size=rules.step_size,
            min_notional=rules.min_notional,
            cfg=ctx.sizing,
        )
        if not result.accepted:
            self._skip(result.status)
            return False
        notional = result.qty * entry_price
        if notional <= 0.0:
            self._skip("zero_notional")
            return False
        entry_fee = notional * fee_rate
        pos = Position(
            symbol=symbol,
            entry_ts_ms=ts_ms,
            entry_p1s=p1s,
            entry_price=entry_price,
            qty=result.qty,
            notional=notional,
            margin=notional / float(ctx.sizing.leverage),
            stop_price=entry_price * (1.0 - stop_pct),
            stop_pct=stop_pct,
            entry_fee=entry_fee,
            entry_rank=decision.rank.get(symbol, 0),
            entry_score=dict(decision.ranked).get(symbol, float("nan")),
            funding_idx=int(
                np.searchsorted(ctx.panel.series[symbol].funding_times, ts_ms, side="right")
            ),
        )
        pos.last_price = entry_price
        self.cash -= entry_fee
        self.positions[symbol] = pos
        self._arm_stop(pos, chunk, k)
        return True

    def _exit_market(
        self, symbol: str, chunk: MinuteChunk, k: int, ts_ms: int, reason: str
    ) -> bool:
        """按调仓/检查规则市价卖出（成交价 = p_1s × (1 − 半价差 − 滑点)）。"""
        pos = self.positions[symbol]
        r = chunk.row.get(symbol)
        if r is None:
            self._skip("exit_no_data")
            return False
        p1s = float(chunk.p1s[r, k])
        if not _is_num(p1s):
            self._skip("exit_p1s_missing")
            return False
        half, slip_value = self._costs(symbol, chunk, k, p1s)
        self._close(pos, p1s, p1s * (1.0 - half - slip_value), ts_ms, reason)
        return True

    def _exit_stop(self, pos: Position, chunk: MinuteChunk, k: int, ts_ms: int) -> None:
        """灾难止损：按止损价 × (1 − 2 × 滑点) 成交，跳空时用该分钟开盘价（11 §5.3）。"""
        r = chunk.row.get(pos.symbol)
        if r is None:
            return
        open_price = float(chunk.open[r, k])
        p1s = float(chunk.p1s[r, k])
        reference = p1s if _is_num(p1s) else pos.last_price
        _, slip_value = self._costs(pos.symbol, chunk, k, reference)
        trigger = pos.stop_price
        if _is_num(open_price) and open_price <= pos.stop_price:
            trigger = open_price
        self._close(pos, trigger, trigger * (1.0 - 2.0 * slip_value), ts_ms, "stop")
        self.cooldown[pos.symbol] = ts_ms + self.ctx.cfg.stop_cooldown_hours * MS_HOUR

    def _close(
        self, pos: Position, exit_p1s: float, exit_price: float, ts_ms: int, reason: str
    ) -> None:
        """平仓记账并登记到 trades（毛收益按 p_1s / 触发价计，净收益扣全部成本）。"""
        fee_rate = self.ctx.costs.fee_taker * self.scenario.fee_mult
        exit_fee = pos.qty * exit_price * fee_rate
        self.cash += pos.qty * (exit_price - pos.entry_price) - exit_fee
        net = pos.qty * (exit_price - pos.entry_price) - pos.entry_fee - exit_fee - pos.funding_paid
        self.trades.append(
            RotationTrade(
                symbol=pos.symbol,
                entry_ts_ms=pos.entry_ts_ms,
                exit_ts_ms=ts_ms,
                entry_p1s=pos.entry_p1s,
                exit_p1s=exit_p1s,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=pos.qty,
                notional=pos.notional,
                stop_pct=pos.stop_pct,
                entry_fee=pos.entry_fee,
                exit_fee=exit_fee,
                funding=pos.funding_paid,
                gross_pnl_p1s=pos.qty * (exit_p1s - pos.entry_p1s),
                net_pnl=net,
                exit_reason=reason,
                entry_rank=pos.entry_rank,
                entry_score=pos.entry_score,
            )
        )
        del self.positions[pos.symbol]
        register_exit(self.risk, ts_ms=ts_ms, net_pnl=net, hit_stop_type=reason == "stop")
        register_pause_if_needed(self.risk, ts_ms=ts_ms, limits=self.limits)

    # ---- 调仓 ----
    def _sell_reason(self, symbol: str, decision: Decision) -> str | None:
        """返回卖出原因；None 表示继续持有（11 §4.3）。"""
        if not decision.filter_on:
            return "filter_off"
        if symbol not in decision.universe:
            return "universe"
        rank = decision.rank.get(symbol)
        if rank is None or rank > self.ctx.buffer_k:
            return "rank_out"
        return None

    def _rebalance(self, ts_ms: int, chunk: MinuteChunk, k: int) -> None:
        """4h（或配置周期）调仓：先卖后买（11 §5.1、§5.2）。"""
        decision = self.schedule.get(ts_ms)
        if decision is None:
            return
        if not decision.filter_on:
            self.filter_off += 1
        for symbol in list(self.positions):
            reason = self._sell_reason(symbol, decision)
            if reason is not None:
                self._exit_market(symbol, chunk, k, ts_ms, reason)
        if not decision.filter_on:
            return
        if self.risk.halted(ts_ms) or self.risk.paused(ts_ms):
            return
        if self.risk.daily_stop(utc_day(ts_ms)):
            return
        cfg = self.ctx.cfg
        retained = len(self.positions)
        target = min(self.ctx.sizing.max_positions, max(cfg.top_k, retained))
        slots = target - retained
        if slots <= 0:
            return
        universe_month = self.ctx.calendar.get(month_key(ts_ms))
        unlock = universe_month.unlock_ms if universe_month is not None else {}
        for symbol, _score in decision.ranked:
            if slots <= 0:
                break
            if decision.rank[symbol] > cfg.top_k:
                break
            if symbol in self.positions:
                continue
            if unlock.get(symbol, 0) > ts_ms:
                self._skip("new_entry_delay")
                continue
            if self.cooldown.get(symbol, 0) > ts_ms:
                self._skip("cooldown")
                continue
            if self._open(symbol, ts_ms, chunk, k, decision):
                slots -= 1

    def _exit_check(self, ts_ms: int, chunk: MinuteChunk, k: int) -> None:
        """整点只做卖出检查（11 §4.4 可选开关）。"""
        decision = self.exit_schedule.get(ts_ms)
        if decision is None:
            return
        for symbol in list(self.positions):
            reason = self._sell_reason(symbol, decision)
            if reason is not None:
                self._exit_market(symbol, chunk, k, ts_ms, reason)

    def _flatten(self, ts_ms: int, chunk: MinuteChunk, k: int) -> None:
        """R6/R8 停机全平（11 §4.5）。"""
        for symbol in list(self.positions):
            if not self._exit_market(symbol, chunk, k, ts_ms, "halt"):
                return
        self.pending_flatten = False

    def final_equity(self) -> float:
        """期末权益：已实现现金 + 未平仓按最后盯市价（不计平仓成本，11 §6）。"""
        total = self.cash
        for pos in self.positions.values():
            if _is_num(pos.last_price):
                total += pos.qty * (pos.last_price - pos.entry_price)
        return total


def run_scenarios(ctx: SimContext, scenario_names: Sequence[str]) -> dict[str, SimResult]:
    """按月分块、逐分钟推进所有情景（共享同一份分钟数据）。

    Args:
        ctx: 运行上下文（数据、配置、决策表）。
        scenario_names: 需要运行的情景名（11 §7）。

    Returns:
        情景名 -> SimResult。
    """
    scenarios = [scenario_by_name(name) for name in scenario_names]
    portfolios = {scenario.name: Portfolio(scenario, ctx) for scenario in scenarios}
    for month in ctx.months:
        chunk = build_minute_chunk(
            data_root=ctx.data_root,
            month=month,
            symbols=ctx.chunk_symbols[month],
            aliases=ctx.aliases,
            holdout_start_ms=ctx.holdout_start_ms,
            allow_holdout=ctx.allow_holdout,
        )
        for portfolio in portfolios.values():
            portfolio.start_month(chunk)
        minutes = chunk.minute_ms
        first_ts = int(minutes[0])
        start = max(0, (ctx.eval_start_ms - first_ts) // MS_MIN)
        end = min(minutes.shape[0], (ctx.eval_end_ms - first_ts) // MS_MIN)
        for k in range(start, end):
            ts_ms = int(minutes[k])
            for portfolio in portfolios.values():
                portfolio.step(ts_ms, chunk, k)
        for portfolio in portfolios.values():
            portfolio.end_month(chunk)
    return {
        name: SimResult(
            scenario=name,
            trades=portfolio.trades,
            daily=portfolio.daily,
            final_equity=portfolio.final_equity(),
            skipped=portfolio.skipped,
            risk_events=list(portfolio.risk.events),
            filter_off_minutes=portfolio.filter_off,
        )
        for name, portfolio in portfolios.items()
    }
