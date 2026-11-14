"""回测引擎：逐笔预计算 + 组合模拟（R0 驱动）。

规格：docs/06-回测引擎.md §3、§5
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from trade.backtest.costs import FeeSchedule, Scenario, scenario
from trade.common.anchors import anchor_series
from trade.common.batch import batch_exit
from trade.common.costs import net_return_after_fees
from trade.common.entry import half_spread_for
from trade.common.exits import (
    EXIT_RISK_HALT,
    EXIT_TYPE_NAMES,
    ExitConfig,
    exit_anchor_spec,
)
from trade.common.funding import funding_rate_between, funding_settlements
from trade.common.risk_rules import (
    RiskEvent,
    RiskLimits,
    RiskState,
    register_exit,
    register_pause_if_needed,
    risk_step,
)
from trade.common.sizing import SizingConfig, calc_notional, equity_floor_hit
from trade.features.kline import rolling_mean, rv_15, true_range, wilder_atr

# precompute_trades 与盯市只读取这些列（06 §3.4）；供 `load_bars(columns=...)` 使用
PRECOMPUTE_BAR_COLUMNS: tuple[str, ...] = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "is_filled",
    "kline_missing",
    "aggtrades_missing",
    "p_1s",
    "p_1s_filled",
    "hi_post_1s",
    "lo_post_1s",
    "p_3s",
    "p_3s_filled",
    "hi_post_3s",
    "lo_post_3s",
    "funding_time",
    "funding_rate",
)


class CostConfig(BaseModel):
    """成本模型参数（06 §4）。"""

    model_config = ConfigDict(extra="forbid")

    half_spread_min: float = Field(default=0.0001, gt=0.0)
    slip_min: float = Field(default=0.0001, gt=0.0)
    Y: float = Field(default=1.0, gt=0.0)
    order_rtt_ms: int = Field(default=200, ge=0, le=2000)
    # 实测点差画像路径（06 §4.3，`spread_measured` 情景用；None 表示未配置）
    spread_profile: str | None = None


class RiskConfig(BaseModel):
    """回测风控参数（06 §3.1、§3.4）。"""

    model_config = ConfigDict(extra="forbid")

    cooldown_bars: int = Field(default=15, ge=0)
    max_chase: float = Field(default=0.003, gt=0.0)
    # R6/R8 触发后的处理：strict = 停止交易至运行结束；
    # reset = 停 halt_reset_days 个自然日后按当时权益继续
    halt_mode: str = Field(default="strict", pattern=r"^(strict|reset)$")
    halt_reset_days: int = Field(default=1, ge=1, le=30)


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

    initial_equity: float = Field(default=100.0, gt=0.0)
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


def load_trade_rules(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读 `rules.json` 与 `aliases.json`，返回 alias → `TradeRule`（03 §4.6、09 §3）。

    `rules.json` 以 symbol 为键，回测与标签以 alias 为键（03 §1.1 第 7 条）；两者必须经
    `aliases.json` 映射，不能假设键相同。缺字段的条目由 `TradeRule.from_snapshot` 报错。

    Args:
        rules_path: exchangeInfo 规则快照 `rules.json`。
        aliases_path: `data/universe/aliases.json`（symbol → alias）。

    Returns:
        alias → TradeRule；`rules.json` 中没有的 symbol 不写入（由调用方按需报错）。
    """
    rules_raw: dict[str, dict[str, object]] = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias: dict[str, str] = json.loads(aliases_path.read_text(encoding="utf-8"))
    rules: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        raw = rules_raw.get(symbol)
        if raw is not None:
            rules[alias] = TradeRule.from_snapshot(alias, raw)
    # 兼容以 alias 为键的快照（fixture 与旧快照）；已由 symbol 映射的条目以 symbol 为准
    known_aliases = set(symbol_to_alias.values())
    for key, raw in rules_raw.items():
        if key in known_aliases and key not in rules:
            rules[key] = TradeRule.from_snapshot(key, raw)
    return rules


@dataclass(frozen=True)
class TradeRule:
    """交易规则快照（exchangeInfo，09 §3）。"""

    tick_size: float
    step_size: float
    min_notional: float

    @classmethod
    def from_snapshot(cls, alias: str, raw: Mapping[str, object]) -> TradeRule:
        """由 `rules.json` 的单个条目构造交易规则（03 §4.6）。

        快照条目可能含 `base_asset`、`contract_type` 等扩展字段，这里只取回测需要的
        三项；缺任一字段直接报错，不回退默认值（红线 13）。

        Args:
            alias: 币别名（用于报错上下文）。
            raw: `rules.json` 中该 symbol 的原始字典。

        Returns:
            `TradeRule`。

        Raises:
            ValueError: 缺少 `tick_size`、`step_size` 或 `min_notional`。
        """
        missing = [name for name in ("tick_size", "step_size", "min_notional") if name not in raw]
        if missing:
            raise ValueError(f"交易规则缺少字段 {missing}：{alias}（03 §4.6、红线 13）")
        return cls(
            tick_size=float(raw["tick_size"]),  # type: ignore[arg-type]
            step_size=float(raw["step_size"]),  # type: ignore[arg-type]
            min_notional=float(raw["min_notional"]),  # type: ignore[arg-type]
        )


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
    # 该行信号生效的阈值 θ（signals.parquet 的 theta 列，06 §3.1 条件 1、§8）
    theta: float = 0.0
    # 以下字段供 S8 逐笔重放复用同一套入场成本与出场参数（06 §7 S8），不进报告 schema。
    atr_abs: float = 0.0
    slip_stop: float = 0.0
    slip_exit: float = 0.0
    half_spread: float = 0.0
    notional_ref: float = 0.0
    h_max: int = 0
    entry_bar_post_missing: int = 0


@dataclass
class SkippedSignal:
    """被放弃的候选信号（06 §8 skipped.parquet）。"""

    symbol: str
    open_time: int
    p: float
    reason: str
    # 该行信号生效的阈值 θ（按行阈值过滤用，06 §3.1 条件 1）
    theta: float = 0.0


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
    rebate: float = 0.0
    entry_bar_post_missing: int = 0
    # 风控强平（R6/R8）用：入场时的半价差与离场滑点（06 §3.3 同口径平仓）
    half_spread: float = 0.0
    slip_exit: float = 0.0


@dataclass
class PortfolioResult:
    """组合模拟结果。"""

    trades: list[ExecutedTrade] = field(default_factory=list)
    skipped: list[SkippedSignal] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    final_equity: float = 0.0
    equity_floor_hit: bool = False
    max_drawdown_halt: bool = False
    # 风控 R5~R8 的触发记录（06 §6、§8）
    risk_events: list[RiskEvent] = field(default_factory=list)


def _row_index(open_time: np.ndarray, ts_ms: int) -> int:
    """返回 open_time == ts_ms 的行下标；不存在时返回 -1（06 §3.4）。

    用 `np.searchsorted` 取代逐币分钟建 dict，命中后再校验一次时间戳相等。
    """
    pos = int(np.searchsorted(open_time, ts_ms, side="left"))
    if pos >= open_time.shape[0] or int(open_time[pos]) != ts_ms:
        return -1
    return pos


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
class PreparedSymbol:
    """单个币的 numpy 数组与派生量（06 §3.4）：DataFrame → 数组只做一次，进程内复用。

    执行类原子量（`p_1s` 等）按执行延迟各存一组；bars 未提供的为 None，由
    `entry_arrays` 在实际用到时抛错（不在准备阶段静默补值）。
    """

    symbol: str
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote_volume: np.ndarray
    kline_missing: np.ndarray
    aggtrades_missing: np.ndarray
    p_1s: np.ndarray | None
    p_1s_filled: np.ndarray | None
    hi_post_1s: np.ndarray | None
    lo_post_1s: np.ndarray | None
    p_3s: np.ndarray | None
    p_3s_filled: np.ndarray | None
    hi_post_3s: np.ndarray | None
    lo_post_3s: np.ndarray | None
    atr_abs: np.ndarray
    atr_pct: np.ndarray
    rv15: np.ndarray
    mean_qv_60: np.ndarray
    funding_time: np.ndarray
    funding_rate: np.ndarray

    def entry_arrays(self, latency_ms: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """按执行延迟返回 (入场价, 持仓内最高, 持仓内最低) 数组（06 §3.2、§4.3）。

        Raises:
            ValueError: 不支持的延迟，或 bars 缺少该延迟所需的执行类原子量。
        """
        if latency_ms == 1000:
            price, high_post, low_post = self.p_1s, self.hi_post_1s, self.lo_post_1s
        elif latency_ms == 3000:
            price, high_post, low_post = self.p_3s, self.hi_post_3s, self.lo_post_3s
        else:
            raise ValueError(f"不支持的执行延迟：{latency_ms}（仅 1000/3000）")
        if price is None or high_post is None or low_post is None:
            raise ValueError(f"bars 缺少 {latency_ms}ms 延迟所需的执行类原子量（{self.symbol}）")
        return price, high_post, low_post

    def entry_filled(self, latency_ms: int) -> np.ndarray:
        """按执行延迟返回入场价陈旧标记 `p_{lat}_filled`（03 §2.2、06 §3.1）。

        Raises:
            ValueError: 不支持的延迟，或 bars 缺少该延迟的 `p_*_filled` 列。
        """
        if latency_ms == 1000:
            filled = self.p_1s_filled
        elif latency_ms == 3000:
            filled = self.p_3s_filled
        else:
            raise ValueError(f"不支持的执行延迟：{latency_ms}（仅 1000/3000）")
        if filled is None:
            raise ValueError(
                f"bars 缺少 {latency_ms}ms 延迟所需的 p_*_filled 列（{self.symbol}；03 §2.2）"
            )
        return filled


def _bar_column(bars: pd.DataFrame, name: str, dtype: str) -> np.ndarray:
    """取 bars 的一列并转成指定 dtype。

    Raises:
        ValueError: 列缺失（红线 13：不用默认值顶替）。
    """
    if name not in bars.columns:
        raise ValueError(f"bars 缺少列 {name!r}（06 §3.4）")
    return np.asarray(bars[name].to_numpy(dtype=dtype), dtype=dtype)


def prepare_symbols(bars_by_symbol: Mapping[str, pd.DataFrame]) -> dict[str, PreparedSymbol]:
    """把每币 bars 一次性转成 numpy 数组与派生指标（06 §3.4）。

    Args:
        bars_by_symbol: 币种 → 分钟 bars（含 03 §2.3 的执行类原子量）。

    Returns:
        币种 → PreparedSymbol；同一份输入多次调用结果相同。

    Raises:
        ValueError: 缺少必需列。
    """
    prepared: dict[str, PreparedSymbol] = {}
    for symbol, bars in bars_by_symbol.items():
        arrays = _symbol_arrays(bars)
        funding_time, funding_rate = funding_settlements(bars)
        prepared[symbol] = PreparedSymbol(
            symbol=symbol,
            open_time=_bar_column(bars, "open_time", "int64"),
            open=_bar_column(bars, "open", "float64"),
            high=_bar_column(bars, "high", "float64"),
            low=_bar_column(bars, "low", "float64"),
            close=_bar_column(bars, "close", "float64"),
            quote_volume=_bar_column(bars, "quote_volume", "float64"),
            kline_missing=_bar_column(bars, "kline_missing", "int64"),
            aggtrades_missing=_bar_column(bars, "aggtrades_missing", "int64"),
            p_1s=_optional_bar_column(bars, "p_1s"),
            p_1s_filled=_optional_bar_column(bars, "p_1s_filled"),
            hi_post_1s=_optional_bar_column(bars, "hi_post_1s"),
            lo_post_1s=_optional_bar_column(bars, "lo_post_1s"),
            p_3s=_optional_bar_column(bars, "p_3s"),
            p_3s_filled=_optional_bar_column(bars, "p_3s_filled"),
            hi_post_3s=_optional_bar_column(bars, "hi_post_3s"),
            lo_post_3s=_optional_bar_column(bars, "lo_post_3s"),
            atr_abs=arrays["atr_abs"],
            atr_pct=arrays["atr_pct"],
            rv15=arrays["rv15"],
            mean_qv_60=arrays["mean_qv_60"],
            funding_time=funding_time,
            funding_rate=funding_rate,
        )
    return prepared


def _optional_bar_column(bars: pd.DataFrame, name: str) -> np.ndarray | None:
    """取执行类原子量列；列不存在时返回 None（用到时才报错，见 `entry_arrays`）。"""
    return bars[name].to_numpy(dtype="float64") if name in bars.columns else None


@dataclass(frozen=True)
class CloseSeries:
    """单币 close 序列（open_time 升序 int64 + close float64），供逐分钟盯市（06 §3.4）。"""

    open_time: np.ndarray
    close: np.ndarray


def _close_arrays(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame] | None,
) -> dict[str, CloseSeries]:
    """取每币的 open_time 与 close 两列（不复制整张 bars），供逐分钟盯市。

    规格：docs/06-回测引擎.md §3.4、§5.1

    Args:
        bars_by_symbol: 币种 → `PreparedSymbol` 或分钟 bars（缺 `open_time` 或
            `close` 列的币跳过）。

    Returns:
        币种 → CloseSeries；`open_time` 升序（bars 未按时间排序时按时间重排两列）。
    """
    if not bars_by_symbol:
        return {}
    arrays: dict[str, CloseSeries] = {}
    for symbol, bars in bars_by_symbol.items():
        if isinstance(bars, PreparedSymbol):
            arrays[symbol] = CloseSeries(open_time=bars.open_time, close=bars.close)
            continue
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
    # 出场手续费按出场名义价值 notional × (1 + gross_return) 计（06 §4.1）
    return sz.notional * net_return_after_fees(
        gross_return=tr.gross_return,
        entry_fee_rate=fees.taker,
        exit_fee_rate=tr.exit_fee_rate,
        funding_rate=tr.funding_rate_sum,
    )


def precompute_trades(
    bars: PreparedSymbol | pd.DataFrame,
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
    p_min: float | None = None,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """逐笔预计算（06 §3.4 第 1 步）：为每个候选信号算 P_fill 与出场结果。

    只处理 p ≥ θ 的候选；开仓过滤、追价保护在此判断，仓位与组合约束在
    `simulate_portfolio` 中判断（06 §3.4 第 2 步）。

    Args:
        bars: 该币的 `PreparedSymbol`，或原始分钟 bars（内部转换一次）。
        p_min: 候选过滤的 p 下限；None 表示用 `backtest_cfg.threshold`。同进程复用
            预计算结果时先用 0.0 算一次、再按各阈值过滤（06 §3.4）。
        trade_mask: 可选，与 bars 等长的 bool 数组，标记该分钟是否在当月交易范围
            `U(R)` 内（06 §3.1 条件 8）。为 None 时视为全部可交易（无宇宙约束）。

    Returns:
        (逐笔预计算结果, 被放弃的候选)。

    Raises:
        ValueError: bars 缺列或不支持的执行延迟（红线 13）。
    """
    prepared = bars if isinstance(bars, PreparedSymbol) else prepare_symbols({symbol: bars})[symbol]
    n = int(prepared.open_time.shape[0])
    if n == 0:
        return [], []
    p_entry_arr, hi_post, lo_post = prepared.entry_arrays(scen.latency_ms)
    p_filled_arr = prepared.entry_filled(scen.latency_ms)
    open_time = prepared.open_time
    close = prepared.close
    qv = prepared.quote_volume
    kline_missing = prepared.kline_missing
    aggtrades_missing = prepared.aggtrades_missing
    high = prepared.high
    low = prepared.low
    open_ = prepared.open
    atr_abs_arr = prepared.atr_abs
    atr_pct_arr = prepared.atr_pct
    rv15_arr = prepared.rv15
    mean_qv_60_arr = prepared.mean_qv_60
    if "theta" not in signals.columns:
        raise ValueError(
            "signals 缺少 theta 列（signals.parquet 必须带每行阈值 θ；06 §8、红线 13）"
        )
    threshold = backtest_cfg.threshold if p_min is None else p_min

    notional_ref = sizing_cfg.initial_equity * sizing_cfg.position_frac * float(sizing_cfg.leverage)
    half_spread_min = scen.half_spread_min_for(
        symbol, backtest_cfg.costs.half_spread_min * scen.half_spread_mult
    )
    slip_min = backtest_cfg.costs.slip_min * scen.slip_mult
    H_max = exit_cfg.H_max if exit_cfg.type != "fixed" else H_label
    # 锚点与入场成本按币算一次（06 §3.4）：逐笔只按下标取值，不在信号循环里重算
    anchors = anchor_series(
        close=close,
        p_entry=p_entry_arr,
        atr_pct=atr_pct_arr,
        rv15=rv15_arr,
        qv=qv,
        tick_size=rule.tick_size,
        spec=exit_anchor_spec(
            exit_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            half_spread_min=half_spread_min,
            slip_min=slip_min,
            Y=backtest_cfg.costs.Y,
            fixed_slip=scen.fixed_slip,
            fee_taker=fees.taker,
            notional_ref=notional_ref,
        ),
    )

    skipped: list[SkippedSignal] = []
    fund_times, fund_rates = prepared.funding_time, prepared.funding_rate
    kept_t: list[int] = []
    kept_signal: list[tuple[int, float, float, float]] = []

    for row in signals.itertuples(index=False):
        # 开仓阈值按行取（五折各用自己的 θ，06 §3.1 条件 1、§8）
        theta = float(row.theta)
        p_signal = float(row.p)
        if p_signal < threshold:
            continue
        t = _row_index(open_time, int(row.open_time))
        if t < 0 or t + 1 >= n:
            continue
        if trade_mask is not None and not bool(trade_mask[t]):
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), p_signal, "not_in_universe", theta)
            )
            continue
        if kline_missing[t + 1] == 1:
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), p_signal, "entry_bar_filled", theta)
            )
            continue
        if p_filled_arr[t + 1] == 1.0:
            # 入场那一秒无成交、价格沿用上一分钟收盘价：实盘读到陈旧价，放弃（06 §3.1 条件 1）
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), p_signal, "entry_price_stale", theta)
            )
            continue
        p_raw = float(p_entry_arr[t + 1])
        if not np.isfinite(p_raw) or p_raw <= 0:
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), p_signal, "no_entry_price", theta)
            )
            continue
        c_t = float(close[t])
        if not np.isfinite(c_t) or c_t <= 0:
            skipped.append(SkippedSignal(symbol, int(row.open_time), p_signal, "no_atr", theta))
            continue
        hs_t = half_spread_for(c_t, rule.tick_size, half_spread_min)
        if p_raw * (1.0 + hs_t) > c_t * (1.0 + backtest_cfg.risk.max_chase):
            skipped.append(SkippedSignal(symbol, int(row.open_time), p_signal, "chase", theta))
            continue
        sigma = float(atr_pct_arr[t])
        if not np.isfinite(sigma):
            skipped.append(SkippedSignal(symbol, int(row.open_time), p_signal, "no_atr", theta))
            continue
        if not np.isfinite(rv15_arr[t]) or not np.isfinite(mean_qv_60_arr[t]):
            skipped.append(
                SkippedSignal(symbol, int(row.open_time), p_signal, "no_lookback", theta)
            )
            continue
        kept_t.append(t)
        kept_signal.append((int(row.open_time), p_signal, float(row.m), theta))

    if not kept_t:
        return [], skipped

    t_all = np.asarray(kept_t, dtype="int64")
    batch = batch_exit(
        open_=open_,
        high=high,
        low=low,
        close=close,
        p_entry=p_entry_arr,
        hi_post=hi_post,
        lo_post=lo_post,
        kline_missing=kline_missing,
        aggtrades_missing=aggtrades_missing,
        anchors=anchors,
        signal_bars=t_all,
        atr_abs=atr_abs_arr,
        h_max=H_max,
        cfg=exit_cfg,
        tick_size=rule.tick_size,
        fee_taker=fees.taker,
        fee_maker=fees.maker,
    )

    trades: list[PrecomputedTrade] = []
    for slot, t in enumerate(kept_t):
        signal_open_time, p_signal, m_signal, theta = kept_signal[slot]
        p_fill = float(anchors.p_fill[t])
        hs = float(anchors.half_spread[t])
        slip_ = float(anchors.slip[t])
        exit_idx = int(batch.exit_idx[slot])
        exit_price = float(batch.exit_price[slot])
        if not np.isfinite(exit_price) or exit_price <= 0.0:
            # 出场分钟缺 aggTrades 时 p_1s 为空、出场价无法确定（06 §3.3 缺数据
            # 分钟不判定）：按该根 K 线收盘价出场（同样扣点差与离场滑点），
            # 避免净盈亏变成 NaN。
            exit_price = float(close[exit_idx]) * (1.0 - hs - slip_)
        exit_type_code = int(batch.exit_type[slot])
        if scen.all_taker or (exit_type_code == 3 and scen.tp_taker):
            exit_fee_rate = fees.taker
        else:
            exit_fee_rate = float(batch.fee_rate[slot])

        trades.append(
            PrecomputedTrade(
                symbol=symbol,
                group=group,
                signal_open_time=signal_open_time,
                entry_open_time=int(open_time[t + 1]),
                p=p_signal,
                m=m_signal,
                p_fill=p_fill,
                sl=float(anchors.sl[t]),
                loss_rate=float(anchors.loss_rate[t]),
                mean_qv_60=float(mean_qv_60_arr[t]),
                exit_open_time=int(open_time[exit_idx]),
                exit_price=exit_price,
                exit_type=EXIT_TYPE_NAMES[exit_type_code],
                exit_type_code=exit_type_code,
                bars_held=int(batch.bars_held[slot]),
                stop_updates=int(batch.stop_updates[slot]),
                mfe_r=float(batch.mfe_r[slot]),
                mae_r=float(batch.mae_r[slot]),
                gross_return=exit_price / p_fill - 1.0,
                exit_fee_rate=exit_fee_rate,
                entry_bar_post_missing=int(batch.entry_bar_post_missing[slot]),
                funding_rate_sum=funding_rate_between(
                    fund_times,
                    fund_rates,
                    int(open_time[t + 1]),
                    int(open_time[exit_idx]),
                ),
                tick_size=rule.tick_size,
                step_size=rule.step_size,
                min_notional=rule.min_notional,
                new_entry=new_entry,
                atr_abs=float(atr_abs_arr[t]),
                slip_stop=float(anchors.slip_stop[t]),
                slip_exit=slip_,
                half_spread=hs,
                notional_ref=notional_ref,
                h_max=H_max,
                theta=theta,
            )
        )
    return trades, skipped


def _risk_limits(sizing_cfg: SizingConfig, backtest_cfg: BacktestConfig) -> RiskLimits:
    """从仓位与回测配置构造风控参数（06 §2、§5.4；07 §7.1）。"""
    return RiskLimits(
        daily_loss_stop=sizing_cfg.daily_loss_stop,
        daily_loss_halt=sizing_cfg.daily_loss_halt,
        max_drawdown_halt=sizing_cfg.max_drawdown_halt,
        max_consecutive_losses=sizing_cfg.max_consecutive_losses,
        consecutive_loss_pause_min=sizing_cfg.consecutive_loss_pause_min,
        halt_mode=backtest_cfg.risk.halt_mode,
        halt_reset_days=backtest_cfg.risk.halt_reset_days,
    )


def _entry_price_arrays(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame] | None,
) -> dict[str, CloseSeries]:
    """取每币 `p_1s` 序列（风控强平按下一分钟 `p_1s` 平仓，06 §3.4）。"""
    arrays: dict[str, CloseSeries] = {}
    if not bars_by_symbol:
        return arrays
    for symbol, bars in bars_by_symbol.items():
        if isinstance(bars, PreparedSymbol):
            if bars.p_1s is not None:
                arrays[symbol] = CloseSeries(open_time=bars.open_time, close=bars.p_1s)
            continue
        if "open_time" in bars.columns and "p_1s" in bars.columns:
            arrays[symbol] = CloseSeries(
                open_time=bars["open_time"].to_numpy(dtype="int64"),
                close=bars["p_1s"].to_numpy(dtype="float64"),
            )
    return arrays


def _funding_arrays(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame] | None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """取每币资金费结算时刻与费率（风控强平重算资金费，06 §3.3）。"""
    arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if not bars_by_symbol:
        return arrays
    for symbol, bars in bars_by_symbol.items():
        if isinstance(bars, PreparedSymbol):
            arrays[symbol] = (bars.funding_time, bars.funding_rate)
        else:
            arrays[symbol] = funding_settlements(bars)
    return arrays


def _risk_timeline(
    trades: list[PrecomputedTrade], close_arrays: Mapping[str, CloseSeries]
) -> np.ndarray:
    """风控逐分钟检查的时间网格（06 §3.4）：全部入场/离场时刻 ∪ 持仓期该币的 K 线时刻。"""
    if not trades:
        return np.empty(0, dtype="int64")
    pieces: list[np.ndarray] = [
        np.fromiter((item.entry_open_time for item in trades), dtype="int64", count=len(trades)),
        np.fromiter((item.exit_open_time for item in trades), dtype="int64", count=len(trades)),
    ]
    for item in trades:
        series = close_arrays.get(item.symbol)
        if series is None:
            continue
        lo = int(np.searchsorted(series.open_time, item.entry_open_time, side="left"))
        hi = int(np.searchsorted(series.open_time, item.exit_open_time, side="left"))
        if hi > lo:
            pieces.append(series.open_time[lo:hi])
    return np.unique(np.concatenate(pieces))


def _unrealized_at(
    positions: Mapping[str, ExecutedTrade], close_arrays: Mapping[str, CloseSeries], ts_ms: int
) -> float:
    """持仓在 `ts_ms` 的未实现盈亏（缺失分钟按 `p_fill` 计，贡献 0；06 §3.4）。"""
    total = 0.0
    for trade in positions.values():
        series = close_arrays.get(trade.symbol)
        if series is None:
            continue
        pos = int(np.searchsorted(series.open_time, ts_ms, side="left"))
        if pos >= series.open_time.shape[0] or int(series.open_time[pos]) != ts_ms:
            continue
        price = float(series.close[pos])
        if price > 0.0:
            total += trade.notional * (price / trade.p_fill - 1.0)
    return total


@dataclass
class PortfolioBook:
    """组合账本：钱包余额、持仓、冷却与风控状态（06 §3.4）。

    回测快速模拟器（`simulate_portfolio`）与事件驱动参考模拟器（`event_sim`）共用本账本，
    风控判定由 `trade.common.risk_rules` 提供唯一实现（AGENTS.md §6 红线 4）。
    """

    wallet: float
    positions: dict[str, ExecutedTrade]
    cooldown_until: dict[str, int]
    result: PortfolioResult
    close_arrays: Mapping[str, CloseSeries]
    entry_arrays: Mapping[str, CloseSeries]
    funding_arrays: Mapping[str, tuple[np.ndarray, np.ndarray]]
    risk: RiskState
    limits: RiskLimits
    cooldown_bars: int
    fees: FeeSchedule

    def equity_at(self, ts_ms: int) -> float:
        """`ts_ms` 的盯市权益 = 钱包余额 + 未实现盈亏（06 §5.1）。"""
        return self.wallet + _unrealized_at(self.positions, self.close_arrays, ts_ms)

    def release_due(self, ts_ms: int) -> None:
        """结算 `exit_open_time ≤ ts_ms` 的持仓（06 §3.4 第 2 步）。"""
        due = sorted(
            (
                (sym, trade)
                for sym, trade in self.positions.items()
                if trade.exit_open_time <= ts_ms
            ),
            key=lambda kv: kv[1].exit_open_time,
        )
        for sym, trade in due:
            self.realize(sym, trade)

    def realize(self, symbol: str, trade: ExecutedTrade) -> None:
        """把一笔已结束的交易计入钱包余额并更新 R7 连续止损计数（07 §7.1）。"""
        self.wallet += trade.net_pnl
        register_exit(
            self.risk,
            ts_ms=trade.exit_open_time,
            net_pnl=trade.net_pnl,
            hit_stop_type=trade.exit_type_code in (0, 1, 2),
        )
        register_pause_if_needed(self.risk, ts_ms=trade.exit_open_time, limits=self.limits)
        if trade.exit_type_code in (0, 1, 2):
            self.cooldown_until[symbol] = trade.exit_open_time + self.cooldown_bars * 60_000
        self.positions.pop(symbol, None)

    def advance(self, until_ms: int, timeline: np.ndarray, cursor: int) -> int:
        """推进到 `until_ms`：逐分钟结算离场并检查 R5~R8，返回新的游标（06 §3.4）。"""
        if not self.positions:
            # 空仓时权益不随价格变化：跳过网格中间点，只更新当日初权益与峰值
            cursor = int(np.searchsorted(timeline, until_ms, side="right"))
            risk_step(self.risk, ts_ms=until_ms, equity=self.wallet, limits=self.limits)
            return cursor
        n = int(timeline.shape[0])
        while cursor < n and int(timeline[cursor]) <= until_ms:
            ts = int(timeline[cursor])
            self.release_due(ts)
            action = risk_step(self.risk, ts_ms=ts, equity=self.equity_at(ts), limits=self.limits)
            if action == "flatten":
                self.flatten_all(ts)
            cursor += 1
        return cursor

    def flatten_all(self, ts_ms: int) -> None:
        """R6/R8：下一分钟按 `p_1s` 平掉全部持仓（06 §3.4、07 §7.1）。

        出场价 = 下一分钟 `p_1s × (1 − 半价差 − 离场滑点)`，吃单费率；下一分钟缺逐笔
        成交价时用该分钟收盘价（与 06 §3.3 的出场价回退同口径）。没有下一根 K 线时
        保留原定出场（不臆造价格）。
        """
        for symbol, trade in list(self.positions.items()):
            series = self.entry_arrays.get(symbol)
            close_series = self.close_arrays.get(symbol)
            if series is None or close_series is None:
                continue
            pos = int(np.searchsorted(series.open_time, ts_ms, side="right"))
            if pos >= series.open_time.shape[0]:
                continue
            new_ts = int(series.open_time[pos])
            price = float(series.close[pos])
            if not np.isfinite(price) or price <= 0.0:
                close_pos = int(np.searchsorted(close_series.open_time, new_ts, side="left"))
                if close_pos >= close_series.open_time.shape[0]:
                    continue
                price = float(close_series.close[close_pos])
            price = price * (1.0 - trade.half_spread - trade.slip_exit)
            gross = price / trade.p_fill - 1.0
            times, rates = self.funding_arrays.get(symbol, (np.empty(0), np.empty(0)))
            funding = funding_rate_between(times, rates, trade.entry_open_time, new_ts)
            trade.exit_open_time = new_ts
            trade.exit_price = price
            trade.exit_type = EXIT_TYPE_NAMES[EXIT_RISK_HALT]
            trade.exit_type_code = EXIT_RISK_HALT
            trade.exit_fee = trade.notional * (1.0 + gross) * self.fees.taker
            trade.funding_fee = trade.notional * funding
            trade.gross_pnl = trade.notional * gross
            trade.net_pnl = trade.notional * net_return_after_fees(
                gross_return=gross,
                entry_fee_rate=self.fees.taker,
                exit_fee_rate=self.fees.taker,
                funding_rate=funding,
            )
            trade.bars_held = (new_ts - trade.entry_open_time) // 60_000 + 1
            self.realize(symbol, trade)


def simulate_portfolio(
    trades: list[PrecomputedTrade],
    precompute_skipped: list[SkippedSignal],
    *,
    sizing_cfg: SizingConfig,
    backtest_cfg: BacktestConfig,
    fees: FeeSchedule,
    initial_equity: float | None = None,
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame] | None = None,
) -> PortfolioResult:
    """组合模拟（06 §3.4 第 2 步）：按时间推进，应用仓位与组合约束。

    组合约束：冷却期、组合持仓上限、候选排序（p 降序、m 降序）、当日亏损停止、
    权益下限、保证金与数量取整（06 §3.1、§5.3）。权益曲线按盯市权益（钱包余额
    + 未实现盈亏）逐分钟生成，回撤与回撤停机基于该曲线（06 §5.1、§3.4）。
    """
    start_equity = sizing_cfg.initial_equity if initial_equity is None else initial_equity
    result = PortfolioResult(skipped=list(precompute_skipped))
    min_min_notional = min((t.min_notional for t in trades), default=1e18)
    close_arrays = _close_arrays(bars_by_symbol)
    limits = _risk_limits(sizing_cfg, backtest_cfg)
    book = PortfolioBook(
        wallet=start_equity,
        positions={},
        cooldown_until={},
        result=result,
        close_arrays=close_arrays,
        entry_arrays=_entry_price_arrays(bars_by_symbol),
        funding_arrays=_funding_arrays(bars_by_symbol),
        risk=RiskState(peak_equity=start_equity),
        limits=limits,
        cooldown_bars=backtest_cfg.risk.cooldown_bars,
        fees=fees,
    )
    timeline = _risk_timeline(trades, close_arrays)
    cursor = 0
    positions = book.positions

    ordered = sorted(trades, key=lambda tr: (tr.entry_open_time, -tr.p, -tr.m))

    for tr in ordered:
        # 推进分钟时钟：结算到期离场并检查 R5~R8（06 §3.4 第 2 步）
        cursor = book.advance(tr.entry_open_time, timeline, cursor)
        if book.risk.halted(tr.entry_open_time):
            result.skipped.append(SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "halted"))
            continue
        if tr.symbol in positions:
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "already_in_position")
            )
            continue
        if tr.entry_open_time < book.cooldown_until.get(tr.symbol, 0):
            result.skipped.append(SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "cooldown"))
            continue
        if len(positions) >= sizing_cfg.max_positions:
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "max_positions")
            )
            continue
        day = _utc_day(tr.entry_open_time)
        if book.risk.daily_stop(day):
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "daily_loss_stop")
            )
            continue
        if book.risk.paused(tr.entry_open_time):
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "consecutive_loss_pause")
            )
            continue
        base = book.wallet if sizing_cfg.base == "equity" else sizing_cfg.initial_equity
        if equity_floor_hit(base, sizing_cfg, min_min_notional):
            result.equity_floor_hit = True
            result.skipped.append(
                SkippedSignal(tr.symbol, tr.signal_open_time, tr.p, "equity_floor")
            )
            continue
        available_margin = book.wallet - sum(t.margin for t in positions.values())
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
        net_pnl = sz.notional * net_return_after_fees(
            gross_return=tr.gross_return,
            entry_fee_rate=fees.taker,
            exit_fee_rate=tr.exit_fee_rate,
            funding_rate=tr.funding_rate_sum,
        )
        entry_fee = sz.notional * fees.taker
        exit_fee = sz.notional * (1.0 + tr.gross_return) * tr.exit_fee_rate
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
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            funding_fee=funding_fee,
            gross_pnl=sz.notional * tr.gross_return,
            net_pnl=net_pnl,
            rebate=(entry_fee + exit_fee) * fees.rebate_rate,
            capped_by=sz.capped_by,
            new_entry=tr.new_entry,
            entry_bar_post_missing=tr.entry_bar_post_missing,
            half_spread=tr.half_spread,
            slip_exit=tr.slip_exit,
        )
        positions[tr.symbol] = trade
        result.trades.append(trade)

    if timeline.size:
        # 走完剩余分钟：让风控在最后一批持仓上同样生效（06 §3.4）
        book.advance(int(timeline[-1]), timeline, cursor)
        book.release_due(int(timeline[-1]))
    for _sym, trade in sorted(list(positions.items()), key=lambda kv: kv[1].exit_open_time):
        book.realize(_sym, trade)

    result.risk_events = list(book.risk.events)
    result.final_equity = book.wallet
    result.equity_curve = _mark_to_market_curve(result.trades, close_arrays, start_equity)
    result.max_drawdown_halt = any(event.rule == "R8" for event in book.risk.events)
    return result


def _as_prepared(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame],
) -> dict[str, PreparedSymbol]:
    """把 bars 入口统一成 PreparedSymbol 映射（DataFrame 入口是薄包装，06 §3.4）。"""
    prepared: dict[str, PreparedSymbol] = {}
    for symbol, bars in bars_by_symbol.items():
        prepared[symbol] = (
            bars if isinstance(bars, PreparedSymbol) else prepare_symbols({symbol: bars})[symbol]
        )
    return prepared


def _signals_by_symbol(signals: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """按 symbol 一次拆分信号（06 §3.4），避免在币循环里反复布尔筛选。"""
    if signals.empty:
        return {}
    return {str(symbol): frame for symbol, frame in signals.groupby("symbol", sort=False)}


def precompute_trades_all(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame],
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
    p_min: float | None = None,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """为全部信号做逐笔预计算（06 §3.4 第 1 步）。

    阈值 θ 只影响「p ≥ θ 的候选过滤」，不影响逐笔出场模拟（`simulate_exit` 与 θ
    无关）。把预计算独立出来，先用 `p_min=0.0` 算一次、再按各 θ 过滤，即可在阈值
    网格/敏感性扫描/情景重复中复用同一批逐笔结果（06 §3.4、§7 S4）。
    """
    prepared = _as_prepared(bars_by_symbol)
    by_symbol = _signals_by_symbol(signals)
    # 默认预计算下限 = 该批信号中最小 θ（按行阈值），与逐次预计算的结果一致（06 §3.4）
    floor = p_min if p_min is not None else float(signals["theta"].min())
    all_trades: list[PrecomputedTrade] = []
    all_skipped: list[SkippedSignal] = []
    for symbol, item in prepared.items():
        sym_signals = by_symbol.get(symbol)
        if sym_signals is None or sym_signals.empty:
            continue
        trades, skipped = precompute_trades(
            item,
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
            p_min=floor,
        )
        all_trades.extend(trades)
        all_skipped.extend(skipped)
    return all_trades, all_skipped


# 进程内逐笔预计算缓存（06 §3.4）：键覆盖影响逐笔结果的全部输入，值为 p ≥ 0 的结果。
_PRECOMPUTE_CACHE: dict[tuple[object, ...], tuple[list[PrecomputedTrade], list[SkippedSignal]]] = {}
_PRECOMPUTE_CACHE_LIMIT = 8


def clear_precompute_cache() -> None:
    """清空进程内逐笔预计算缓存（测试与长跑脚本的显式内存回收）。"""
    _PRECOMPUTE_CACHE.clear()


def _data_token(prepared: Mapping[str, PreparedSymbol]) -> tuple[object, ...]:
    """bars 内容指纹：币种、行数、首尾时间戳、close 与 quote_volume 之和。"""
    token: list[object] = []
    for symbol, item in sorted(prepared.items()):
        count = int(item.open_time.shape[0])
        token.append(
            (
                symbol,
                count,
                int(item.open_time[0]) if count else 0,
                int(item.open_time[-1]) if count else 0,
                float(np.sum(item.close)),
                float(np.sum(item.quote_volume)),
            )
        )
    return tuple(token)


def _precompute_key(
    prepared: Mapping[str, PreparedSymbol],
    rules: Mapping[str, TradeRule],
    groups: Mapping[str, str],
    *,
    scen: Scenario,
    fees: FeeSchedule,
    sizing_cfg: SizingConfig,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
    trade_masks: dict[str, np.ndarray] | None,
    new_entry_symbols: set[str] | None,
) -> tuple[object, ...]:
    """逐笔预计算缓存键（06 §3.4）：逐笔结果只由这些输入决定。"""
    mask_token: tuple[object, ...] = (
        ()
        if not trade_masks
        else tuple(
            sorted(
                (name, id(mask), int(mask.shape[0]), int(np.count_nonzero(mask)))
                for name, mask in trade_masks.items()
            )
        )
    )
    rule_token = tuple(
        sorted(
            (name, item.tick_size, item.step_size, item.min_notional)
            for name, item in rules.items()
        )
    )
    return (
        _data_token(prepared),
        scen.name,
        scen.latency_ms,
        scen.slip_mult,
        scen.half_spread_mult,
        scen.fixed_slip,
        scen.tp_taker,
        scen.all_taker,
        scen.half_spread_by_symbol,
        exit_cfg.model_dump_json(),
        k_sl,
        sl_min,
        sl_max,
        H_label,
        sizing_cfg.initial_equity * sizing_cfg.position_frac * float(sizing_cfg.leverage),
        fees.maker,
        fees.taker,
        mask_token,
        tuple(sorted(new_entry_symbols)) if new_entry_symbols else (),
        rule_token,
        tuple(sorted(groups.items())),
    )


def _cached_precompute(
    prepared: Mapping[str, PreparedSymbol],
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
    trade_masks: dict[str, np.ndarray] | None,
    new_entry_symbols: set[str] | None,
    p_floor: float,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """取（必要时计算）p ≥ p_floor 的逐笔预计算结果（06 §3.4）。"""
    key = (
        p_floor,
        *_precompute_key(
            prepared,
            rules,
            groups,
            scen=scen,
            fees=fees,
            sizing_cfg=sizing_cfg,
            exit_cfg=exit_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=H_label,
            trade_masks=trade_masks,
            new_entry_symbols=new_entry_symbols,
        ),
    )
    cached = _PRECOMPUTE_CACHE.get(key)
    if cached is None:
        cached = precompute_trades_all(
            prepared,
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
            p_min=p_floor,
        )
        _PRECOMPUTE_CACHE[key] = cached
        while len(_PRECOMPUTE_CACHE) > _PRECOMPUTE_CACHE_LIMIT:
            _PRECOMPUTE_CACHE.pop(next(iter(_PRECOMPUTE_CACHE)))
    return cached


def _filter_by_threshold(
    cached: tuple[list[PrecomputedTrade], list[SkippedSignal]],
    theta_offset: float = 0.0,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """从 p ≥ 0 的逐笔结果里取出 `p ≥ 该行 θ + offset` 的子集（06 §3.4、§7 S4）。

    阈值按行取：五折各用自己的 θ（06 §8）；S4 的阈值扰动是对每行 θ 统一加减
    `threshold_perturb`，即 `offset = ±Δ`。
    """
    trades = [trade for trade in cached[0] if trade.p >= trade.theta + theta_offset]
    skipped = [item for item in cached[1] if item.p >= item.theta + theta_offset]
    return trades, skipped


def run_backtest(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame],
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
    p_floor: float | None = None,
    theta_offset: float = 0.0,
) -> tuple[PortfolioResult, list[PrecomputedTrade]]:
    """运行单个成本情景的回测（06 §3.4）：预计算 + 组合模拟。

    同一进程内，逐笔预计算按输入指纹缓存：先用 `p_floor` 算一次，再按每行 θ 过滤，
    因此不同阈值/情景/仓位的重复运行不再重算逐笔出场（06 §3.4）。

    Args:
        bars_by_symbol: 币种 → `PreparedSymbol`（推荐）或分钟 bars。
        trade_masks: 可选，alias -> 该币每分钟是否在当月交易范围的 bool 数组（06 §3.1
            条件 8）。缺省 None 表示无宇宙约束（测试/旧调用兼容）。
        new_entry_symbols: 可选，新进入组 alias 集合（03 §4.5、06 §7 S10）。
        p_floor: 逐笔预计算的 p 下限；None 表示取 `signals` 中最小 θ。
            需要一次预计算覆盖多个 θ 时，传入这些 θ 的最小值。
        theta_offset: 对每行 θ 的统一扰动（06 §7 S4 用 ±`threshold_perturb`）。
    """
    scen = scenario_override if scenario_override is not None else scenario(scen_name)
    prepared = _as_prepared(bars_by_symbol)
    theta_floor = float(signals["theta"].min())
    floor = theta_floor if p_floor is None else min(p_floor, theta_floor)
    cached = _cached_precompute(
        prepared,
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
        p_floor=floor,
    )
    all_trades, all_skipped = _filter_by_threshold(cached, theta_offset)
    result = simulate_portfolio(
        all_trades,
        all_skipped,
        sizing_cfg=sizing_cfg,
        backtest_cfg=backtest_cfg,
        fees=fees,
        initial_equity=initial_equity,
        bars_by_symbol=prepared,
    )
    return result, all_trades
