"""轮动策略数据准备：1m 聚合 1h/4h、月度交易范围与 1m 数据块。

规格：docs/11-轮动策略.md §3

所有读取都受“不得读取评估区间之后数据”的守卫约束：`read_month_bars` 与
`trade.data.loader.load_bars` 使用同一条判定（`end_ms > holdout_start_ms` 且
`allow_holdout=False` 时报错），调用方不得绕过。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.common.funding import funding_settlements
from trade.data.loader import load_universe_snapshot
from trade.data.universe import UniverseConfig

# 毫秒常量（UTC 对齐用，03 §1.2）
MS_MIN = 60_000
MS_HOUR = 3_600_000
MS_4H = 14_400_000
MS_DAY = 86_400_000

# 分钟数据读取列：1m 块（模拟用）与面板（小时聚合用）
MINUTE_SIM_COLUMNS = ("open_time", "open", "low", "close", "p_1s", "quote_volume")
MINUTE_PANEL_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "quote_volume",
    "oi",
    "funding_rate",
)


def month_start_ms(year: int, month: int) -> int:
    """返回某 UTC 月 1 日 00:00 的毫秒时间戳。"""
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """返回 (year, month) 偏移 delta 个月后的年月。"""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def month_key(ts_ms: int) -> str:
    """返回时间戳所属 UTC 月的 "YYYY-MM"。"""
    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}"


def month_bounds(month: str) -> tuple[int, int]:
    """返回某 UTC 月的 [起点, 下月起点) 毫秒时间戳。"""
    year, month_num = (int(part) for part in month.split("-"))
    ny, nm = shift_month(year, month_num, 1)
    return month_start_ms(year, month_num), month_start_ms(ny, nm)


def month_keys(start_ms: int, end_ms: int) -> list[str]:
    """返回覆盖 [start_ms, end_ms) 的 UTC 月列表。"""
    first = month_key(start_ms)
    last = month_key(end_ms - 1)
    year, month_num = (int(part) for part in first.split("-"))
    out: list[str] = []
    while True:
        key = f"{year:04d}-{month_num:02d}"
        out.append(key)
        if key == last:
            break
        year, month_num = shift_month(year, month_num, 1)
    return out


def hour_grid(start_ms: int, end_ms: int) -> np.ndarray:
    """返回 [start_ms, end_ms) 的 UTC 整点网格（毫秒）。"""
    first = start_ms - start_ms % MS_HOUR
    return np.arange(first, end_ms, MS_HOUR, dtype="int64")


def read_month_bars(
    data_root: Path,
    alias: str,
    month: str,
    *,
    columns: tuple[str, ...],
    holdout_start_ms: int,
    allow_holdout: bool = False,
) -> pd.DataFrame | None:
    """读取某币某月的分钟数据（只读该月文件，按列裁剪）。

    Args:
        data_root: 数据根目录。
        alias: 币种别名（如 C001）。
        month: UTC 月 "YYYY-MM"。
        columns: 需要读取的列。
        holdout_start_ms: 封存集起点（M23 起点）。
        allow_holdout: 是否允许读取封存集（只有 `--stage final` 为 True）。

    Returns:
        该月分钟数据（按 open_time 升序）；文件不存在时返回 None。

    Raises:
        ValueError: 请求越过封存集起点且 `allow_holdout=False`。
    """
    start, end = month_bounds(month)
    if not allow_holdout and end > holdout_start_ms:
        raise ValueError(
            f"请求范围越过封存集起点（{month}，end_ms={end} > holdout_start_ms="
            f"{holdout_start_ms}），且 allow_holdout=False（11 §3）"
        )
    path = data_root / "bars" / alias / f"{month.replace('-', '')}.parquet"
    if not path.is_file():
        return None
    frame = pd.read_parquet(path, columns=list(columns))
    return frame.sort_values("open_time", kind="stable").reset_index(drop=True)


def _bucket_aggregate(
    bucket: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    qv: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """把分钟序列按桶聚合为 OHLC + 成交额（桶内首开、末收、最高、最低、求和）。

    Args:
        bucket: 非降序的桶编号（毫秒时间戳整除桶长）。
        open_/high/low/close: 分钟 OHLC。
        qv: 分钟成交额。

    Returns:
        (桶起点毫秒, open, high, low, close, quote_volume)。
    """
    uniq, first = np.unique(bucket, return_index=True)
    last = np.searchsorted(bucket, uniq, side="right") - 1
    return (
        uniq.astype("int64"),
        open_[first],
        np.maximum.reduceat(high, first),
        np.minimum.reduceat(low, first),
        close[last],
        np.add.reduceat(qv, first),
    )


def _bucket_last(bucket: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按桶取最后一个值（用于 oi / funding 的小时对齐）。"""
    uniq, first = np.unique(bucket, return_index=True)
    last = np.searchsorted(bucket, uniq, side="right") - 1
    return uniq.astype("int64"), values[last]


def _atr_series(
    bar_ms: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
) -> np.ndarray:
    """ATR：TR 的 period 期简单均值（11 §5.3）。"""
    if bar_ms.shape[0] == 0:
        return np.empty(0, dtype="float64")
    prev_close = np.r_[np.nan, close[:-1]]
    true_range = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(prev_close - low)]
    )
    atr = np.full(bar_ms.shape[0], np.nan)
    if bar_ms.shape[0] < period:
        return atr
    kernel = np.ones(period) / period
    sums = np.convolve(np.nan_to_num(true_range, nan=0.0), kernel, mode="valid")
    counts = np.convolve(np.isfinite(true_range).astype("float64"), kernel, mode="valid")
    atr[period - 1 :] = np.where(counts > 1.0 - 1e-9, sums / np.maximum(counts, 1e-9), np.nan)
    return atr


@dataclass
class SymbolSeries:
    """单币的 ATR 序列与资金费结算事件。"""

    symbol: str
    alias: str
    atr_ms: np.ndarray
    atr: np.ndarray
    funding_times: np.ndarray
    funding_rates: np.ndarray


@dataclass
class HourlyPanel:
    """全币小时面板（按 UTC 整点对齐，缺数据为 NaN）。"""

    hour_ms: np.ndarray
    symbols: tuple[str, ...]
    row: dict[str, int]
    close: np.ndarray
    qv: np.ndarray
    oi: np.ndarray
    funding: np.ndarray
    series: dict[str, SymbolSeries]
    btc_row: int

    def hour_index(self, ts_ms: int) -> int:
        """返回某毫秒时间戳所属整点在网格中的下标（越界返回 -1）。"""
        offset = (ts_ms - ts_ms % MS_HOUR) - int(self.hour_ms[0])
        idx = offset // MS_HOUR
        if idx < 0 or idx >= self.hour_ms.shape[0]:
            return -1
        return int(idx)

    def atr_at(self, symbol: str, ts_ms: int) -> float:
        """返回 ts_ms 之前已收盘的最后一根 4h K 线的 ATR（NaN 表示不可用）。"""
        series = self.series[symbol]
        if series.atr_ms.shape[0] == 0:
            return float("nan")
        # 4h K 线在 open_time + 4h 收盘；ts 时刻可用的最新一根满足 open + 4h ≤ ts
        idx = int(np.searchsorted(series.atr_ms, ts_ms - MS_4H, side="right")) - 1
        if idx < 0:
            return float("nan")
        return float(series.atr[idx])


def build_hourly_panel(
    *,
    data_root: Path,
    symbols: tuple[str, ...],
    aliases: dict[str, str],
    btc_symbol: str,
    btc_alias: str,
    hours: np.ndarray,
    months: list[str],
    holdout_start_ms: int,
    allow_holdout: bool,
    atr_period: int,
) -> HourlyPanel:
    """构建小时面板与 ATR / 资金费序列（读取预热月到评估月末）。

    Args:
        data_root: 数据根目录。
        symbols: 需要加载的 symbol（评估窗口内各月交易范围并集）。
        aliases: symbol -> 别名（data/universe/aliases.json）。
        btc_symbol: 大盘基准 symbol。
        btc_alias: 大盘基准的 bars 目录名。
        hours: 整点网格（`hour_grid` 的返回值）。
        months: 需要读取的 UTC 月列表（含预热月）。
        holdout_start_ms: 封存集起点。
        allow_holdout: 是否允许读封存集。
        atr_period: ATR 周期（4h 根数）。

    Returns:
        HourlyPanel。

    Raises:
        ValueError: 某 symbol 在整个窗口没有任何分钟数据。
    """
    all_symbols = symbols if btc_symbol in symbols else (*symbols, btc_symbol)
    n_sym = len(all_symbols)
    n_hour = hours.shape[0]
    close = np.full((n_sym, n_hour), np.nan)
    qv = np.full((n_sym, n_hour), np.nan)
    oi = np.full((n_sym, n_hour), np.nan)
    funding = np.full((n_sym, n_hour), np.nan)
    row = {symbol: i for i, symbol in enumerate(all_symbols)}
    hour0 = int(hours[0])
    series: dict[str, SymbolSeries] = {}
    for symbol in all_symbols:
        alias = btc_alias if symbol == btc_symbol else aliases[symbol]
        r = row[symbol]
        heads: list[np.ndarray] = []
        highs: list[np.ndarray] = []
        lows: list[np.ndarray] = []
        closes: list[np.ndarray] = []
        ft_all: list[np.ndarray] = []
        fr_all: list[np.ndarray] = []
        loaded = False
        for month in months:
            frame = read_month_bars(
                data_root,
                alias,
                month,
                columns=MINUTE_PANEL_COLUMNS,
                holdout_start_ms=holdout_start_ms,
                allow_holdout=allow_holdout,
            )
            if frame is None or frame.empty:
                continue
            loaded = True
            times = frame["open_time"].to_numpy(dtype="int64")
            open_ = frame["open"].to_numpy(dtype="float64")
            high = frame["high"].to_numpy(dtype="float64")
            low = frame["low"].to_numpy(dtype="float64")
            closes_m = frame["close"].to_numpy(dtype="float64")
            quote = frame["quote_volume"].to_numpy(dtype="float64")
            hourly = _bucket_aggregate(times // MS_HOUR, open_, high, low, closes_m, quote)
            idx = hourly[0] - hour0 // MS_HOUR
            inside = (idx >= 0) & (idx < n_hour)
            if inside.any():
                close[r, idx[inside]] = hourly[4][inside]
                qv[r, idx[inside]] = hourly[5][inside]
                oi[r, idx[inside]] = _bucket_last(
                    times // MS_HOUR, frame["oi"].to_numpy(dtype="float64")
                )[1][inside]
                funding[r, idx[inside]] = _bucket_last(
                    times // MS_HOUR, frame["funding_rate"].to_numpy(dtype="float64")
                )[1][inside]
            four = _bucket_aggregate(times // MS_4H, open_, high, low, closes_m, quote)
            heads.append(four[0] * MS_4H)
            highs.append(four[2])
            lows.append(four[3])
            closes.append(four[4])
            ft, fr = funding_settlements(frame)
            ft_all.append(ft)
            fr_all.append(fr)
        if not loaded:
            raise ValueError(f"币种 {symbol}（{alias}）在窗口内没有任何分钟数据（11 §3）")
        atr_ms = np.concatenate(heads) if heads else np.empty(0, dtype="int64")
        atr = _atr_series(
            atr_ms,
            np.concatenate(highs) if highs else np.empty(0),
            np.concatenate(lows) if lows else np.empty(0),
            np.concatenate(closes) if closes else np.empty(0),
            atr_period,
        )
        series[symbol] = SymbolSeries(
            symbol=symbol,
            alias=alias,
            atr_ms=atr_ms,
            atr=atr,
            funding_times=np.concatenate(ft_all) if ft_all else np.empty(0, dtype="int64"),
            funding_rates=np.concatenate(fr_all) if fr_all else np.empty(0, dtype="float64"),
        )
    return HourlyPanel(
        hour_ms=hours,
        symbols=all_symbols,
        row=row,
        close=close,
        qv=qv,
        oi=oi,
        funding=funding,
        series=series,
        btc_row=row[btc_symbol],
    )


@dataclass
class MinuteChunk:
    """某 UTC 月的分钟数据块（行 = 币，列 = 分钟）。"""

    month: str
    minute_ms: np.ndarray
    row: dict[str, int]
    low: np.ndarray
    open: np.ndarray
    close: np.ndarray
    p1s: np.ndarray
    qv: np.ndarray

    def index_of(self, ts_ms: int) -> int:
        """返回某毫秒时间戳在该月块中的分钟下标（越界返回 -1）。"""
        return int((ts_ms - int(self.minute_ms[0])) // MS_MIN)


def build_minute_chunk(
    *,
    data_root: Path,
    month: str,
    symbols: tuple[str, ...],
    aliases: dict[str, str],
    holdout_start_ms: int,
    allow_holdout: bool,
) -> MinuteChunk:
    """读取某月分钟块（只含模拟所需列，缺数据的分钟为 NaN）。

    Args:
        data_root: 数据根目录。
        month: UTC 月 "YYYY-MM"。
        symbols: 该月需要的 symbol（本月与相邻月交易范围的并集）。
        aliases: symbol -> 别名。
        holdout_start_ms: 封存集起点。
        allow_holdout: 是否允许读封存集。

    Returns:
        MinuteChunk。

    Raises:
        ValueError: 所有 symbol 都没有该月数据。
    """
    start, end = month_bounds(month)
    n_min = (end - start) // MS_MIN
    low = np.full((len(symbols), n_min), np.nan)
    open_ = np.full((len(symbols), n_min), np.nan)
    close = np.full((len(symbols), n_min), np.nan)
    p1s = np.full((len(symbols), n_min), np.nan)
    qv = np.full((len(symbols), n_min), np.nan)
    row: dict[str, int] = {}
    for symbol in symbols:
        alias = aliases.get(symbol)
        if alias is None:
            raise ValueError(f"别名缺失：{symbol}（data/universe/aliases.json，11 §3）")
        frame = read_month_bars(
            data_root,
            alias,
            month,
            columns=MINUTE_SIM_COLUMNS,
            holdout_start_ms=holdout_start_ms,
            allow_holdout=allow_holdout,
        )
        if frame is None or frame.empty:
            continue
        idx = ((frame["open_time"].to_numpy(dtype="int64") - start) // MS_MIN).astype("int64")
        inside = (idx >= 0) & (idx < n_min)
        if not inside.any():
            continue
        r = len(row)
        row[symbol] = r
        cols = idx[inside]
        low[r, cols] = frame["low"].to_numpy(dtype="float64")[inside]
        open_[r, cols] = frame["open"].to_numpy(dtype="float64")[inside]
        close[r, cols] = frame["close"].to_numpy(dtype="float64")[inside]
        p1s[r, cols] = frame["p_1s"].to_numpy(dtype="float64")[inside]
        qv[r, cols] = frame["quote_volume"].to_numpy(dtype="float64")[inside]
    if not row:
        raise ValueError(f"{month} 没有任何分钟数据（11 §3）")
    return MinuteChunk(
        month=month,
        minute_ms=np.arange(start, end, MS_MIN, dtype="int64"),
        row=row,
        low=low,
        open=open_,
        close=close,
        p1s=p1s,
        qv=qv,
    )


@dataclass(frozen=True)
class UniverseMonth:
    """某月交易范围与“新进入延迟”的解锁时刻。"""

    month: str
    symbols: frozenset[str]
    unlock_ms: dict[str, int]


def load_universe_calendar(
    *, data_root: Path, universe_cfg: UniverseConfig, months: list[str]
) -> dict[str, UniverseMonth]:
    """读取各月交易范围快照，并计算新进入币的解锁时刻（03 §4.2、11 §4）。

    Args:
        data_root: 数据根目录。
        universe_cfg: configs/universe.yaml（只读 `rule.new_entry_delay_days`）。
        months: UTC 月列表（升序）。

    Returns:
        month -> UniverseMonth；`unlock_ms` 中未出现的币可以立即买入。

    Raises:
        ValueError: 快照未标记 `reviewed: true`。
    """
    if not universe_cfg.reviewed:
        raise ValueError("configs/universe.yaml 的 reviewed 不为 true，不得用于回测（03 §4.6）")
    delay_ms = universe_cfg.rule.new_entry_delay_days * MS_DAY
    out: dict[str, UniverseMonth] = {}
    prev: frozenset[str] | None = None
    for month in months:
        snapshot = load_universe_snapshot(data_root, month)
        current = frozenset(coin.symbol for coin in snapshot.coins)
        previous = prev if prev is not None else frozenset()
        unlock = {symbol: snapshot.rebalance_ms + delay_ms for symbol in current - previous}
        out[month] = UniverseMonth(month=month, symbols=current, unlock_ms=unlock)
        prev = current
    return out
