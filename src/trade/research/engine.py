"""执行模拟与组合回测引擎（docs/13 §4、§6）。

- 决策在 t 时刻做出（只看到 `open_time + 1h ≤ t` 的 K 线），成交在 `open_time = t` 的 K 线开盘。
- 仓位规模用 t 时刻已知的权益（截至 `open_time = t − 1h` 的收盘盯市值）。
- 买入价 = 开盘价 × (1 + 半价差 + 滑点)，卖出价 = 开盘价 × (1 − 半价差 − 滑点)。
- 止损以入场时的标记价格为基准，用 `mark_1h` 的 low/high 触发，按成交价 K 线成交并加
  “`stop_slip_mult` 倍滑点 + 半价差”；某币某月缺标记价格时退回成交价并计数。
- 资金费按结算时刻从持仓名义价值中扣除（多头付正费率）；缺数据时按第 1 条规则估算并计数。
- 下架或断档超过 24 小时：按最后可用收盘价加 `stop_slip_mult` 倍滑点、再扣 1% 惩罚强平。
- 取不到价格一律抛异常（红线 12）。
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
from trade.research.data import DAY_MS, HOUR_MS, MarketData
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
# 资金费缺失判定与估算窗口（1 号 B 第 1 条：过去 7 天平均费率）
_FUNDING_FALLBACK_DAYS = 7
# 结算间隔的正常范围上限系数：超过 1.5 倍中位间隔视为数据缺失
_FUNDING_GAP_MULTIPLIER = 1.5


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


@dataclass(frozen=True)
class Segment:
    """连续路径上的一段：`[start_ms, end_ms)` 内使用给定策略（docs/13 §6 第 10 条 b 款）。"""

    start_ms: int
    end_ms: int
    strategy: Strategy
    label: str = ""


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
    attempted: int = 0
    dropped_min_notional: int = 0
    funding_estimated: int = 0
    funding_missing: int = 0
    mark_fallback: int = 0
    segments: list[str] = field(default_factory=list)
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
    """单参数 / 多段连续路径的组合回测引擎（研究、最终检验、影子盘共用同一口径）。"""

    def __init__(
        self,
        *,
        market: MarketData,
        strategy: Strategy | None = None,
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
        """单策略连续运行 `[start_ms, end_ms)`。"""
        if self.strategy is None:
            raise ValueError("Engine.run 需要 strategy；多段路径请用 run_path")
        return self.run_path(
            [Segment(start_ms=int(start_ms), end_ms=int(end_ms), strategy=self.strategy)], end_ms
        )

    def run_path(self, segments: list[Segment] | tuple[Segment, ...], end_ms: int) -> RunResult:
        """按段连续运行：权益、持仓与风控状态跨段延续，段起点状态为空（docs/13 §6）。

        Args:
            segments: 按时间升序、首尾相接的段列表；每段给出该段使用的策略。
            end_ms: 路径结束毫秒（不含）；最后一段的 `end_ms` 也按此截断。

        Returns:
            RunResult。

        Raises:
            ValueError: 段列表为空或首段起点早于 `end_ms`。
        """
        if not segments:
            raise ValueError("run_path 需要至少一个段（docs/13 §6）")
        ordered = sorted(segments, key=lambda item: item.start_ms)
        if ordered[0].start_ms >= end_ms:
            raise ValueError(f"路径起点 {ordered[0].start_ms} 不早于结束 {end_ms}")
        self.run_start_ms = int(ordered[0].start_ms)
        self._reset_state(float(self.sizing_cfg.initial_equity))
        index = 0
        labels: list[str] = []
        for ts_raw in np.arange(self.run_start_ms, end_ms, HOUR_MS, dtype=np.int64):
            ts = int(ts_raw)
            while index + 1 < len(ordered) and ts >= ordered[index].end_ms:
                index += 1
            self.strategy = ordered[index].strategy
            if not labels or labels[-1] != ordered[index].label:
                labels.append(ordered[index].label)
            # 1) 资金费（last_funding_ts < calc_time <= ts）
            self._apply_funding(ts)
            # 2) t 时刻已知权益：截至 open_time = t − 1h 的收盘盯市值
            equity_known = self._mark_at(ts - HOUR_MS)
            # 3) 风控 R5~R8
            equity_known = self._step_risk(ts, equity_known)
            # 4) 调仓：决策后在该 K 线（open_time = t）开盘成交
            self._maybe_decide_and_execute(ts, equity_known)
            # 5) 止损（标记价格触发）
            self._check_stops(ts)
            # 6) 下架 / 断档
            self._handle_gaps(ts)
            # 7) 收盘盯市并记录权益
            self.equity_ts.append(ts)
            self.equity.append(self._mark_at(ts))
            self.last_funding_ts = ts
        if self.positions:
            last_ts = int(self.equity_ts[-1])
            for position in list(self.positions.values()):
                self._close(position, last_ts, "end")
            self.equity[-1] = self._mark_at(last_ts)
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
            attempted=self.attempted,
            dropped_min_notional=self.dropped_min_notional,
            funding_estimated=self.funding_estimated,
            funding_missing=self.funding_missing,
            mark_fallback=self.mark_fallback,
            segments=labels,
        )

    def _reset_state(self, initial_equity: float) -> None:
        """把权益、持仓、风控与统计计数器重置为路径起点（空仓、初始权益、空风控）。"""
        self.cash = initial_equity
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.cooldown: dict[str, int] = {}
        self.equity_ts: list[int] = []
        self.equity: list[float] = []
        self.turnover = 0.0
        self.dropped = 0
        self.forced = 0
        self.attempted = 0
        self.dropped_min_notional = 0
        self.funding_estimated = 0
        self.funding_missing = 0
        self.mark_fallback = 0
        self.cost_total = 0.0
        self.funding_total = 0.0
        self.long_gross = 0.0
        self.short_gross = 0.0
        self.net_exposure: list[float] = []
        self.risk_events: list[str] = []
        self.rng = np.random.default_rng(self.seed)
        self.risk = RiskState(peak_equity=initial_equity)
        self.last_funding_ts = self.run_start_ms

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

    def _mark_price_at(self, symbol: str, ts: int) -> float | None:
        """标记价格收盘价；没有该币的标记价格数据返回 None（调用方计数）。"""
        mark = self.market.mark_bars(symbol)
        if mark is None:
            return None
        index = mark.bar_index(ts)
        if index is not None:
            return float(mark.close[index])
        last = mark.last_index_before(ts + 1)
        return float(mark.close[last]) if last >= 0 else None

    def _open(self, planned: PlannedPosition, ts: int, equity: float) -> None:
        """在 `open_time = ts` 的 K 线开盘成交，并按入场标记价格设止损价（docs/13 §4）。"""
        bars = self.market.bars(planned.symbol)
        index = bars.bar_index(ts)
        if index is None:
            self.dropped += 1
            return
        spread = self._half_spread(planned.symbol, ts)
        slip = self._slip(ts, planned.symbol, planned.notional)
        price = float(bars.open[index]) * (1.0 + planned.side * (spread + slip))
        rule = self.market.rule(planned.symbol)
        if price <= 0.0 or rule.step_size is None or rule.step_size <= 0.0:
            self.dropped += 1
            return
        qty = math.floor(planned.notional / price / rule.step_size + 1e-9) * rule.step_size
        notional = qty * price
        if qty <= 0.0 or notional < (rule.min_notional or 0.0) * 1.1:
            self.dropped += 1
            self.dropped_min_notional += 1
            return
        base_price = self._mark_price_at(planned.symbol, ts - HOUR_MS)
        if base_price is None or base_price <= 0.0:
            base_price = float(bars.close[index - 1]) if index > 0 else price
            self.mark_fallback += 1
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
            stop_price=base_price * (1.0 - planned.side * planned.sl),
            entry_ts=ts,
            entry_fee=fee,
            entry_equity=equity,
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
        """平仓并登记交易（docs/13 §4）。

        Raises:
            ValueError: 取不到平仓价格（红线 12：不得静默保留仓位）。
        """
        if explicit_price is not None:
            price = explicit_price
        else:
            bars = self.market.bars(position.symbol)
            index = bars.bar_index(ts)
            base = float(bars.open[index]) if use_open and index is not None else None
            if base is None:
                base = self._price_at(position.symbol, ts)
            if base is None or base <= 0.0:
                raise ValueError(
                    f"{ts} 平仓缺少 {position.symbol} 的价格（原因 {reason}，红线 12）"
                )
            spread = self._half_spread(position.symbol, ts)
            slip = self._slip(ts, position.symbol, position.notional)
            price = base * (1.0 - position.side * (spread + slip))
        if price <= 0.0:
            raise ValueError(f"{ts} 平仓价格非法：{position.symbol} price={price}（红线 12）")
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
        """结算 `last_funding_ts < calc_time <= ts` 的资金费（多头付正费率）。

        缺失处理（1 号 B 第 1 条）：没有资金费序列、或距上一次结算超过 1.5 倍中位结算间隔时，
        按该币过去 7 天平均每小时费率估算；该币过去 7 天也没有结算时按同期 U(R) 资金费中位数
        估算，两者分别计数；任何情况下都不把资金费当成 0。

        Raises:
            ValueError: 估算时 U(R) 也没有可用资金费、或取不到价格（红线 12）。
        """
        for position in self.positions.values():
            series = self.market.funding_series(position.symbol)
            if series is not None:
                times, rates = series.settlements_between(self.last_funding_ts, ts)
                if times.size:
                    price = self._settlement_price(position.symbol, ts)
                    for rate in rates:
                        payment = position.side * position.qty * price * float(rate)
                        self.cash -= payment
                        position.funding += payment
                        self.funding_total += payment
                    continue
            rate = self._missing_funding_rate(series, ts)
            if rate is None:
                continue
            price = self._settlement_price(position.symbol, ts)
            payment = position.side * position.qty * price * float(rate)
            self.cash -= payment
            position.funding += payment
            self.funding_total += payment

    def _settlement_price(self, symbol: str, ts: int) -> float:
        """资金费结算使用的价格（收盘价优先，缺失时用标记价格）。"""
        price = self._price_at(symbol, ts)
        if price is None or price <= 0.0:
            price = self._mark_price_at(symbol, ts)
        if price is None or price <= 0.0:
            raise ValueError(f"{ts} 结算资金费缺少 {symbol} 的价格（红线 12）")
        return price

    def _missing_funding_rate(self, series, ts: int) -> float | None:
        """缺失期间使用的每小时费率估计；正常无结算的小时返回 None（成本本来为 0）。"""
        lookback_ms = _FUNDING_FALLBACK_DAYS * DAY_MS
        if series is not None:
            last = series.last_calc_time(ts)
            normal_gap = (
                last is not None
                and ts - last <= series.typical_gap_hours() * HOUR_MS * _FUNDING_GAP_MULTIPLIER
            )
            if normal_gap:
                return None
            recent = series.mean_between(ts - lookback_ms, ts)
            if recent is not None:
                self.funding_estimated += 1
                return recent
        self.funding_missing += 1
        return self._universe_funding_median(ts)

    def _universe_funding_median(self, ts: int) -> float:
        """同期 U(R) 各币过去 7 天平均每小时费率的中位数（1 号 B 第 1 条）。"""
        view = View(self.market, ts)
        lookback_ms = _FUNDING_FALLBACK_DAYS * DAY_MS
        values: list[float] = []
        for symbol in sorted(view.universe_symbols()):
            series = self.market.funding_series(symbol)
            if series is None:
                continue
            rate = series.mean_between(ts - lookback_ms, ts)
            if rate is not None:
                values.append(float(rate))
        if not values:
            raise ValueError(f"{ts} 时期 U(R) 没有任何资金费记录，无法估算缺失资金费（红线 12）")
        return float(np.median(np.asarray(values, dtype="float64")))

    def _check_stops(self, ts: int) -> None:
        """用当前 1h 标记价格的 low/high 判断止损，按成交价 K 线成交（docs/13 §4）。

        成交价 = 止损价（开盘已越过止损价时用开盘价）加 `stop_slip_mult` 倍滑点再加半价差；
        该币该小时缺标记价格时退回成交价 K 线并计数。
        """
        for position in list(self.positions.values()):
            bars = self.market.bars(position.symbol)
            index = bars.bar_index(ts)
            if index is None:
                continue
            mark = self.market.mark_bars(position.symbol)
            mark_index = mark.bar_index(ts) if mark is not None else None
            if mark is not None and mark_index is not None:
                low = float(mark.low[mark_index])
                high = float(mark.high[mark_index])
            else:
                low = float(bars.low[index])
                high = float(bars.high[index])
                self.mark_fallback += 1
            open_price = float(bars.open[index])
            spread = self._half_spread(position.symbol, ts)
            slip = self._slip(ts, position.symbol, position.notional)
            cost = spread + slip * self.exec_cfg.stop_slip_mult
            if position.side > 0 and low <= position.stop_price:
                fill = min(open_price, position.stop_price)
                self._close(position, ts, "stop", explicit_price=fill * (1.0 - cost))
            elif position.side < 0 and high >= position.stop_price:
                fill = max(open_price, position.stop_price)
                self._close(position, ts, "stop", explicit_price=fill * (1.0 + cost))

    def _handle_gaps(self, ts: int) -> None:
        """下架或断档超过 24 小时：按最后收盘价加 `stop_slip_mult` 倍滑点与 1% 惩罚强平。"""
        for position in list(self.positions.values()):
            bars = self.market.bars(position.symbol)
            last = bars.last_index_before(ts + 1)
            if last < 0:
                raise ValueError(f"{ts} 持仓币 {position.symbol} 没有任何 K 线（红线 12）")
            if ts - int(bars.open_time[last]) <= _GAP_HOURS * HOUR_MS:
                continue
            price = float(bars.close[last])
            slip = self._slip(ts, position.symbol, position.notional) * self.exec_cfg.stop_slip_mult
            exit_price = price * (1.0 - position.side * (slip + self.exec_cfg.delist_penalty))
            self._close(position, ts, "delist", explicit_price=exit_price)
            self.forced += 1

    def _mark_at(self, ts: int) -> float:
        """盯市权益 = 现金 + 未实现盈亏；取不到价格时抛异常（红线 12）。"""
        unrealized = 0.0
        for position in self.positions.values():
            price = self._price_at(position.symbol, ts)
            if price is None or price <= 0.0:
                raise ValueError(f"{ts} 盯市缺少 {position.symbol} 的价格（红线 12）")
            unrealized += position.side * position.qty * (price - position.entry_price)
        return self.cash + unrealized

    def _step_risk(self, ts: int, equity: float) -> float:
        """推进 R5~R8（docs/13 §3、07 §7.1）；触发 R6/R8 时在该 K 线开盘全平。"""
        if self.options.no_halt:
            return equity
        action = risk_step(self.risk, ts_ms=ts, equity=equity, limits=self.risk_limits)
        self.risk_events = [
            f"{event.ts_ms}:{event.rule}:{event.action}" for event in self.risk.events
        ]
        if action == "flatten":
            for position in list(self.positions.values()):
                self._close(position, ts, "risk_flatten", use_open=True)
            return self.cash
        return equity

    def _maybe_decide_and_execute(self, ts: int, equity: float) -> None:
        """调仓时刻生成目标，并在同一 K 线（`open_time = ts`）开盘成交（docs/13 §4）。"""
        if not rebalance_due(ts, self.strategy.rebalance_hours):
            return
        if self.risk.halted(ts) or self.risk.paused(ts):
            return
        if not self.options.no_halt and self.risk.daily_stop(utc_day(ts)):
            return
        view = self._view(ts)
        if view.now_ms < self.run_start_ms:
            return
        held = {symbol: position.side for symbol, position in self.positions.items()}
        decision = self.strategy.decide(view, held)
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
            held=held,
        )
        self.dropped += stats.dropped
        self.attempted += stats.attempted
        self.dropped_min_notional += stats.dropped_min_notional
        keep = {position.symbol for position in planned} | stats.kept_symbols
        for symbol in [s for s in self.positions if s not in keep]:
            self._close(self.positions[symbol], ts, "rebalance", use_open=True)
        for item in planned:
            if item.symbol in self.positions:
                continue
            self._open(item, ts, equity)

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
