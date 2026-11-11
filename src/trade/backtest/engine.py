"""回测引擎：逐笔预计算 + 组合模拟（R0 驱动）。

规格：docs/06-回测引擎.md §3、§5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from trade.backtest.costs import FeeSchedule, Scenario, scenario
from trade.common.entry import compute_entry
from trade.common.exits import ExitConfig, simulate_exit
from trade.common.funding import funding_rate_between, funding_settlements
from trade.common.sizing import SizingConfig, calc_notional, equity_floor_hit
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
    random_seed: int = Field(default=42, ge=0)
    threshold_perturb: float = Field(default=0.04, gt=0.0)
    min_months_positive: int = Field(default=6, ge=1)
    min_folds_positive: int = Field(default=4, ge=1)
    bootstrap_iters: int = Field(default=10000, ge=1)
    bootstrap_seed: int = Field(default=42, ge=0)


class ReferenceConfig(BaseModel):
    """reference 运行配置（06 §5.1，用于 G2/G3 闸门判定）。"""

    model_config = ConfigDict(extra="forbid")

    initial_equity: float = Field(default=10000.0, gt=0.0)
    mode: str = Field(default="fixed_risk", pattern=r"^(fixed_fraction|fixed_risk)$")
    risk_per_trade: float = Field(default=0.005, ge=0.001, le=0.05)
    base: str = Field(default="initial", pattern=r"^(equity|initial)$")


class BacktestConfig(BaseModel):
    """configs/backtest.yaml 的 pydantic 模型（06 §2）。"""

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    costs: CostConfig = Field(default_factory=CostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    scenarios: list[str] = Field(default_factory=lambda: ["base", "stress", "hell", "breakeven"])
    sizing_sweep: SizingSweepConfig = Field(default_factory=SizingSweepConfig)
    sanity: SanityConfig = Field(default_factory=SanityConfig)
    reference: ReferenceConfig = Field(default_factory=ReferenceConfig)


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
    funding_rate_sum: float = 0.0
    tick_size: float = 0.0
    step_size: float = 0.0
    min_notional: float = 0.0
    new_entry: bool = False
    # 以下字段供 S8 逐笔重放复用同一套入场成本与出场参数（06 §7 S8），不进报告 schema。
    atr_abs: float = 0.0
    slip_stop: float = 0.0
    slip_exit: float = 0.0
    half_spread: float = 0.0
    notional_ref: float = 0.0
    h_max: int = 0


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
    funding_fee: float = 0.0
    new_entry: bool = False


@dataclass
class PortfolioResult:
    """组合模拟结果。"""

    trades: list[ExecutedTrade] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    final_equity: float = 0.0
    equity_floor_hit: bool = False
    max_drawdown_halt: bool = False


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


@dataclass(frozen=True)
class CloseSeries:
    """单币 close 序列（open_time 升序 int64 + close float64），供逐分钟盯市（06 §3.4）。"""

    open_time: np.ndarray
    close: np.ndarray


def _close_arrays(bars_by_symbol: dict[str, pd.DataFrame] | None) -> dict[str, CloseSeries]:
    """取每币的 open_time 与 close 两列（不复制整张 bars），供逐分钟盯市。

    规格：docs/06-回测引擎.md §3.4、§5.1

    Args:
        bars_by_symbol: 币种 → 分钟 bars（缺 `open_time` 或 `close` 列的币跳过）。

    Returns:
        币种 → CloseSeries；`open_time` 升序（bars 未按时间排序时按时间重排两列）。
    """
    if not bars_by_symbol:
        return {}
    arrays: dict[str, CloseSeries] = {}
    for symbol, bars in bars_by_symbol.items():
        if "open_time" not in bars.columns or "close" not in bars.columns:
            continue
        open_time = bars["open_time"].to_numpy(dtype="int64")
        close = bars["close"].to_numpy(dtype="float64")
        if open_time.shape[0] > 1 and not bool(np.all(np.diff(open_time) > 0)):
            order = np.argsort(open_time, kind="stable")
            open_time = open_time[order]
            close = close[order]
        arrays[symbol] = CloseSeries(open_time=open_time, close=close)
    return arrays


def _mark_to_market_curve(
    trades: list[ExecutedTrade],
    close_arrays: dict[str, CloseSeries],
    initial_equity: float,
) -> pd.DataFrame:
    """按分钟盯市的权益曲线（06 §3.4、§5.1）。

    权益 = 钱包余额（初始 + 已实现净盈亏） + 未实现盈亏；在每根持仓 K 线收盘处采样。
    采样时刻 = 各笔交易持仓区间 `[entry_open_time, exit_open_time)` 内该币的 K 线时间戳
    ∪ 各笔离场时间。未实现盈亏只在该交易所在币有该分钟 K 线时计入，缺失分钟贡献 0
    （等价于按 `p_fill` 估值）。

    Args:
        trades: 组合模拟接受的交易。
        close_arrays: `_close_arrays` 的返回值（每币 close 数组）。
        initial_equity: 初始权益，USDT。

    Returns:
        DataFrame，列 = [ts_ms（int64）, equity（float64）, drawdown（float64）]；首行为
        (0, initial_equity, 0.0)，无交易时只返回该行。
    """
    if not trades:
        return pd.DataFrame({"ts_ms": [0], "equity": [initial_equity], "drawdown": [0.0]})

    exit_times = np.fromiter(
        (trade.exit_open_time for trade in trades), dtype="int64", count=len(trades)
    )
    net_pnl = np.fromiter((trade.net_pnl for trade in trades), dtype="float64", count=len(trades))

    # 每笔交易在其所在币 K 线上的取样区间（左闭右开），一次算好供采样与盯市复用
    slices: list[tuple[int, int, int]] = []
    pieces: list[np.ndarray] = [exit_times]
    for index, trade in enumerate(trades):
        series = close_arrays.get(trade.symbol)
        if series is None:
            slices.append((index, 0, 0))
            continue
        lo = int(np.searchsorted(series.open_time, trade.entry_open_time, side="left"))
        hi = int(np.searchsorted(series.open_time, trade.exit_open_time, side="left"))
        slices.append((index, lo, hi))
        if hi > lo:
            pieces.append(series.open_time[lo:hi])
    sample_ts = np.unique(np.concatenate(pieces))

    # 已实现：exit_open_time ≤ ts 的全部 net_pnl 之和（按离场时间稳定排序后 cumsum）
    exit_order = np.argsort(exit_times, kind="stable")
    sorted_exit = exit_times[exit_order]
    cumulative = np.cumsum(net_pnl[exit_order])
    realized_pos = np.searchsorted(sorted_exit, sample_ts, side="right")
    realized = np.where(realized_pos > 0, cumulative[np.maximum(realized_pos - 1, 0)], 0.0)

    # 未实现：每笔交易只在自己币有 K 线的采样时刻贡献 notional × (close / p_fill − 1)
    unrealized = np.zeros(sample_ts.shape[0], dtype="float64")
    for index, lo, hi in slices:
        if hi <= lo:
            continue
        trade = trades[index]
        series = close_arrays[trade.symbol]
        close_slice = series.close[lo:hi]
        # close ≤ 0 的缺失分钟按 p_fill 估值（贡献 0），与逐元素实现一致
        price = np.where(close_slice > 0.0, close_slice, trade.p_fill)
        positions = np.searchsorted(sample_ts, series.open_time[lo:hi])
        np.add.at(unrealized, positions, trade.notional * (price / trade.p_fill - 1.0))

    equity = initial_equity + realized + unrealized
    peak = np.maximum.accumulate(np.concatenate(([initial_equity], equity)))[1:]
    drawdown = np.where(peak > 0.0, (peak - equity) / peak, 0.0)
    frame = pd.DataFrame({"ts_ms": sample_ts, "equity": equity, "drawdown": drawdown})
    if int(frame["ts_ms"].iloc[0]) != 0:
        head = pd.DataFrame({"ts_ms": [0], "equity": [initial_equity], "drawdown": [0.0]})
        frame = pd.concat([head, frame], ignore_index=True)
    return frame


def reference_trade_net_pnl(
    tr: PrecomputedTrade,
    *,
    sizing_cfg: SizingConfig,
    fees: FeeSchedule,
) -> float:
    """reference 仓位（base=initial、无组合约束）下单笔净盈亏（06 §7 S1）。"""
    base = sizing_cfg.initial_equity
    sz = calc_notional(
        base=base,
        sl=tr.sl,
        loss_rate=tr.loss_rate,
        entry_price=tr.p_fill,
        mean_qv_60=tr.mean_qv_60,
        available_margin=base,
        step_size=tr.step_size,
        min_notional=tr.min_notional,
        cfg=sizing_cfg,
    )
    if not sz.accepted:
        return 0.0
    return sz.notional * (tr.gross_return - fees.taker - tr.exit_fee_rate - tr.funding_rate_sum)


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
    trade_mask: np.ndarray | None = None,
    new_entry: bool = False,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """逐笔预计算（06 §3.4 第 1 步）：为每个候选信号算 P_fill 与出场结果。

    只处理 is_sample=1 且 p ≥ θ 的候选；开仓过滤、追价保护在此判断，
    仓位与组合约束在 simulate_portfolio 中判断（06 §3.4 第 2 步）。

    Args:
        trade_mask: 可选，与 bars 等长的 bool 数组，标记该分钟是否在当月交易范围
            `U(R)` 内（06 §3.1 条件 8）。为 None 时视为全部可交易（无宇宙约束）。
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
    fund_times, fund_rates = funding_settlements(bars)

    for row in signals.itertuples(index=False):
        if float(row.p) < backtest_cfg.threshold:
            continue
        t = idx_of.get(int(row.open_time))
        if t is None or t + 1 >= n:
            continue
        if trade_mask is not None and not bool(trade_mask[t]):
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), float(row.p), "not_in_universe")
            )
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
        entry = compute_entry(
            c_t=c_t,
            p_raw=p_raw,
            sigma=sigma,
            atr_abs=float(arr["atr_abs"][t]),
            rv15=float(arr["rv15"][t]),
            qv=float(qv[t]),
            tick_size=rule.tick_size,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            notional_ref=notional_ref,
            half_spread_min=half_spread_min,
            slip_min=backtest_cfg.costs.slip_min * scen.slip_mult,
            Y=backtest_cfg.costs.Y,
            fixed_slip=scen.fixed_slip,
            fee_taker=fees.taker,
        )
        sl_ = entry.sl
        atr_abs = entry.atr_abs
        hs = entry.half_spread
        slip_ = entry.slip
        slip_stop = entry.slip_stop
        p_fill = entry.p_fill
        lr = entry.loss_rate

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
        exit_price = float(result.exit_price)
        if not np.isfinite(exit_price) or exit_price <= 0.0:
            # 出场分钟缺 aggTrades 时 p_1s 为空、出场价无法确定（06 §3.3 缺数据
            # 分钟不判定）：按该根 K 线收盘价出场（同样扣点差与离场滑点），
            # 避免净盈亏变成 NaN。
            exit_price = float(close[result.exit_idx]) * (1.0 - hs - slip_)
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
                exit_price=exit_price,
                exit_type=result.exit_type,
                exit_type_code=result.exit_type_code,
                bars_held=result.bars_held,
                stop_updates=result.stop_updates,
                mfe_r=result.mfe_r,
                mae_r=result.mae_r,
                gross_return=exit_price / p_fill - 1.0,
                exit_fee_rate=exit_fee_rate,
                funding_rate_sum=funding_rate_between(
                    fund_times,
                    fund_rates,
                    int(open_time[t + 1]),
                    int(open_time[result.exit_idx]),
                ),
                tick_size=rule.tick_size,
                step_size=rule.step_size,
                min_notional=rule.min_notional,
                new_entry=new_entry,
                atr_abs=atr_abs,
                slip_stop=slip_stop,
                slip_exit=slip_,
                half_spread=hs,
                notional_ref=notional_ref,
                h_max=H_max,
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
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> PortfolioResult:
    """组合模拟（06 §3.4 第 2 步）：按时间推进，应用仓位与组合约束。

    组合约束：冷却期、组合持仓上限、候选排序（p 降序、m 降序）、当日亏损停止、
    权益下限、保证金与数量取整（06 §3.1、§5.3）。权益曲线按盯市权益（钱包余额
    + 未实现盈亏）逐分钟生成，回撤与回撤停机基于该曲线（06 §5.1、§3.4）。
    """
    start_equity = sizing_cfg.initial_equity if initial_equity is None else initial_equity
    equity = start_equity
    positions: dict[str, ExecutedTrade] = {}
    cooldown_until: dict[str, int] = {}
    daily_realized: dict[str, float] = {}
    result = PortfolioResult(skipped=list(precompute_skipped))
    min_min_notional = min((t.min_notional for t in trades), default=1e18)
    close_arrays = _close_arrays(bars_by_symbol)

    ordered = sorted(trades, key=lambda tr: (tr.entry_open_time, -tr.p, -tr.m))

    for tr in ordered:
        due = sorted(
            ((s, t) for s, t in positions.items() if t.exit_open_time <= tr.entry_open_time),
            key=lambda kv: kv[1].exit_open_time,
        )
        for sym, trade in due:
            equity += trade.net_pnl
            day = _utc_day(trade.exit_open_time)
            daily_realized[day] = daily_realized.get(day, 0.0) + trade.net_pnl
            if trade.exit_type_code in (0, 1, 2):
                cooldown_until[sym] = (
                    trade.exit_open_time + backtest_cfg.risk.cooldown_bars * 60_000
                )
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

    for _sym, trade in sorted(positions.items(), key=lambda kv: kv[1].exit_open_time):
        equity += trade.net_pnl
        day = _utc_day(trade.exit_open_time)
        daily_realized[day] = daily_realized.get(day, 0.0) + trade.net_pnl
    positions.clear()

    result.final_equity = equity
    result.equity_curve = _mark_to_market_curve(result.trades, close_arrays, start_equity)
    if not result.equity_curve.empty:
        max_dd = float(result.equity_curve["drawdown"].max())
        if max_dd >= sizing_cfg.max_drawdown_halt:
            result.max_drawdown_halt = True
    return result


def precompute_trades_all(
    bars_by_symbol: dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    *,
    scen: Scenario,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    trade_masks: dict[str, np.ndarray] | None = None,
    new_entry_symbols: set[str] | None = None,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """为全部信号做逐笔预计算（06 §3.4 第 1 步）。

    阈值 θ 只影响「p ≥ θ 的候选过滤」，不影响逐笔出场模拟（`simulate_exit` 与 θ
    无关）。把预计算独立出来，便于阈值网格/敏感性扫描只预计算一次，再按 θ 过滤后
    反复跑组合层（06 §7 S4），避免对同一批信号重复模拟出场。
    """
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
    return all_trades, all_skipped


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
    trade_masks: dict[str, np.ndarray] | None = None,
    new_entry_symbols: set[str] | None = None,
    scenario_override: Scenario | None = None,
) -> tuple[PortfolioResult, list[PrecomputedTrade]]:
    """运行单个成本情景的回测（06 §3.4）：预计算 + 组合模拟。

    Args:
        trade_masks: 可选，alias -> 该币每分钟是否在当月交易范围的 bool 数组（06 §3.1
            条件 8）。缺省 None 表示无宇宙约束（测试/旧调用兼容）。
        new_entry_symbols: 可选，新进入组 alias 集合（03 §4.5、06 §7 S10）。
    """
    scen = scenario_override if scenario_override is not None else scenario(scen_name)
    all_trades, all_skipped = precompute_trades_all(
        bars_by_symbol,
        signals,
        rules,
        groups,
        scen=scen,
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        k_sl=k_sl,
        sl_min=sl_min,
        sl_max=sl_max,
        H_label=H_label,
        trade_masks=trade_masks,
        new_entry_symbols=new_entry_symbols,
    )
    result = simulate_portfolio(
        all_trades,
        all_skipped,
        sizing_cfg=sizing_cfg,
        backtest_cfg=backtest_cfg,
        fees=fees,
        initial_equity=initial_equity,
        bars_by_symbol=bars_by_symbol,
    )
    return result, all_trades
