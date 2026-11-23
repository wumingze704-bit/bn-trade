"""实盘行情：用 REST 组装与研究框架相同的数据视图（docs/07 §6.2）。

每个整点后 30 秒拉取刚收盘的 1h K 线（策略需要的全部币）、最新资金费与
交易所交易规则；启动时回补策略所需历史。拼出的 `MarketData` 与
`trade.research.data` 的数据层逐值一致（同一份 `Bars` / `FundingSeries` 结构）。

规格：docs/07-实盘与风控.md §6.2；docs/13-研究框架.md §1
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from trade.data.universe import SymbolMeta, UniverseSnapshot, read_universe_snapshot
from trade.live.exchange.models import BookTicker
from trade.research.data import (
    DAY_MS,
    HOUR_MS,
    Bars,
    FundingSeries,
    MarketData,
    UniverseHistory,
)

_KLINE_LIMIT = 1000
_RETRIES = 3
_MONTH_FILE_RE = re.compile(r"^\d{4}-\d{2}\.json$")


class FeedError(RuntimeError):
    """行情拉取失败（重试后仍失败）。"""


class RestSource(Protocol):
    """行情拉取需要的交易所接口子集。"""

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]: ...

    async def funding_rate(self, symbol: str, *, limit: int = 500) -> list[dict[str, Any]]: ...

    async def book_ticker(self, symbol: str | None = None) -> list[BookTicker]: ...

    async def exchange_info(self) -> Any: ...


async def _retry(call: Any, *args: Any, **kwargs: Any) -> Any:
    """带 3 次重试的 REST 调用（失败抛出带上下文的 FeedError）。"""
    last: BaseException | None = None
    for attempt in range(_RETRIES):
        if attempt > 0:
            await asyncio.sleep(float(attempt))
        try:
            return await call(*args, **kwargs)
        except Exception as error:  # 网络与交易所错误都重试
            last = error
    raise FeedError(f"REST 拉取失败（已重试 {_RETRIES} 次）：{call.__name__}：{last}")


def bars_from_rows(symbol: str, rows: Iterable[list[Any]]) -> Bars | None:
    """按 symbol 构造 `Bars`（K 线数组不含 symbol 列）。"""
    data = list(rows)
    if not data:
        return None
    open_time = np.array([int(row[0]) for row in data], dtype=np.int64)
    order = np.argsort(open_time)
    return Bars(
        symbol=symbol,
        open_time=open_time[order],
        open=np.array([float(row[1]) for row in data], dtype=np.float64)[order],
        high=np.array([float(row[2]) for row in data], dtype=np.float64)[order],
        low=np.array([float(row[3]) for row in data], dtype=np.float64)[order],
        close=np.array([float(row[4]) for row in data], dtype=np.float64)[order],
        quote_volume=np.array([float(row[7]) for row in data], dtype=np.float64)[order],
    )


def merge_bars(old: Bars | None, new: Bars | None) -> Bars | None:
    """按 open_time 合并两段 K 线，后到的数据覆盖重复行。"""
    if old is None:
        return new
    if new is None:
        return old
    combined = {int(ts): i for i, ts in enumerate(old.open_time)}
    times = list(int(v) for v in old.open_time)
    opens = list(old.open)
    highs = list(old.high)
    lows = list(old.low)
    closes = list(old.close)
    volumes = list(old.quote_volume)
    for index, ts in enumerate(new.open_time):
        target = combined.get(int(ts))
        if target is None:
            combined[int(ts)] = len(times)
            times.append(int(ts))
            opens.append(float(new.open[index]))
            highs.append(float(new.high[index]))
            lows.append(float(new.low[index]))
            closes.append(float(new.close[index]))
            volumes.append(float(new.quote_volume[index]))
        else:
            opens[target] = float(new.open[index])
            highs[target] = float(new.high[index])
            lows[target] = float(new.low[index])
            closes[target] = float(new.close[index])
            volumes[target] = float(new.quote_volume[index])
    order = np.argsort(np.array(times, dtype=np.int64))
    return Bars(
        symbol=old.symbol,
        open_time=np.array(times, dtype=np.int64)[order],
        open=np.array(opens, dtype=np.float64)[order],
        high=np.array(highs, dtype=np.float64)[order],
        low=np.array(lows, dtype=np.float64)[order],
        close=np.array(closes, dtype=np.float64)[order],
        quote_volume=np.array(volumes, dtype=np.float64)[order],
    )


def bars_from_funding(symbol: str, rows: Iterable[dict[str, Any]]) -> FundingSeries | None:
    """把资金费记录转换为 `FundingSeries`，按相邻结算时刻推断 interval_hours。

    Raises:
        FeedError: 只有一条记录无法推断结算间隔（红线 12，不用默认值）。
    """
    data = sorted(rows, key=lambda item: int(item["fundingTime"]))
    if len(data) < 2:
        raise FeedError(f"{symbol} 资金费记录不足 2 条，无法推断结算间隔")
    calc = np.array([int(item["fundingTime"]) for item in data], dtype=np.int64)
    rate = np.array([float(item["fundingRate"]) for item in data], dtype=np.float64)
    gaps = np.diff(calc) / HOUR_MS
    if np.any(gaps <= 0.0):
        raise FeedError(f"{symbol} 资金费结算时刻重复或乱序，无法推断结算间隔")
    intervals = np.concatenate([gaps[:1], gaps])
    return FundingSeries(
        symbol=symbol,
        calc_time=calc,
        hourly_rate=rate / intervals,
        interval_hours=intervals,
    )


def rules_from_exchange_info(info: Any) -> dict[str, SymbolMeta]:
    """从 exchangeInfo 响应提取 tickSize / stepSize / minNotional。

    Raises:
        FeedError: 缺少必要过滤器（红线 12）。
    """
    rules: dict[str, SymbolMeta] = {}
    for item in info.symbols:
        symbol = str(item.get("symbol", ""))
        filters = {str(f.get("filterType")): f for f in item.get("filters", [])}
        try:
            tick = float(filters["PRICE_FILTER"]["tickSize"])
            step = float(filters["LOT_SIZE"]["stepSize"])
            min_notional = float(filters["MIN_NOTIONAL"]["notional"])
        except (KeyError, TypeError, ValueError) as error:
            raise FeedError(f"{symbol} 的 exchangeInfo 过滤器缺失：{error}") from error
        rules[symbol] = SymbolMeta(
            symbol=symbol,
            tick_size=tick,
            step_size=step,
            min_notional=min_notional,
        )
    return rules


def statuses_from_exchange_info(info: Any) -> dict[str, str]:
    """从 exchangeInfo 提取交易状态（`TRADING` / `SETTLING` 等，用于下架检查）。"""
    return {
        str(item.get("symbol", "")): str(item.get("status", ""))
        for item in info.symbols
        if item.get("symbol")
    }


def load_universe_history(directory: Path, *, months: int = 3) -> UniverseHistory:
    """读取最近 `months` 个月的选币快照（`data/universe_hist/{YYYY-MM}.json`）。

    Raises:
        FeedError: 目录为空或快照无法读取。
    """
    files = [path for path in sorted(directory.glob("*.json")) if _MONTH_FILE_RE.match(path.name)]
    if not files:
        raise FeedError(f"选币快照目录为空：{directory}（先运行 scripts/build_universe_hist.py）")
    snapshots: dict[str, UniverseSnapshot] = {}
    for path in files[-months:]:
        snapshots[path.stem] = read_universe_snapshot(path)
    return UniverseHistory(snapshots)


class LiveFeed:
    """实盘行情缓存：启动回补历史，之后每小时只补尾部（docs/07 §6.2）。

    Args:
        source: REST 行情客户端（正式网或 demo）。
        rules: exchangeInfo 提取的交易规则。
        universe: 月度选币快照。
        statuses: symbol → exchangeInfo 交易状态（下架检查用）。
        history_days_hourly: 1h 回补天数。
        history_days_daily: 1d 回补天数。
        funding_limit: 每币资金费记录上限。
        concurrency: 并发拉取的币数（控制请求权重）。
    """

    def __init__(
        self,
        *,
        source: RestSource,
        rules: Mapping[str, SymbolMeta],
        universe: UniverseHistory,
        statuses: Mapping[str, str] | None = None,
        history_days_hourly: int,
        history_days_daily: int,
        funding_limit: int,
        concurrency: int = 3,
    ) -> None:
        self._source = source
        self._rules = dict(rules)
        self._statuses = dict(statuses or {})
        self._universe = universe
        self._days_hourly = history_days_hourly
        self._days_daily = history_days_daily
        self._funding_limit = funding_limit
        self._semaphore = asyncio.Semaphore(concurrency)
        self.hourly: dict[str, Bars] = {}
        self.daily: dict[str, Bars] = {}
        self.funding: dict[str, FundingSeries] = {}

    def market(self) -> MarketData:
        """当前缓存组装成研究框架的 `MarketData`（与离线数据层同构）。"""
        return MarketData(
            hourly=self.hourly,
            daily=self.daily,
            funding=self.funding,
            universe=self._universe,
            rules=self._rules,
        )

    def rules(self) -> dict[str, SymbolMeta]:
        """返回交易规则字典（tickSize / stepSize / minNotional）。"""
        return dict(self._rules)

    def status(self, symbol: str) -> str | None:
        """返回 exchangeInfo 中的交易状态（未知返回 None）。"""
        return self._statuses.get(symbol)

    async def warmup(self, symbols: Iterable[str], now_ms: int) -> None:
        """回补每个币的 1h / 1d K 线与资金费（启动时调用一次）。"""
        tasks = [self._load_symbol(symbol, now_ms) for symbol in sorted(set(symbols))]
        await asyncio.gather(*tasks)

    async def refresh(self, symbols: Iterable[str], now_ms: int, *, tail_hours: int = 48) -> None:
        """补齐最近 `tail_hours` 的 1h、最近 3 天 1d 与资金费（每整点后调用）。"""
        tasks = [
            self._refresh_symbol(symbol, now_ms, tail_hours) for symbol in sorted(set(symbols))
        ]
        await asyncio.gather(*tasks)

    async def book(self, symbol: str) -> tuple[float, float]:
        """读取买一 / 卖一（决策时刻的盘口快照）。

        Returns:
            (bid, ask)。

        Raises:
            FeedError: 返回条数不为 1 或价格非法。
        """
        items = await _retry(self._source.book_ticker, symbol)
        if len(items) != 1:
            raise FeedError(f"{symbol} 盘口请求返回 {len(items)} 条")
        bid = float(items[0].bid_price)
        ask = float(items[0].ask_price)
        if bid <= 0.0 or ask <= 0.0 or ask < bid:
            raise FeedError(f"{symbol} 盘口非法：bid={bid}, ask={ask}")
        return bid, ask

    async def _load_symbol(self, symbol: str, now_ms: int) -> None:
        hourly = await self._fetch_klines(symbol, "1h", now_ms - self._days_hourly * DAY_MS, now_ms)
        if hourly is None:
            raise FeedError(f"{symbol} 回补 1h K 线为空（{self._days_hourly} 天窗口）")
        daily = await self._fetch_klines(symbol, "1d", now_ms - self._days_daily * DAY_MS, now_ms)
        if daily is None:
            raise FeedError(f"{symbol} 回补 1d K 线为空（{self._days_daily} 天窗口）")
        self.hourly[symbol] = hourly
        self.daily[symbol] = daily
        rows = await _retry(self._source.funding_rate, symbol, limit=self._funding_limit)
        if rows:
            series = bars_from_funding(symbol, rows)
            if series is not None:
                self.funding[symbol] = series

    async def _refresh_symbol(self, symbol: str, now_ms: int, tail_hours: int) -> None:
        if symbol not in self.hourly:
            await self._load_symbol(symbol, now_ms)
            return
        hourly = await self._fetch_klines(symbol, "1h", now_ms - tail_hours * HOUR_MS, now_ms)
        self.hourly[symbol] = merge_bars(self.hourly[symbol], hourly) or self.hourly[symbol]
        daily = await self._fetch_klines(symbol, "1d", now_ms - 3 * DAY_MS, now_ms)
        self.daily[symbol] = merge_bars(self.daily[symbol], daily) or self.daily[symbol]
        rows = await _retry(self._source.funding_rate, symbol, limit=self._funding_limit)
        if rows:
            series = bars_from_funding(symbol, rows)
            if series is not None:
                self.funding[symbol] = series

    async def _fetch_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> Bars | None:
        rows: list[list[Any]] = []
        cursor = start_ms
        step_ms = HOUR_MS if interval == "1h" else DAY_MS
        async with self._semaphore:
            while cursor < end_ms:
                batch = await _retry(
                    self._source.klines,
                    symbol,
                    interval,
                    limit=_KLINE_LIMIT,
                    start_time=cursor,
                    end_time=end_ms,
                )
                if not batch:
                    break
                rows.extend(batch)
                last_open = int(batch[-1][0])
                cursor = last_open + step_ms
                if len(batch) < _KLINE_LIMIT:
                    break
        # 只保留已收盘的 K 线：open_time + 周期 <= now
        closed = [row for row in rows if int(row[0]) + step_ms <= end_ms]
        return bars_from_rows(symbol, closed)
