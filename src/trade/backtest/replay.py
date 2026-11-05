"""S8 逐笔成交重放（K 线级回测 vs 逐笔级执行）。

规格：docs/06-回测引擎.md §7 S8、§9 A5/A6；docs/07-实盘与风控.md §5.2

逐笔重放只替换“持仓期间的判定精度”，其余全部复用回测的同一套实现：入场价与成本
（`trade.common.entry`）、出场参数与收盘状态机（`trade.common.exits`）、成本情景
（`trade.backtest.costs`）、交易规则取整（`trade.common.exits.floor_tick` /
`ceil_tick`）。不复制出场逻辑（AGENTS.md §6 红线 4）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.costs import FeeSchedule, Scenario
from trade.backtest.engine import PrecomputedTrade
from trade.common.exits import (
    EXIT_TYPE_NAMES,
    ExitConfig,
    exit_mode,
    initial_stop,
    min_stop_move,
    take_profit_price,
    update_stop_after_bar,
)
from trade.data.aggregate import read_aggtrades_zip

# 改单窗口：新止损单挂出并确认后旧止损单才撤销，窗口内旧止损仍然有效（07 §5.2）。
ORDER_WINDOW_MS = 1000
# S8 通过标准（06 §7 S8）。
S8_NET_DIFF_MAX = 0.15
S8_TYPE_MATCH_MIN = 0.90

_BAR_MS = 60_000
# 净收益差异的分母下限（USDT）：K 线级净收益近似为 0 时改用绝对差判定（06 §7 S8）。
_NET_DENOM_EPS = 1e-6

# 出场类型编码（与 trade.common.exits 一致）
_EXIT_INITIAL_STOP = 0
_EXIT_TP = 3
_EXIT_STALL = 4
_EXIT_TIMEOUT = 5
_EXIT_EOD = 6


@dataclass(frozen=True)
class TickArrays:
    """一段按时间升序的逐笔成交（毫秒时间戳 + 成交价）。"""

    time_ms: npt.NDArray[np.int64]
    price: npt.NDArray[np.float64]

    def __len__(self) -> int:
        """逐笔条数。"""
        return int(self.time_ms.shape[0])


@dataclass(frozen=True)
class BarsArrays:
    """逐笔重放需要的 K 线数组（06 §3.3）。"""

    open_time: npt.NDArray[np.int64]
    p_entry: npt.NDArray[np.float64]
    close: npt.NDArray[np.float64]
    is_filled: npt.NDArray[np.int64]

    @classmethod
    def from_frame(cls, bars: pd.DataFrame, entry_price_col: str) -> BarsArrays:
        """从分钟 bars 构造（`entry_price_col` 为 `p_1s` 或 `p_3s`）。"""
        if entry_price_col not in bars.columns:
            raise ValueError(f"bars 缺少入场参考价列：{entry_price_col}")
        is_filled = (
            bars["is_filled"].to_numpy(dtype="int64")
            if "is_filled" in bars.columns
            else np.zeros(len(bars), dtype="int64")
        )
        return cls(
            open_time=bars["open_time"].to_numpy(dtype="int64"),
            p_entry=bars[entry_price_col].to_numpy(dtype="float64"),
            close=bars["close"].to_numpy(dtype="float64"),
            is_filled=is_filled,
        )

    @property
    def n(self) -> int:
        """K 线根数。"""
        return int(self.open_time.shape[0])

    def index_of(self, open_time_ms: int) -> int:
        """按 `open_time` 查 K 线下标。

        Raises:
            ValueError: 该分钟不在 bars 中（数据不完整，离线流程直接失败）。
        """
        idx = int(np.searchsorted(self.open_time, open_time_ms))
        if idx >= self.n or int(self.open_time[idx]) != open_time_ms:
            raise ValueError(f"bars 中找不到 open_time={open_time_ms} 的 K 线")
        return idx


@dataclass(frozen=True)
class ReplayResult:
    """单笔交易的逐笔重放结果。"""

    symbol: str
    entry_open_time: int
    exit_time_ms: int
    exit_price: float
    exit_type: str
    exit_type_code: int
    stop_updates: int
    bars_held: int
    exit_fee_rate: float
    entry_price_check_rel: float  # 逐笔重算的 p_1s 与 bars 的相对误差（校验入场口径）
    old_stop_window_hit: bool  # 是否在“新止损生效前”的窗口里由旧止损触发
    n_ticks: int


def load_day_ticks(path: Path) -> TickArrays:
    """读取某币某 UTC 日的 aggTrades zip（复用 03 §2.2 的流式读取）。

    Args:
        path: `{symbol}-aggTrades-YYYY-MM-DD.zip`。

    Returns:
        按 `transact_time` 升序的逐笔时间与成交价。

    Raises:
        ValueError: 逐笔时间非单调递增。
    """
    times: list[npt.NDArray[np.int64]] = []
    prices: list[npt.NDArray[np.float64]] = []
    for chunk in read_aggtrades_zip(path):
        if chunk.empty:
            continue
        times.append(chunk["transact_time"].to_numpy(dtype="int64"))
        prices.append(chunk["price"].to_numpy(dtype="float64"))
    if not times:
        return TickArrays(np.empty(0, dtype="int64"), np.empty(0, dtype="float64"))
    time_ms = np.concatenate(times)
    price = np.concatenate(prices)
    if np.any(np.diff(time_ms) < 0):
        raise ValueError(f"aggTrades 时间非单调递增：{path}")
    return TickArrays(time_ms, price)


TickLoader = Callable[[str], TickArrays]


def _exit_price_from_bars(
    bars: BarsArrays,
    exit_idx: int,
    *,
    half_spread: float,
    slip_exit: float,
) -> tuple[int, float, bool]:
    """无进展/超时/数据末尾的离场价（06 §3.3，与 K 线级完全同一口径）。

    Returns:
        `(出场 K 线下标, 出场价, 是否为数据末尾 eod)`。
    """
    if exit_idx < bars.n:
        price = float(bars.p_entry[exit_idx]) * (1.0 - half_spread - slip_exit)
        return exit_idx, price, False
    return bars.n - 1, float(bars.close[bars.n - 1]), True


def replay_trade(
    bars: BarsArrays,
    trade: PrecomputedTrade,
    ticks: TickArrays,
    *,
    exit_cfg: ExitConfig,
    fees: FeeSchedule,
    scen: Scenario,
    entry_delay_ms: int = ORDER_WINDOW_MS,
) -> ReplayResult:
    """按逐笔顺序重放单笔交易的持仓期（06 §7 S8）。

    规则与 K 线级一致，只有两点不同：①止损/止盈按逐笔成交价的时间顺序判定，因此同一
    根 K 线内先到止损还是先到止盈由真实顺序决定（§9 A5）；②收盘算出的新止损在
    `收盘 + entry_delay_ms` 之后才生效，窗口内仍由旧止损判定（§9 A6、07 §5.2）。

    Args:
        bars: 该币分钟 K 线数组（含 `p_1s`/`p_3s` 与 `is_filled`）。
        trade: K 线级预计算结果，提供 `p_fill`/`sl`/`atr_abs` 与成本参数。
        ticks: 覆盖 `[入场 K 线起点, 出场上界]` 的逐笔序列（可跨日拼接）。
        exit_cfg: 出场策略配置。
        fees: 费率。
        scen: 成本情景（决定止盈是否按吃单费率）。
        entry_delay_ms: 入场延迟（毫秒），与 06 §3.2 的 `p_1s`/`p_3s` 一致。

    Returns:
        ReplayResult。

    Raises:
        ValueError: 入场 K 线不在 bars 中，或逐笔数据为空。
    """
    t_entry = bars.index_of(trade.entry_open_time)
    h_max = trade.h_max if trade.h_max > 0 else exit_cfg.H_max
    mode = exit_mode(exit_cfg)
    r_dist = trade.p_fill * trade.sl
    stop = initial_stop(trade.p_fill, trade.sl, trade.tick_size)
    cap = take_profit_price(trade.p_fill, trade.sl, exit_cfg, trade.tick_size)
    min_move = min_stop_move(trade.tick_size, exit_cfg.min_stop_move_r, r_dist)
    entry_bar_start = int(bars.open_time[t_entry])
    if len(ticks) == 0:
        raise ValueError(f"逐笔数据为空（{trade.symbol}@{entry_bar_start}）")
    tick_start = int(np.searchsorted(ticks.time_ms, entry_bar_start + entry_delay_ms, side="right"))
    # tick_start == 0 表示该日首笔成交已晚于入场延迟：从第一笔开始重放，并把入场价
    # 校验记为 NaN（bars 的 p_1s 此时回退为上一分钟收盘价，见 03 §2.2）。
    entry_check = (
        abs(float(ticks.price[tick_start - 1]) / float(bars.p_entry[t_entry]) - 1.0)
        if tick_start > 0
        else float("nan")
    )

    phase = _EXIT_INITIAL_STOP
    pending_stop = float("nan")
    pending_ms = 0
    stop_updates = 0
    hh = float("-inf")
    made_progress = False
    old_stop_window_hit = False
    pos = tick_start
    exit_idx = -1
    exit_price = float("nan")
    exit_type = _EXIT_EOD
    exit_time_ms = entry_bar_start
    exited = False

    j = t_entry
    while j < t_entry + h_max and not exited:
        if j >= bars.n:
            exit_idx = bars.n - 1
            exit_price = float(bars.close[bars.n - 1])
            exit_type = _EXIT_EOD
            exit_time_ms = int(bars.open_time[exit_idx]) + _BAR_MS
            exited = True
            break
        bar_end = int(bars.open_time[j]) + _BAR_MS
        if bars.is_filled[j] == 1:
            # 补齐分钟不判定（06 §3.3），但时间仍在推进：丢弃该分钟的逐笔后继续。
            while pos < len(ticks) and int(ticks.time_ms[pos]) < bar_end:
                pos += 1
            if not math.isnan(pending_stop) and bar_end >= pending_ms:
                stop = pending_stop
                pending_stop = float("nan")
                stop_updates += 1
            j += 1
            continue
        while pos < len(ticks) and int(ticks.time_ms[pos]) < bar_end:
            tick_ms = int(ticks.time_ms[pos])
            price = float(ticks.price[pos])
            if not math.isnan(pending_stop) and tick_ms >= pending_ms:
                stop = pending_stop
                pending_stop = float("nan")
                stop_updates += 1
            if price > hh:
                hh = price
            if price <= stop:
                exit_price = price * (1.0 - trade.slip_stop)
                exit_type = phase
                exit_idx = j
                exit_time_ms = tick_ms
                old_stop_window_hit = not math.isnan(pending_stop)
                exited = True
                break
            if price >= cap * (1.0 + exit_cfg.tp_penetration):
                exit_price = cap
                exit_type = _EXIT_TP
                exit_idx = j
                exit_time_ms = tick_ms
                exited = True
                break
            pos += 1
        if exited:
            break
        if not math.isnan(pending_stop) and bar_end >= pending_ms:
            stop = pending_stop
            pending_stop = float("nan")
            stop_updates += 1
        held_bars = j - t_entry + 1
        new_stop, phase, _, progress = update_stop_after_bar(
            stop,
            phase,
            hh,
            trade.p_fill,
            r_dist,
            trade.atr_abs,
            trade.tick_size,
            min_move,
            held_bars,
            mode,
            exit_cfg.be_trigger_r,
            exit_cfg.be_offset,
            exit_cfg.trail_trigger_r,
            exit_cfg.k_trail,
            exit_cfg.stall_bars,
            exit_cfg.stall_r,
        )
        if new_stop > stop:
            # 新止损 1 秒后才生效（07 §5.2），窗口内旧止损仍在。
            pending_stop = new_stop
            pending_ms = bar_end + ORDER_WINDOW_MS
        made_progress = made_progress or progress
        if exit_cfg.type == "trail" and held_bars == exit_cfg.stall_bars and not made_progress:
            exit_idx = j + 1
            exit_type = _EXIT_STALL
            exited = True
            break
        j += 1

    if not exited:
        exit_idx = t_entry + h_max
        exit_type = _EXIT_TIMEOUT

    if exit_type in (_EXIT_STALL, _EXIT_TIMEOUT, _EXIT_EOD):
        resolved_idx, exit_price, is_eod = _exit_price_from_bars(
            bars,
            exit_idx,
            half_spread=trade.half_spread,
            slip_exit=trade.slip_exit,
        )
        exit_idx = resolved_idx
        if is_eod:
            exit_type = _EXIT_EOD
        exit_time_ms = int(bars.open_time[exit_idx]) + (_BAR_MS if is_eod else 0)

    if scen.all_taker or (exit_type == _EXIT_TP and scen.tp_taker):
        exit_fee_rate = fees.taker
    elif exit_type == _EXIT_TP:
        exit_fee_rate = fees.maker
    else:
        exit_fee_rate = fees.taker

    return ReplayResult(
        symbol=trade.symbol,
        entry_open_time=trade.entry_open_time,
        exit_time_ms=exit_time_ms,
        exit_price=exit_price,
        exit_type=EXIT_TYPE_NAMES[int(exit_type)],
        exit_type_code=int(exit_type),
        stop_updates=stop_updates,
        bars_held=exit_idx - t_entry + 1,
        exit_fee_rate=exit_fee_rate,
        entry_price_check_rel=entry_check,
        old_stop_window_hit=old_stop_window_hit,
        n_ticks=len(ticks),
    )


def trade_net_return(
    gross_return: float,
    *,
    entry_fee_rate: float,
    exit_fee_rate: float,
    funding_rate_sum: float,
) -> float:
    """单笔净收益率（比例）：毛收益 − 入场费 − 出场费 − 资金费（06 §4.1、§3.3）。"""
    return gross_return - entry_fee_rate - exit_fee_rate - funding_rate_sum


def bar_level_net_return(trade: PrecomputedTrade, *, entry_fee_rate: float) -> float:
    """K 线级单笔净收益率（06 §3.4 第 1 步结果 + 成本）。"""
    return trade_net_return(
        trade.gross_return,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=trade.exit_fee_rate,
        funding_rate_sum=trade.funding_rate_sum,
    )


def tick_level_net_return(
    trade: PrecomputedTrade, replay: ReplayResult, *, entry_fee_rate: float
) -> float:
    """逐笔级单笔净收益率（入场价沿用 `trade.p_fill`，出场价来自逐笔重放）。"""
    return trade_net_return(
        replay.exit_price / trade.p_fill - 1.0,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=replay.exit_fee_rate,
        funding_rate_sum=trade.funding_rate_sum,
    )


def relative_net_diff(bar_net: float, tick_net: float) -> float:
    """净收益相对差异 `|逐笔 − K 线级| / |K 线级|`（06 §7 S8）。

    K 线级净收益绝对值小于 1e-6 USDT 时改用绝对差判定（两者都为 0 记 0，否则记 1），
    避免分母接近 0 时出现无意义的放大。
    """
    if abs(bar_net) < _NET_DENOM_EPS:
        return 0.0 if abs(tick_net - bar_net) < _NET_DENOM_EPS else 1.0
    return abs(tick_net - bar_net) / abs(bar_net)


@dataclass(frozen=True)
class S8Summary:
    """S8 汇总结果（06 §7 S8）。"""

    month: str
    symbols: tuple[str, ...]
    n_trades: int
    bar_net_usdt: float
    tick_net_usdt: float
    net_diff: float
    type_match_rate: float
    old_stop_window_hits: int
    per_symbol: dict[str, dict[str, float]]

    @property
    def net_diff_passed(self) -> bool:
        """净收益差异是否小于 15%。"""
        return self.net_diff < S8_NET_DIFF_MAX

    @property
    def type_match_passed(self) -> bool:
        """出场类型一致率是否 ≥ 90%。"""
        return self.type_match_rate >= S8_TYPE_MATCH_MIN

    @property
    def passed(self) -> bool:
        """S8 是否通过（两项同时满足）。"""
        return self.net_diff_passed and self.type_match_passed


def compare_s8(
    month: str,
    pairs: Sequence[tuple[PrecomputedTrade, ReplayResult]],
    *,
    entry_fee_rate: float,
) -> S8Summary:
    """汇总 K 线级与逐笔级的净收益差异与出场类型一致率（06 §7 S8）。

    两侧使用同一参考名义价值（`PrecomputedTrade.notional_ref`）与同一入场价，因此差异
    完全来自持仓期执行细节。样本是候选信号的预计算集（未经过组合约束），与
    `precompute_trades` 的口径一致。

    Args:
        month: 重放月份 `YYYY-MM`。
        pairs: `(K 线级预计算, 逐笔重放结果)` 列表。
        entry_fee_rate: 入场吃单费率（06 §4.1）。

    Returns:
        S8Summary。
    """
    per_symbol: dict[str, dict[str, float]] = {}
    bar_total = 0.0
    tick_total = 0.0
    matches = 0
    window_hits = 0
    symbols: list[str] = []
    for trade, replay in pairs:
        notional = trade.notional_ref
        bar_net = notional * bar_level_net_return(trade, entry_fee_rate=entry_fee_rate)
        tick_net = notional * tick_level_net_return(trade, replay, entry_fee_rate=entry_fee_rate)
        bar_total += bar_net
        tick_total += tick_net
        matched = replay.exit_type == trade.exit_type
        matches += 1 if matched else 0
        window_hits += 1 if replay.old_stop_window_hit else 0
        if trade.symbol not in symbols:
            symbols.append(trade.symbol)
        stats = per_symbol.setdefault(
            trade.symbol,
            {"n_trades": 0.0, "bar_net": 0.0, "tick_net": 0.0, "net_diff": 0.0, "match": 0.0},
        )
        stats["n_trades"] += 1.0
        stats["bar_net"] += bar_net
        stats["tick_net"] += tick_net
        stats["match"] += 1.0 if matched else 0.0
    for stats in per_symbol.values():
        stats["net_diff"] = relative_net_diff(stats["bar_net"], stats["tick_net"])
        stats["match"] = stats["match"] / stats["n_trades"] if stats["n_trades"] else 0.0
    n_trades = len(pairs)
    return S8Summary(
        month=month,
        symbols=tuple(symbols),
        n_trades=n_trades,
        bar_net_usdt=bar_total,
        tick_net_usdt=tick_total,
        net_diff=relative_net_diff(bar_total, tick_total),
        type_match_rate=(matches / n_trades) if n_trades else 0.0,
        old_stop_window_hits=window_hits,
        per_symbol=per_symbol,
    )


def s8_status_text(summary: S8Summary) -> str:
    """S8 结果的一句话状态（写入 `gate.json` 与 `report.md`，06 §7 S8）。"""
    state = "passed" if summary.passed else "failed"
    return (
        f"{state}（净收益差异 {summary.net_diff:.1%} < {S8_NET_DIFF_MAX:.0%}；"
        f"出场类型一致率 {summary.type_match_rate:.1%} ≥ {S8_TYPE_MATCH_MIN:.0%}；"
        f"{summary.n_trades} 笔，{summary.month}，{'/'.join(summary.symbols)}）"
    )


def s8_payload(summary: S8Summary, *, run_id: str, selection_rule: str) -> dict[str, object]:
    """S8 结果的可持久化字典（`s8_replay.json`，06 §8）。"""
    return {
        "run_id": run_id,
        "month": summary.month,
        "symbols": list(summary.symbols),
        "selection_rule": selection_rule,
        "n_trades": summary.n_trades,
        "bar_net_usdt": summary.bar_net_usdt,
        "tick_net_usdt": summary.tick_net_usdt,
        "net_diff": summary.net_diff,
        "type_match_rate": summary.type_match_rate,
        "old_stop_window_hits": summary.old_stop_window_hits,
        "net_diff_max": S8_NET_DIFF_MAX,
        "type_match_min": S8_TYPE_MATCH_MIN,
        "net_diff_passed": summary.net_diff_passed,
        "type_match_passed": summary.type_match_passed,
        "passed": summary.passed,
        "status": s8_status_text(summary),
        "per_symbol": summary.per_symbol,
    }


def s8_status_from_payload(payload: dict[str, object]) -> str:
    """从 `s8_replay.json` 内容还原状态文本，格式不合法时返回 not_run 说明。"""
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "not_run（s8_replay.json 缺少 status 字段）"


def _utc_day(ts_ms: int) -> str:
    """UTC 日 `YYYY-MM-DD`（逐笔归档按日存放，03 §2.2）。"""
    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


def _day_range(first_day: str, last_day: str) -> list[str]:
    """闭区间 `[first_day, last_day]` 的 UTC 日期列表。"""
    current = date.fromisoformat(first_day)
    end = date.fromisoformat(last_day)
    days: list[str] = []
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _concat_ticks(arrays: Sequence[TickArrays]) -> TickArrays:
    """拼接相邻日期的逐笔序列（保持时间升序）。"""
    if len(arrays) == 1:
        return arrays[0]
    return TickArrays(
        time_ms=np.concatenate([array.time_ms for array in arrays]),
        price=np.concatenate([array.price for array in arrays]),
    )


def replay_month(
    bars: BarsArrays,
    trades: Sequence[PrecomputedTrade],
    *,
    tick_loader: TickLoader,
    exit_cfg: ExitConfig,
    fees: FeeSchedule,
    scen: Scenario,
    entry_delay_ms: int = ORDER_WINDOW_MS,
) -> list[tuple[PrecomputedTrade, ReplayResult]]:
    """重放某币某月全部候选交易（按入场时间升序，逐日装载逐笔数据）。

    一次只在内存里保留“当前交易可能用到的日期窗口”（入场 K 线到 `H_max` 上界，最多
    跨 2~3 个 UTC 日），避免把整月逐笔读入内存（03 §3 的 3.6 GB 内存约束）。

    Args:
        bars: 该币分钟 K 线数组。
        trades: 该币该月的 K 线级预计算交易。
        tick_loader: `YYYY-MM-DD` → 当日逐笔序列。
        exit_cfg: 出场策略配置。
        fees: 费率。
        scen: 成本情景。
        entry_delay_ms: 入场延迟（毫秒）。

    Returns:
        `[(K 线级预计算, 逐笔重放结果), ...]`，顺序与入场时间一致。

    Raises:
        ValueError: bars 缺少入场 K 线，或逐笔数据为空。
    """
    ordered = sorted(trades, key=lambda trade: trade.entry_open_time)
    day_cache: dict[str, TickArrays] = {}
    window_cache: dict[tuple[str, ...], TickArrays] = {}
    out: list[tuple[PrecomputedTrade, ReplayResult]] = []
    for trade in ordered:
        t_entry = bars.index_of(trade.entry_open_time)
        h_max = trade.h_max if trade.h_max > 0 else exit_cfg.H_max
        last_idx = min(t_entry + h_max, bars.n - 1)
        days = tuple(
            _day_range(
                _utc_day(int(bars.open_time[t_entry])),
                _utc_day(int(bars.open_time[last_idx])),
            )
        )
        if days not in window_cache:
            # 只保留当前窗口用到的日期，避免整月逐笔常驻内存（03 §3）。
            window_cache.clear()
            day_cache = {day: ticks for day, ticks in day_cache.items() if day in days}
            for day in days:
                if day not in day_cache:
                    day_cache[day] = tick_loader(day)
            window_cache[days] = _concat_ticks([day_cache[day] for day in days])
        out.append(
            (
                trade,
                replay_trade(
                    bars,
                    trade,
                    window_cache[days],
                    exit_cfg=exit_cfg,
                    fees=fees,
                    scen=scen,
                    entry_delay_ms=entry_delay_ms,
                ),
            )
        )
    return out
