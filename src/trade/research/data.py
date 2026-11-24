"""研究数据层：月度 parquet 加载、资金费、选币快照与交易规则。

规格：docs/13-研究框架.md §1
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow.parquet as pq

from trade.data.universe import SymbolMeta, UniverseSnapshot, read_universe_snapshot

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
_BAR_COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume")
_PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Bars:
    """单个币的 1h / 1d K 线数组（按 open_time 升序，UTC 毫秒）。"""

    symbol: str
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote_volume: np.ndarray

    def closed_count(self, ts_ms: int) -> int:
        """返回 `open_time + 1h <= ts_ms` 的 K 线根数（决策时刻只用已收盘的）。"""
        return int(np.searchsorted(self.open_time, ts_ms - HOUR_MS, side="right"))

    def bar_index(self, ts_ms: int) -> int | None:
        """返回 open_time == ts_ms 的 K 线下标；缺失返回 None。"""
        index = int(np.searchsorted(self.open_time, ts_ms, side="left"))
        if index < self.open_time.size and int(self.open_time[index]) == ts_ms:
            return index
        return None

    def last_index_before(self, ts_ms: int) -> int:
        """返回 open_time < ts_ms 的最后一根 K 线下标；没有则 -1。"""
        return int(np.searchsorted(self.open_time, ts_ms, side="left")) - 1


@dataclass(frozen=True)
class MarkBars:
    """标记价格 1h 数组（止损触发基准，docs/13 §1、§4）。

    只保留触发与定基准需要的列（high/low/close），减少内存占用。
    """

    symbol: str
    open_time: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def bar_index(self, ts_ms: int) -> int | None:
        """返回 open_time == ts_ms 的 K 线下标；缺失返回 None。"""
        index = int(np.searchsorted(self.open_time, ts_ms, side="left"))
        if index < self.open_time.size and int(self.open_time[index]) == ts_ms:
            return index
        return None

    def last_index_before(self, ts_ms: int) -> int:
        """返回 open_time < ts_ms 的最后一根 K 线下标；没有则 -1。"""
        return int(np.searchsorted(self.open_time, ts_ms, side="left")) - 1


@dataclass(frozen=True)
class FlowBars:
    """主动买入成交（`taker_buy_*`，docs/13 §1）。"""

    symbol: str
    open_time: np.ndarray
    taker_buy_volume: np.ndarray
    taker_buy_quote_volume: np.ndarray


@dataclass(frozen=True)
class FundingSeries:
    """资金费结算序列（每小时费率，docs/13 §1）。"""

    symbol: str
    calc_time: np.ndarray
    hourly_rate: np.ndarray
    interval_hours: np.ndarray

    def mean_between(self, start_ms: int, end_ms: int) -> float | None:
        """`start_ms < calc_time <= end_ms` 的平均每小时费率；没有结算返回 None。"""
        lo = int(np.searchsorted(self.calc_time, start_ms, side="right"))
        hi = int(np.searchsorted(self.calc_time, end_ms, side="right"))
        if hi <= lo:
            return None
        return float(np.mean(self.hourly_rate[lo:hi]))

    def settlements_between(self, start_ms: int, end_ms: int) -> tuple[np.ndarray, np.ndarray]:
        """返回 `start_ms < calc_time <= end_ms` 的 (calc_time, 每期费率)。"""
        lo = int(np.searchsorted(self.calc_time, start_ms, side="right"))
        hi = int(np.searchsorted(self.calc_time, end_ms, side="right"))
        return self.calc_time[lo:hi], (self.hourly_rate[lo:hi] * self.interval_hours[lo:hi])

    def last_calc_time(self, ts_ms: int) -> int | None:
        """`calc_time <= ts_ms` 的最后一次结算时刻；没有返回 None。"""
        index = int(np.searchsorted(self.calc_time, ts_ms, side="right")) - 1
        return int(self.calc_time[index]) if index >= 0 else None

    def median_interval_hours(self) -> float:
        """结算间隔的中位数（小时），用于判断资金费数据是否断档（docs/13 §1）。

        优先用相邻结算时刻差的中位数（真实间隔），不足两笔时退回 `interval_hours` 字段。
        """
        if self.calc_time.size >= 2:
            value = float(np.median(np.diff(self.calc_time)) / HOUR_MS)
        elif self.interval_hours.size:
            value = float(np.median(self.interval_hours))
        else:
            raise ValueError(f"{self.symbol} 的资金费序列为空（红线 12）")
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"{self.symbol} 的资金费结算间隔非法：{value}（红线 12）")
        return value


class UniverseHistory:
    """月度选币快照（`data/universe_hist/{YYYY-MM}.json`）。"""

    def __init__(self, snapshots: Mapping[str, UniverseSnapshot]) -> None:
        if not snapshots:
            raise ValueError("没有选币快照：先运行 scripts/build_universe_hist.py（docs/13 §1）")
        self._snapshots = dict(snapshots)

    def month_of(self, ts_ms: int) -> str:
        day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
        return f"{day.year:04d}-{day.month:02d}"

    def snapshot_at(self, ts_ms: int) -> UniverseSnapshot:
        month = self.month_of(ts_ms)
        if month not in self._snapshots:
            available = sorted(self._snapshots)
            raise KeyError(f"{month} 没有选币快照（可用 {available[0]}~{available[-1]}）")
        return self._snapshots[month]

    def symbols_at(self, ts_ms: int) -> frozenset[str]:
        return frozenset(coin.symbol for coin in self.snapshot_at(ts_ms).coins)

    def first_listing_ms(self, symbol: str) -> int | None:
        values = [
            coin.first_listing_ms
            for snapshot in self._snapshots.values()
            for coin in snapshot.coins
            if coin.symbol == symbol
        ]
        return min(values) if values else None

    @property
    def months(self) -> list[str]:
        return sorted(self._snapshots)


class MarketData:
    """研究期全部数据（只加载选币快照出现过的币 + BTC/ETH）。"""

    def __init__(
        self,
        *,
        hourly: Mapping[str, Bars],
        daily: Mapping[str, Bars],
        funding: Mapping[str, FundingSeries],
        universe: UniverseHistory,
        rules: Mapping[str, SymbolMeta],
        mark: Mapping[str, MarkBars] | None = None,
        flow: Mapping[str, FlowBars] | None = None,
        premium: Mapping[str, MarkBars] | None = None,
    ) -> None:
        self.hourly = dict(hourly)
        self.daily = dict(daily)
        self.funding = dict(funding)
        self.universe = universe
        self.rules = dict(rules)
        self.mark = dict(mark or {})
        self.flow = dict(flow or {})
        self.premium = dict(premium or {})

    def mark_bars(self, symbol: str) -> MarkBars | None:
        """标记价格 K 线；该币没有标记价格数据时返回 None（调用方必须计数，docs/13 §4）。"""
        return self.mark.get(symbol)

    def flow_bars(self, symbol: str) -> FlowBars | None:
        """主动买入成交；该币没有数据时返回 None。"""
        return self.flow.get(symbol)

    def premium_bars(self, symbol: str) -> MarkBars | None:
        """溢价指数 K 线；该币没有数据时返回 None。"""
        return self.premium.get(symbol)

    def bars(self, symbol: str) -> Bars:
        if symbol not in self.hourly:
            raise KeyError(f"没有 {symbol} 的 1h 数据（docs/13 §1）")
        return self.hourly[symbol]

    def daily_bars(self, symbol: str) -> Bars:
        if symbol not in self.daily:
            raise KeyError(f"没有 {symbol} 的 1d 数据（docs/13 §1）")
        return self.daily[symbol]

    def funding_series(self, symbol: str) -> FundingSeries | None:
        return self.funding.get(symbol)

    def rule(self, symbol: str) -> SymbolMeta:
        if symbol not in self.rules:
            raise KeyError(
                f"缺少 {symbol} 的交易规则（tickSize/stepSize/minNotional），"
                "先运行 scripts/snapshot_rules.py（红线 12）"
            )
        return self.rules[symbol]

    def symbols(self) -> list[str]:
        return sorted(self.hourly)


def _month_keys(start_ms: int, end_ms: int) -> list[str]:
    """返回与 [start_ms, end_ms) 有交集的 YYYY-MM 列表。"""
    start = datetime.fromtimestamp(start_ms // 1000, tz=UTC)
    end = datetime.fromtimestamp(max(start_ms, end_ms - 1) // 1000, tz=UTC)
    keys: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return keys


def load_bars_dir(root: Path, symbol: str, start_ms: int, end_ms: int) -> Bars | None:
    """加载某币在 [start_ms, end_ms) 的月度 parquet；缺文件的月份跳过并记录在 manifest。"""
    frames: list[pd.DataFrame] = []
    for month in _month_keys(start_ms, end_ms):
        path = root / symbol / f"{month}.parquet"
        if not path.is_file():
            continue
        frames.append(pq.read_table(path, columns=["open_time", *_BAR_COLUMNS]).to_pandas())
    if not frames:
        return None
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[(frame["open_time"] >= start_ms) & (frame["open_time"] < end_ms)]
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    if frame.empty:
        return None
    return Bars(
        symbol=symbol,
        open_time=frame["open_time"].to_numpy(dtype="int64"),
        open=frame["open"].to_numpy(dtype="float64"),
        high=frame["high"].to_numpy(dtype="float64"),
        low=frame["low"].to_numpy(dtype="float64"),
        close=frame["close"].to_numpy(dtype="float64"),
        quote_volume=frame["quote_volume"].to_numpy(dtype="float64"),
    )


def load_funding_dir(root: Path, symbol: str) -> FundingSeries | None:
    """加载某币全部资金费月度 parquet。"""
    files = sorted((root / symbol).glob("*.parquet")) if (root / symbol).is_dir() else []
    if not files:
        return None
    frames = [pq.read_table(path).to_pandas() for path in files]
    frame = pd.concat(frames, ignore_index=True).sort_values("calc_time")
    frame = frame.drop_duplicates("calc_time", keep="last")
    return FundingSeries(
        symbol=symbol,
        calc_time=frame["calc_time"].to_numpy(dtype="int64"),
        hourly_rate=frame["hourly_rate"].to_numpy(dtype="float64"),
        interval_hours=frame["interval_hours"].to_numpy(dtype="float64"),
    )


def load_universe_history(root: Path, start_month: str, end_month: str) -> UniverseHistory:
    """加载 [start_month, end_month] 的月度快照。"""
    snapshots: dict[str, UniverseSnapshot] = {}
    for month in _month_keys(_month_start_ms(start_month), _month_start_ms(_next_month(end_month))):
        path = root / f"{month}.json"
        if path.is_file():
            snapshots[month] = read_universe_snapshot(path)
    return UniverseHistory(snapshots)


def load_rules(path: Path) -> dict[str, SymbolMeta]:
    """读取 `artifacts/exchange_info/rules.json`（03 §4.3）。"""
    if not path.is_file():
        raise FileNotFoundError(f"缺少交易规则快照：{path}（先运行 scripts/snapshot_rules.py）")
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = set(SymbolMeta.__dataclass_fields__)
    rules: dict[str, SymbolMeta] = {}
    for symbol, values in raw.items():
        payload = values if isinstance(values, dict) else {}
        rules[symbol] = SymbolMeta(
            symbol=symbol, **{key: value for key, value in payload.items() if key in fields}
        )
    return rules


def load_price_dir(
    root: Path, symbol: str, start_ms: int, end_ms: int, *, label: str
) -> MarkBars | None:
    """加载标记价格 / 溢价指数的月度 parquet（`open_time` + OHLC）。

    Args:
        root: 目录根（`data/mark_1h` 或 `data/premium_1h`）。
        symbol: 币种。
        start_ms: 起始毫秒（含）。
        end_ms: 结束毫秒（不含）。
        label: 目录名，用于报错上下文。

    Returns:
        MarkBars；没有文件或区间内没有数据时返回 None。

    Raises:
        ValueError: 文件内存在空价格（红线 12）。
    """
    frames: list[pd.DataFrame] = []
    for month in _month_keys(start_ms, end_ms):
        path = root / symbol / f"{month}.parquet"
        if not path.is_file():
            continue
        frames.append(pq.read_table(path, columns=["open_time", *_PRICE_COLUMNS]).to_pandas())
    if not frames:
        return None
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[(frame["open_time"] >= start_ms) & (frame["open_time"] < end_ms)]
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    if frame.empty:
        return None
    if frame[list(_PRICE_COLUMNS)].isna().to_numpy().any():
        raise ValueError(f"{label} 的 {symbol} 存在空价格（docs/13 §1，红线 12）")
    return MarkBars(
        symbol=symbol,
        open_time=frame["open_time"].to_numpy(dtype="int64"),
        high=frame["high"].to_numpy(dtype="float64"),
        low=frame["low"].to_numpy(dtype="float64"),
        close=frame["close"].to_numpy(dtype="float64"),
    )


def load_flow_dir(root: Path, symbol: str, start_ms: int, end_ms: int) -> FlowBars | None:
    """加载主动买入成交的月度 parquet。"""
    frames: list[pd.DataFrame] = []
    for month in _month_keys(start_ms, end_ms):
        path = root / symbol / f"{month}.parquet"
        if not path.is_file():
            continue
        frames.append(
            pq.read_table(
                path, columns=["open_time", "taker_buy_volume", "taker_buy_quote_volume"]
            ).to_pandas()
        )
    if not frames:
        return None
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[(frame["open_time"] >= start_ms) & (frame["open_time"] < end_ms)]
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    if frame.empty:
        return None
    return FlowBars(
        symbol=symbol,
        open_time=frame["open_time"].to_numpy(dtype="int64"),
        taker_buy_volume=frame["taker_buy_volume"].to_numpy(dtype="float64"),
        taker_buy_quote_volume=frame["taker_buy_quote_volume"].to_numpy(dtype="float64"),
    )


def _check_flow_alignment(symbol: str, bars: Bars, flow: FlowBars) -> None:
    """`klines_1h_flow` 的 `open_time` 必须与 `klines_1h` 逐行一致（docs/13 §1）。

    Raises:
        ValueError: 时间轴不一致，带币种与首个不一致的月份。
    """
    if bars.open_time.size == flow.open_time.size and np.array_equal(
        bars.open_time, flow.open_time
    ):
        return
    size = min(bars.open_time.size, flow.open_time.size)
    first = size
    if size:
        diff = np.nonzero(bars.open_time[:size] != flow.open_time[:size])[0]
        first = int(diff[0]) if diff.size else size
    stamp = int(bars.open_time[first]) if first < bars.open_time.size else int(bars.open_time[-1])
    day = datetime.fromtimestamp(stamp // 1000, tz=UTC)
    raise ValueError(
        f"{symbol} 的 klines_1h_flow 与 klines_1h 时间轴不一致（首个差异 {day:%Y-%m}，docs/13 §1）"
    )


def load_market_data(
    *,
    klines_1h: Path,
    klines_1d: Path,
    funding: Path,
    universe_hist: Path,
    rules_path: Path,
    start_ms: int,
    end_ms: int,
    start_month: str,
    end_month: str,
    mark_1h: Path | None = None,
    flow_1h: Path | None = None,
    premium_1h: Path | None = None,
    extra_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
) -> MarketData:
    """加载研究期数据；只加载选币快照出现过的币与 `extra_symbols`。

    Args:
        klines_1h: 1h K 线目录。
        klines_1d: 1d K 线目录。
        funding: 资金费目录。
        universe_hist: 月度选币快照目录。
        rules_path: `artifacts/exchange_info/rules.json`。
        start_ms: 起始毫秒（含）。
        end_ms: 结束毫秒（不含）。
        start_month: 起始月份 `YYYY-MM`。
        end_month: 结束月份 `YYYY-MM`。
        mark_1h: 标记价格目录；None 表示不加载（止损触发退回成交价并计数）。
        flow_1h: 主动买入成交目录；None 表示不加载。
        premium_1h: 溢价指数目录；None 表示不加载。
        extra_symbols: 额外加载的币（BTC/ETH）。

    Returns:
        MarketData。

    Raises:
        ValueError: 没有 1h 数据、缺交易规则或 flow 时间轴不一致（红线 12）。
    """
    universe = load_universe_history(universe_hist, start_month, end_month)
    symbols = set(extra_symbols)
    for month in universe.months:
        snapshot = read_universe_snapshot(universe_hist / f"{month}.json")
        symbols.update(coin.symbol for coin in snapshot.coins)
    hourly: dict[str, Bars] = {}
    daily: dict[str, Bars] = {}
    funding_series: dict[str, FundingSeries] = {}
    mark: dict[str, MarkBars] = {}
    flow: dict[str, FlowBars] = {}
    premium: dict[str, MarkBars] = {}
    for symbol in sorted(symbols):
        bars = load_bars_dir(klines_1h, symbol, start_ms, end_ms)
        if bars is None:
            continue
        hourly[symbol] = bars
        day_bars = load_bars_dir(klines_1d, symbol, start_ms, end_ms)
        if day_bars is not None:
            daily[symbol] = day_bars
        fund = load_funding_dir(funding, symbol)
        if fund is not None:
            funding_series[symbol] = fund
        if mark_1h is not None:
            mark_bars = load_price_dir(mark_1h, symbol, start_ms, end_ms, label="mark_1h")
            if mark_bars is not None:
                mark[symbol] = mark_bars
        if premium_1h is not None:
            premium_bars = load_price_dir(premium_1h, symbol, start_ms, end_ms, label="premium_1h")
            if premium_bars is not None:
                premium[symbol] = premium_bars
        if flow_1h is not None:
            flow_bars = load_flow_dir(flow_1h, symbol, start_ms, end_ms)
            if flow_bars is not None:
                _check_flow_alignment(symbol, bars, flow_bars)
                flow[symbol] = flow_bars
    if not hourly:
        raise ValueError(f"研究期没有 1h 数据：{klines_1h}（先运行 scripts/fetch_history.py）")
    rules = load_rules(rules_path)
    missing = sorted(symbol for symbol in hourly if symbol not in rules)
    if missing:
        raise ValueError(f"以下币缺少交易规则：{missing[:5]}…（红线 12）")
    return MarketData(
        hourly=hourly,
        daily=daily,
        funding=funding_series,
        universe=universe,
        rules=rules,
        mark=mark,
        flow=flow,
        premium=premium,
    )


def _month_start_ms(month: str) -> int:
    year, mon = (int(part) for part in month.split("-"))
    return int(datetime(year, mon, 1, tzinfo=UTC).timestamp() * 1000)


def _next_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"
