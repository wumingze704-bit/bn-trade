"""执行模拟与组合回测引擎（docs/13 §4、§6）。

- 决策在 K 线收盘（t）做出，成交在下一根 1h 开盘（t+1h）。
- 买入价 = open × (1 + 半价差 + 滑点)，卖出价 = open × (1 − 半价差 − 滑点)。
- 止损用 1h 的 low/high 判断，触发按止损价（开盘已越过按开盘价）加 2 倍滑点成交。
- 资金费按结算时刻从持仓名义价值中扣除（多头付正费率）。
- 下架或断档超过 24 小时：按最后可用收盘价加 2 倍滑点、再扣 1% 惩罚强平。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.sanity import deflated_sharpe
from trade.common.risk_rules import (
    RiskLimits,
    RiskState,
    register_exit,
    register_pause_if_needed,
    risk_step,
    utc_day,
)
from trade.common.sizing import SizingConfig
from trade.research.config import ExecutionConfig, PortfolioConfig
from trade.research.data import HOUR_MS, MarketData
from trade.research.interface import (
    Decision,
    Strategy,
    Target,
    rebalance_due,
)
from trade.research.portfolio import (
    PlannedPosition,
    half_spread_for,
    plan_positions,
    top_volume_symbols,
)
from trade.research.view import View

_BP = 1e-4
_GAP_HOURS = 24


@dataclass(frozen=True)
class ScenarioOptions:
    """情景参数（docs/13 §7）。"""

    name: str = "base"
    cost_mult: float = 1.0
    no_halt: bool = False
    delay_rebalances: int = 0
    shuffle: bool = False
    long_only: bool = False
    short_only: bool = False


@dataclass
class Trade:
    """一笔完整的持仓（开仓到平仓）。"""

    symbol: str
    side: int
    entry_ts: int
    exit_ts: int
    notional: float
    fees: float
    funding: float
    gross_pnl: float
    net_pnl: float
    reason: str
    entry_equity: float
    entry_price: float = 0.0


@dataclass
class Position:
    """当前持仓。"""

    symbol: str
    side: int
    qty: float
    entry_price: float
    notional: float
    sl: float
    stop_price: float
    entry_ts: int
    entry_fee: float
    entry_equity: float
    funding: float = 0.0


@dataclass
class PendingOrders:
    """上一根 K 线收盘产生、等待下一根开盘成交的订单。"""

    targets: list[PlannedPosition]
    exit_symbols: frozenset[str]
    execute_ts: int


@dataclass
class RunResult:
    """回测结果：交易、权益曲线与统计量。"""

    trades: list[Trade]
    equity_ts: np.ndarray
    equity: np.ndarray
    turnover: float
    dropped: int
    forced_liquidations: int
    cost_total: float
    funding_total: float
    long_gross: float
    short_gross: float
    net_exposure: list[float]
    risk_events: list[str]
    params: dict[str, float] = field(default_factory=dict)

    def daily_returns(self) -> pd.Series:
        """UTC 日收益（自然日补齐，空仓日收益 0，docs/13 §7）。"""
        series = pd.Series(self.equity, index=pd.to_datetime(self.equity_ts, unit="ms", utc=True))
        daily = series.resample("1D").last().dropna()
        if daily.size < 2:
            return pd.Series(dtype="float64")
        daily = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
        daily = daily.ffill()
        return daily.pct_change().dropna()

    def daily_sharpe(self) -> float:
        """日频非年化夏普（Deflated Sharpe 的输入单位）。"""
        returns = self.daily_returns()
        if returns.size < 2:
            return 0.0
        std = float(returns.std(ddof=1))
        return float(returns.mean() / std) if std > 0.0 else 0.0

    def deflated(self, trial_sharpes: list[float]) -> float:
        """Deflated Sharpe（docs/13 §7，日频非年化夏普同单位）。"""
        returns = self.daily_returns()
        if returns.size < 3 or not trial_sharpes:
            return 0.0
        return deflated_sharpe(
            sharpe=self.daily_sharpe(),
            trial_sharpes=trial_sharpes,
            skew=float(returns.skew()),
            kurtosis=float(returns.kurt()),
            n_obs=int(returns.size),
        )


class Engine:
    """单参数组合回测引擎（研究、最终检验、影子盘共用同一口径）。"""

    def __init__(
        self,
        *,
        market: MarketData,
        strategy: Strategy,
        sizing_cfg: SizingConfig,
        exec_cfg: ExecutionConfig,
        portfolio_cfg: PortfolioConfig,
        fee_taker: float,
        risk_limits: RiskLimits,
        options: ScenarioOptions | None = None,
        seed: int = 42,
    ) -> None:
        self.market = market
        self.strategy = strategy
        self.sizing_cfg = sizing_cfg
        self.exec_cfg = exec_cfg
        self.portfolio_cfg = portfolio_cfg
        self.fee_taker = fee_taker
        self.risk_limits = risk_limits
        self.options = options or ScenarioOptions()
        self.seed = seed

    def run(self, start_ms: int, end_ms: int) -> RunResult:
        """从 `start_ms`（含）跑到 `end_ms`（不含）。"""
        self.run_start_ms = int(start_ms)
        self.cash = float(self.sizing_cfg.initial_equity)
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.pending: PendingOrders | None = None
        self.cooldown: dict[str, int] = {}
        self.equity_ts: list[int] = []
        self.equity: list[float] = []
        self.turnover = 0.0
        self.dropped = 0
        self.forced = 0
        self.cost_total = 0.0
        self.funding_total = 0.0
        self.long_gross = 0.0
        self.short_gross = 0.0
        self.net_exposure: list[float] = []
        self.risk_events: list[str] = []
        self.rng = np.random.default_rng(self.seed)
        self.risk = RiskState(peak_equity=float(self.sizing_cfg.initial_equity))
        self.last_funding_ts = int(start_ms)
        for raw_ts in np.arange(start_ms, end_ms, HOUR_MS, dtype=np.int64):
            ts = int(raw_ts)
            if self.pending is not None and self.pending.execute_ts == ts:
                self._execute_pending(ts)
            elif self.pending is not None and self.pending.execute_ts < ts:
                self.pending = None
            self._apply_funding(ts)
            self._check_stops(ts)
            self._handle_gaps(ts)
            equity = self._mark(ts)
            equity = self._step_risk(ts, equity)
            self.equity_ts.append(ts)
            self.equity.append(equity)
            self._maybe_decide(ts, equity)
            self.last_funding_ts = ts
        if self.positions:
            last_ts = int(self.equity_ts[-1])
            for position in list(self.positions.values()):
                self._close(position, last_ts, "end")
            self.equity[-1] = self._mark(last_ts)
        return RunResult(
            trades=self.trades,
            equity_ts=np.asarray(self.equity_ts, dtype="int64"),
            equity=np.asarray(self.equity, dtype="float64"),
            turnover=self.turnover,
            dropped=self.dropped,
            forced_liquidations=self.forced,
            cost_total=self.cost_total,
            funding_total=self.funding_total,
            long_gross=self.long_gross,
            short_gross=self.short_gross,
            net_exposure=self.net_exposure,
            risk_events=self.risk_events,
        )

    def _view(self, ts: int) -> View:
        lag = self.options.delay_rebalances * self.strategy.rebalance_hours * HOUR_MS
        return View(self.market, ts - lag)

    def _slip(self, ts: int, symbol: str, notional: float) -> float:
        """平方根冲击模型（用 1h 波动与成交额，docs/13 §4）。"""
        view = View(self.market, ts)
        vol = view.hourly_vol(symbol, self.exec_cfg.impact_vol_hours) or 0.0
        volume = view.mean_quote_volume(symbol, self.exec_cfg.impact_vol_hours) or 0.0
        base = self.exec_cfg.slip_min_bp * _BP
        if volume <= 0.0:
            return base * self.options.cost_mult
        impact = self.exec_cfg.impact_y * vol * math.sqrt(notional / volume)
        return max(base, impact) * self.options.cost_mult

    def _price_at(self, symbol: str, ts: int) -> float | None:
        bars = self.market.bars(symbol)
        index = bars.bar_index(ts)
        if index is not None:
            return float(bars.close[index])
        last = bars.last_index_before(ts + 1)
        return float(bars.close[last]) if last >= 0 else None

    def _execute_pending(self, ts: int) -> None:
        pending = self.pending
        self.pending = None
        if pending is None:
            return
        for symbol in pending.exit_symbols:
            position = self.positions.get(symbol)
            if position is not None:
                self._close(position, ts, "rebalance", use_open=True)
        for planned in pending.targets:
            if planned.symbol in self.positions:
                continue
            bars = self.market.bars(planned.symbol)
            index = bars.bar_index(ts)
            if index is None:
                self.dropped += 1
                continue
            spread = self._half_spread(planned.symbol, ts)
            slip = self._slip(ts, planned.symbol, planned.notional)
            price = float(bars.open[index]) * (1.0 + planned.side * (spread + slip))
            rule = self.market.rule(planned.symbol)
            if price <= 0.0 or rule.step_size is None or rule.step_size <= 0.0:
                self.dropped += 1
                continue
            qty = math.floor(planned.notional / price / rule.step_size + 1e-9) * rule.step_size
            notional = qty * price
            if qty <= 0.0 or notional < (rule.min_notional or 0.0) * 1.1:
                self.dropped += 1
                continue
            fee = notional * self.fee_taker * self.options.cost_mult
            self.cash -= fee
            self.cost_total += fee
            self.turnover += notional
            self.positions[planned.symbol] = Position(
                symbol=planned.symbol,
                side=planned.side,
                qty=qty,
                entry_price=price,
                notional=notional,
                sl=planned.sl,
                stop_price=price * (1.0 - planned.side * planned.sl),
                entry_ts=ts,
                entry_fee=fee,
                entry_equity=self._mark(ts),
            )

    def _half_spread(self, symbol: str, ts: int) -> float:
        view = View(self.market, ts)
        top = top_volume_symbols(view, view.universe_symbols(), self.exec_cfg.half_spread_top_n)
        return half_spread_for(view, symbol, self.exec_cfg, top)

    def _close(
        self,
        position: Position,
        ts: int,
        reason: str,
        *,
        use_open: bool = False,
        explicit_price: float | None = None,
    ) -> None:
        """平仓并登记交易（docs/13 §4）。"""
        if explicit_price is not None:
            price = explicit_price
        else:
            bars = self.market.bars(position.symbol)
            index = bars.bar_index(ts)
            base = float(bars.open[index]) if use_open and index is not None else None
            if base is None:
                base = self._price_at(position.symbol, ts)
            if base is None:
                return
            spread = self._half_spread(position.symbol, ts)
            slip = self._slip(ts, position.symbol, position.notional)
            price = base * (1.0 - position.side * (spread + slip))
        if price <= 0.0:
            return
        gross = position.side * position.qty * (price - position.entry_price)
        exit_fee = position.qty * price * self.fee_taker * self.options.cost_mult
        self.cash += gross - exit_fee
        self.cost_total += exit_fee
        self.turnover += position.qty * price
        net = gross - exit_fee - position.entry_fee - position.funding
        self.trades.append(
            Trade(
                symbol=position.symbol,
                side=position.side,
                entry_ts=position.entry_ts,
                exit_ts=ts,
                notional=position.notional,
                fees=position.entry_fee + exit_fee,
                funding=position.funding,
                gross_pnl=gross,
                net_pnl=net,
                reason=reason,
                entry_equity=position.entry_equity,
                entry_price=position.entry_price,
            )
        )
        if position.side > 0:
            self.long_gross += gross
        else:
            self.short_gross += gross
        register_exit(self.risk, ts_ms=ts, net_pnl=net, hit_stop_type=reason in {"stop", "delist"})
        register_pause_if_needed(self.risk, ts_ms=ts, limits=self.risk_limits)
        if reason == "stop":
            self.cooldown[position.symbol] = ts + self.exec_cfg.cooldown_hours * HOUR_MS
        del self.positions[position.symbol]

    def _apply_funding(self, ts: int) -> None:
        """结算 `last_funding_ts < calc_time <= ts` 的资金费（多头付正费率）。"""
        for position in self.positions.values():
            series = self.market.funding_series(position.symbol)
            if series is None:
                continue
            times, rates = series.settlements_between(self.last_funding_ts, ts)
            if times.size == 0:
                continue
            price = self._price_at(position.symbol, ts) or position.entry_price
            for rate in rates:
                payment = position.side * position.qty * price * float(rate)
                self.cash -= payment
                position.funding += payment
                self.funding_total += payment

    def _check_stops(self, ts: int) -> None:
        """用当前 1h 的 low/high 判断止损（触发按止损价加 2 倍滑点）。"""
        stop_slip = 2.0 * self.exec_cfg.slip_min_bp * _BP * self.options.cost_mult
        for position in list(self.positions.values()):
            bars = self.market.bars(position.symbol)
            index = bars.bar_index(ts)
            if index is None:
                continue
            open_price = float(bars.open[index])
            if position.side > 0 and float(bars.low[index]) <= position.stop_price:
                fill = min(open_price, position.stop_price)
                self._close(position, ts, "stop", explicit_price=fill * (1.0 - stop_slip))
            elif position.side < 0 and float(bars.high[index]) >= position.stop_price:
                fill = max(open_price, position.stop_price)
                self._close(position, ts, "stop", explicit_price=fill * (1.0 + stop_slip))

    def _handle_gaps(self, ts: int) -> None:
        """下架或断档超过 24 小时：按最后收盘价加 2 倍滑点与 1% 惩罚强平。"""
        stop_slip = 2.0 * self.exec_cfg.slip_min_bp * _BP * self.options.cost_mult
        for position in list(self.positions.values()):
            bars = self.market.bars(position.symbol)
            last = bars.last_index_before(ts + 1)
            if last < 0:
                continue
            if ts - int(bars.open_time[last]) <= _GAP_HOURS * HOUR_MS:
                continue
            price = float(bars.close[last])
            exit_price = price * (1.0 - position.side * (stop_slip + self.exec_cfg.delist_penalty))
            self._close(position, ts, "delist", explicit_price=exit_price)
            self.forced += 1

    def _mark(self, ts: int) -> float:
        """盯市权益 = 现金 + 未实现盈亏。"""
        unrealized = 0.0
        for position in self.positions.values():
            price = self._price_at(position.symbol, ts)
            if price is None:
                continue
            unrealized += position.side * position.qty * (price - position.entry_price)
        return self.cash + unrealized

    def _step_risk(self, ts: int, equity: float) -> float:
        """推进 R5~R8（docs/13 §3、07 §7.1）。"""
        if self.options.no_halt:
            return equity
        action = risk_step(self.risk, ts_ms=ts, equity=equity, limits=self.risk_limits)
        self.risk_events = [
            f"{event.ts_ms}:{event.rule}:{event.action}" for event in self.risk.events
        ]
        if action == "flatten":
            self.pending = None
            for position in list(self.positions.values()):
                self._close(position, ts, "risk_flatten")
            return self._mark(ts)
        return equity

    def _maybe_decide(self, ts: int, equity: float) -> None:
        """调仓时刻生成目标，下一根 K 线开盘成交。"""
        if not rebalance_due(ts, self.strategy.rebalance_hours):
            return
        if self.risk.halted(ts) or self.risk.paused(ts):
            return
        if not self.options.no_halt and self.risk.daily_stop(utc_day(ts)):
            return
        view = self._view(ts)
        if view.now_ms < self.run_start_ms:
            return
        decision = self.strategy.decide(
            view, {symbol: position.side for symbol, position in self.positions.items()}
        )
        targets: list[Target] = list(decision.targets)
        if self.options.long_only:
            targets = [t for t in targets if t.side > 0]
        if self.options.short_only:
            targets = [t for t in targets if t.side < 0]
        if self.options.shuffle:
            targets = self._shuffle_targets(targets, view)
        blocked = {symbol for symbol, until in self.cooldown.items() if until > ts}
        used_margin = sum(p.notional for p in self.positions.values()) / float(
            self.sizing_cfg.leverage
        )
        available = max(0.0, equity * self.portfolio_cfg.margin_cap - used_margin)
        planned, stats = plan_positions(
            Decision(tuple(targets), decision.exposure),
            view=view,
            equity=equity,
            available_margin=available,
            sizing_cfg=self.sizing_cfg,
            exec_cfg=self.exec_cfg,
            portfolio_cfg=self.portfolio_cfg,
            fee_taker=self.fee_taker,
            blocked=blocked,
        )
        self.dropped += stats.dropped
        keep = {position.symbol for position in planned}
        exits = frozenset(symbol for symbol in self.positions if symbol not in keep)
        self.pending = PendingOrders(
            targets=list(planned), exit_symbols=exits, execute_ts=ts + HOUR_MS
        )

    def _shuffle_targets(self, targets: list[Target], view: View) -> list[Target]:
        """shuffle 情景：保持多空与止损，随机替换标的（docs/13 §7）。"""
        pool = sorted(view.universe_symbols() & set(self.market.hourly))
        if not pool:
            return targets
        longs = [t for t in targets if t.side > 0]
        shorts = [t for t in targets if t.side < 0]
        chosen = [str(symbol) for symbol in self.rng.permutation(pool)]
        result: list[Target] = []
        for target, symbol in zip(longs, chosen[: len(longs)], strict=False):
            result.append(Target(symbol=symbol, side=1, sl=target.sl, weight=target.weight))
        for target, symbol in zip(shorts, chosen[len(longs) :], strict=False):
            result.append(Target(symbol=symbol, side=-1, sl=target.sl, weight=target.weight))
        return result
