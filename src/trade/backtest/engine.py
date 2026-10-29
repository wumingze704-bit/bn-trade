"""回测引擎：逐笔预计算 + 组合模拟（R0 驱动）。

规格：docs/06-回测引擎.md §3、§5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from trade.backtest.costs import FeeSchedule, Scenario, half_spread, scenario, slip
from trade.common.exits import ExitConfig, simulate_exit
from trade.common.sizing import SizingConfig, calc_notional, equity_floor_hit, loss_rate
from trade.features.kline import rolling_mean, rv_15, true_range, wilder_atr


class CostConfig(BaseModel):
    """成本模型参数（06 §4）。"""

    model_config = ConfigDict(extra="forbid")

    half_spread_min: float = Field(default=0.0001, gt=0.0)
    slip_min: float = Field(default=0.0001, gt=0.0)
    Y: float = Field(default=1.0, gt=0.0)


class RiskConfig(BaseModel):
    """回测风控参数（06 §3.1）。"""

    model_config = ConfigDict(extra="forbid")

    cooldown_bars: int = Field(default=15, ge=0)
    max_chase: float = Field(default=0.003, gt=0.0)


class SizingSweepConfig(BaseModel):
    """比例敏感性扫描（06 §5.5）。"""

    model_config = ConfigDict(extra="forbid")

    position_frac: list[float] = Field(default_factory=lambda: [0.1, 0.15, 0.2, 0.3])


class SanityConfig(BaseModel):
    """健全性检验参数（06 §7）。"""

    model_config = ConfigDict(extra="forbid")

    random_iters: int = Field(default=1000, ge=1)
    threshold_perturb: float = Field(default=0.04, gt=0.0)
    min_months_positive: int = Field(default=6, ge=1)
    min_folds_positive: int = Field(default=4, ge=1)


class BacktestConfig(BaseModel):
    """configs/backtest.yaml 的 pydantic 模型（06 §2）。"""

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    costs: CostConfig = Field(default_factory=CostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    scenarios: list[str] = Field(default_factory=lambda: ["base", "stress", "hell", "breakeven"])
    sizing_sweep: SizingSweepConfig = Field(default_factory=SizingSweepConfig)
    sanity: SanityConfig = Field(default_factory=SanityConfig)


@dataclass(frozen=True)
class TradeRule:
    """交易规则快照（exchangeInfo，09 §3）。"""

    tick_size: float
    step_size: float
    min_notional: float


@dataclass
class PrecomputedTrade:
    """候选信号的逐笔预计算结果（06 §3.4 第 1 步）。"""

    symbol: str
    group: str
    signal_open_time: int
    entry_open_time: int
    p: float
    m: float
    p_fill: float
    sl: float
    loss_rate: float
    mean_qv_60: float
    exit_open_time: int
    exit_price: float
    exit_type: str
    exit_type_code: int
    bars_held: int
    stop_updates: int
    mfe_r: float
    mae_r: float
    gross_return: float
    exit_fee_rate: float
    tick_size: float = 0.0
    step_size: float = 0.0
    min_notional: float = 0.0


@dataclass
class SkippedSignal:
    """被放弃的候选信号（06 §8 skipped.parquet）。"""

    symbol: str
    open_time: int
    p: float
    reason: str


@dataclass
class ExecutedTrade:
    """组合模拟最终接受的一笔交易。"""

    symbol: str
    group: str
    signal_open_time: int
    entry_open_time: int
    exit_open_time: int
    p: float
    m: float
    p_fill: float
    sl: float
    exit_price: float
    exit_type: str
    exit_type_code: int
    notional: float
    qty: float
    margin: float
    bars_held: int
    stop_updates: int
    mfe_r: float
    mae_r: float
    entry_fee: float
    exit_fee: float
    gross_pnl: float
    net_pnl: float
    capped_by: str | None


@dataclass
class PortfolioResult:
    """组合模拟结果。"""

    trades: list[ExecutedTrade] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    final_equity: float = 0.0
    equity_floor_hit: bool = False


def _entry_columns(latency_ms: int) -> tuple[str, str, str]:
    """按执行延迟选择入场参考列（06 §3.2、§4.3）。"""
    if latency_ms == 1000:
        return "p_1s", "hi_post_1s", "lo_post_1s"
    if latency_ms == 3000:
        return "p_3s", "hi_post_3s", "lo_post_3s"
    raise ValueError(f"不支持的执行延迟：{latency_ms}（仅 1000/3000）")


def _utc_day(ts_ms: int) -> str:
    """返回 UTC 日 "YYYY-MM-DD"（06 §3.1 条件 4）。"""
    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


def _symbol_arrays(bars: pd.DataFrame) -> dict[str, np.ndarray]:
    """预计算每币所需数组（ATR、已实现波动、滚动成交额）。"""
    close = bars["close"].to_numpy(dtype="float64")
    high = bars["high"].to_numpy(dtype="float64")
    low = bars["low"].to_numpy(dtype="float64")
    qv = bars["quote_volume"].to_numpy(dtype="float64")
    atr_abs = wilder_atr(true_range(high, low, close), 14)
    return {
        "atr_abs": atr_abs,
        "atr_pct": atr_abs / close,
        "rv15": rv_15(close),
        "mean_qv_60": rolling_mean(qv, 60),
    }


def precompute_trades(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    symbol: str,
    group: str,
    rule: TradeRule,
    scen: Scenario,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """逐笔预计算（06 §3.4 第 1 步）：为每个候选信号算 P_fill 与出场结果。

    只处理 is_sample=1 且 p ≥ θ 的候选；开仓过滤、追价保护在此判断，
    仓位与组合约束在 simulate_portfolio 中判断（06 §3.4 第 2 步）。
    """
    n = len(bars)
    if n == 0:
        return [], []
    p_col, hi_col, lo_col = _entry_columns(scen.latency_ms)
    arr = _symbol_arrays(bars)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    idx_of = {int(ts): i for i, ts in enumerate(open_time)}
    close = bars["close"].to_numpy(dtype="float64")
    qv = bars["quote_volume"].to_numpy(dtype="float64")
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
    p_entry_arr = bars[p_col].to_numpy(dtype="float64")
    hi_post = bars[hi_col].to_numpy(dtype="float64")
    lo_post = bars[lo_col].to_numpy(dtype="float64")
    high = bars["high"].to_numpy(dtype="float64")
    low = bars["low"].to_numpy(dtype="float64")
    open_ = bars["open"].to_numpy(dtype="float64")

    notional_ref = sizing_cfg.initial_equity * sizing_cfg.position_frac * float(sizing_cfg.leverage)
    half_spread_min = backtest_cfg.costs.half_spread_min * scen.half_spread_mult
    H_max = exit_cfg.H_max if exit_cfg.type != "fixed" else H_label

    trades: list[PrecomputedTrade] = []
    skipped: list[SkippedSignal] = []

    for row in signals.itertuples(index=False):
        if float(row.p) < backtest_cfg.threshold:
            continue
        t = idx_of.get(int(row.open_time))
        if t is None or t + 1 >= n:
            continue
        if is_filled[t + 1] == 1:
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), float(row.p), "entry_bar_filled")
            )
            continue
        p_raw = float(p_entry_arr[t + 1])
        if not np.isfinite(p_raw) or p_raw <= 0:
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), float(row.p), "no_entry_price")
            )
            continue
        c_t = float(close[t])
        if p_raw > c_t * (1.0 + backtest_cfg.risk.max_chase):
            skipped.append(SkippedSignal(symbol, int(row.open_time), float(row.p), "chase"))
            continue
        sigma = float(arr["atr_pct"][t])
        if not np.isfinite(sigma):
            skipped.append(SkippedSignal(symbol, int(row.open_time), float(row.p), "no_atr"))
            continue
        if not np.isfinite(arr["rv15"][t]) or not np.isfinite(arr["mean_qv_60"][t]):
            skipped.append(SkippedSignal(symbol, int(row.open_time), float(row.p), "no_lookback"))
            continue
        sl_ = float(np.clip(k_sl * sigma, sl_min, sl_max))
        atr_abs = float(arr["atr_abs"][t])
        tick_rel = rule.tick_size / c_t
        hs = half_spread(tick_rel, half_spread_min)
        if scen.fixed_slip is not None:
            slip_ = scen.fixed_slip
        else:
            slip_ = slip(
                slip_min=backtest_cfg.costs.slip_min * scen.slip_mult,
                Y=backtest_cfg.costs.Y,
                rv_15_raw=float(arr["rv15"][t]),
                notional=notional_ref,
                qv=float(qv[t]),
            )
        slip_stop = 2.0 * slip_
        p_fill = p_raw * (1.0 + hs + slip_)
        lr = loss_rate(sl=sl_, slip_stop=slip_stop, half_spread=hs, fee_taker=fees.taker)

        result = simulate_exit(
            open_,
            high,
            low,
            close,
            p_entry_arr,
            hi_post,
            lo_post,
            is_filled,
            t_entry=t + 1,
            p_fill=p_fill,
            sl=sl_,
            atr_abs=atr_abs,
            tick_size=rule.tick_size,
            H_max=H_max,
            cfg=exit_cfg,
            slip_stop=slip_stop,
            slip_exit=slip_,
            half_spread=hs,
            fee_taker=fees.taker,
            fee_maker=fees.maker,
        )
        if scen.all_taker or (result.exit_type_code == 3 and scen.tp_taker):
            exit_fee_rate = fees.taker
        else:
            exit_fee_rate = result.fee_rate

        trades.append(
            PrecomputedTrade(
                symbol=symbol,
                group=group,
                signal_open_time=int(row.open_time),
                entry_open_time=int(open_time[t + 1]),
                p=float(row.p),
                m=float(row.m),
                p_fill=p_fill,
                sl=sl_,
                loss_rate=lr,
                mean_qv_60=float(arr["mean_qv_60"][t]),
                exit_open_time=int(open_time[result.exit_idx]),
                exit_price=result.exit_price,
                exit_type=result.exit_type,
                exit_type_code=result.exit_type_code,
                bars_held=result.bars_held,
                stop_updates=result.stop_updates,
                mfe_r=result.mfe_r,
                mae_r=result.mae_r,
                gross_return=result.exit_price / p_fill - 1.0,
                exit_fee_rate=exit_fee_rate,
                tick_size=rule.tick_size,
                step_size=rule.step_size,
                min_notional=rule.min_notional,
            )
        )
    return trades, skipped


def simulate_portfolio(
    trades: list[PrecomputedTrade],
    precompute_skipped: list[SkippedSignal],
    *,
    sizing_cfg: SizingConfig,
    backtest_cfg: BacktestConfig,
    fees: FeeSchedule,
    initial_equity: float | None = None,
) -> PortfolioResult:
    """组合模拟（06 §3.4 第 2 步）：按时间推进，应用仓位与组合约束。

    组合约束：冷却期、组合持仓上限、候选排序（p 降序、m 降序）、当日亏损停止、
    权益下限、保证金与数量取整（06 §3.1、§5.3）。
    """
    equity = sizing_cfg.initial_equity if initial_equity is None else initial_equity
    peak = equity
    positions: dict[str, ExecutedTrade] = {}
    cooldown_until: dict[str, int] = {}
    daily_realized: dict[str, float] = {}
    equity_rows: list[tuple[int, float, float]] = []
    result = PortfolioResult(skipped=list(precompute_skipped))
    min_min_notional = min((t.min_notional for t in trades), default=1e18)

    ordered = sorted(trades, key=lambda tr: (tr.entry_open_time, -tr.p, -tr.m))

    for tr in ordered:
        due = sorted(
            ((s, t) for s, t in positions.items() if t.exit_open_time <= tr.entry_open_time),
            key=lambda kv: kv[1].exit_open_time,
        )
        for sym, trade in due:
            equity += trade.net_pnl
            peak = max(peak, equity)
            day = _utc_day(trade.exit_open_time)
            daily_realized[day] = daily_realized.get(day, 0.0) + trade.net_pnl
            if trade.exit_type_code in (0, 1, 2):
                cooldown_until[sym] = (
                    trade.exit_open_time + backtest_cfg.risk.cooldown_bars * 60_000
                )
            equity_rows.append((trade.exit_open_time, equity, (peak - equity) / peak))
            del positions[sym]

        if tr.symbol in positions:
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "already_in_position")
            )
            continue
        if tr.entry_open_time < cooldown_until.get(tr.symbol, 0):
            result.skipped.append(SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "cooldown"))
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
            result.skipped.append(SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, sz.status))
            continue
        net_pnl = sz.notional * (tr.gross_return - fees.taker - tr.exit_fee_rate)
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
            gross_pnl=sz.notional * tr.gross_return,
            net_pnl=net_pnl,
            capped_by=sz.capped_by,
        )
        positions[tr.symbol] = trade
        result.trades.append(trade)

    for _sym, trade in sorted(positions.items(), key=lambda kv: kv[1].exit_open_time):
        equity += trade.net_pnl
        peak = max(peak, equity)
        day = _utc_day(trade.exit_open_time)
        daily_realized[day] = daily_realized.get(day, 0.0) + trade.net_pnl
        equity_rows.append((trade.exit_open_time, equity, (peak - equity) / peak))
    positions.clear()

    result.final_equity = equity
    if equity_rows:
        result.equity_curve = pd.DataFrame(equity_rows, columns=["ts_ms", "equity", "drawdown"])
    else:
        result.equity_curve = pd.DataFrame(
            {
                "ts_ms": [0],
                "equity": [initial_equity if initial_equity is not None else equity],
                "drawdown": [0.0],
            }
        )
    return result


def run_backtest(
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
) -> tuple[PortfolioResult, list[PrecomputedTrade]]:
    """运行单个成本情景的回测（06 §3.4）：预计算 + 组合模拟。"""
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
            group=groups.get(symbol, "train"),
            rule=rules[symbol],
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
    result = simulate_portfolio(
        all_trades,
        all_skipped,
        sizing_cfg=sizing_cfg,
        backtest_cfg=backtest_cfg,
        fees=fees,
        initial_equity=initial_equity,
    )
    return result, all_trades
