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
    FlowBars,
    FundingSeries,
    MarkBars,
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

    async def mark_price_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]: ...

    async def premium_index_klines(
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


def flow_from_rows(symbol: str, rows: Iterable[list[Any]]) -> FlowBars | None:
    """从 1h K 线行提取主动买入成交（`taker_buy_*`，docs/13 §1）。

    Args:
        symbol: 币种。
        rows: 币安 K 线原始行（索引 9/10 为主动买入成交量与成交额）。

    Returns:
        FlowBars；没有数据返回 None。

    Raises:
        FeedError: 行的列数不足（红线 12，不用 0 填充）。
    """
    data = list(rows)
    if not data:
        return None
    for row in data:
        if len(row) < 11:
            raise FeedError(f"{symbol} 的 K 线缺少 taker_buy 字段（{len(row)} 列）")
    open_time = np.array([int(row[0]) for row in data], dtype=np.int64)
    order = np.argsort(open_time)
    return FlowBars(
        symbol=symbol,
        open_time=open_time[order],
        taker_buy_volume=np.array([float(row[9]) for row in data], dtype=np.float64)[order],
        taker_buy_quote_volume=np.array([float(row[10]) for row in data], dtype=np.float64)[order],
    )


def price_from_rows(symbol: str, rows: Iterable[list[Any]], *, label: str) -> MarkBars | None:
    """把标记价格 / 溢价指数 K 线行转换为 `MarkBars`（docs/13 §1、§4）。

    Args:
        symbol: 币种。
        rows: 价格 K 线原始行（索引 1~4 为 open/high/low/close）。
        label: 目录名（`mark_1h` / `premium_1h`），只用于报错上下文。

    Returns:
        MarkBars；没有数据返回 None。

    Raises:
        FeedError: 行的列数不足 5（红线 12）。
    """
    data = list(rows)
    if not data:
        return None
    for row in data:
        if len(row) < 5:
            raise FeedError(f"{symbol} 的 {label} 行不足 5 列（{len(row)} 列）")
    open_time = np.array([int(row[0]) for row in data], dtype=np.int64)
    order = np.argsort(open_time)
    return MarkBars(
        symbol=symbol,
        open_time=open_time[order],
        open=np.array([float(row[1]) for row in data], dtype=np.float64)[order],
        high=np.array([float(row[2]) for row in data], dtype=np.float64)[order],
        low=np.array([float(row[3]) for row in data], dtype=np.float64)[order],
        close=np.array([float(row[4]) for row in data], dtype=np.float64)[order],
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


def _merge_flow(old: FlowBars | None, new: FlowBars) -> FlowBars:
    """按 `open_time` 合并主动买入成交（后到的数据覆盖重复行）。"""
    if old is None:
        return new
    merged: dict[int, tuple[float, float]] = {
        int(ts): (float(buy), float(quote))
        for ts, buy, quote in zip(
            old.open_time, old.taker_buy_volume, old.taker_buy_quote_volume, strict=True
        )
    }
    for index, ts in enumerate(new.open_time):
        merged[int(ts)] = (
            float(new.taker_buy_volume[index]),
            float(new.taker_buy_quote_volume[index]),
        )
    times = np.array(sorted(merged), dtype=np.int64)
    return FlowBars(
        symbol=new.symbol,
        open_time=times,
        taker_buy_volume=np.array([merged[int(ts)][0] for ts in times], dtype=np.float64),
        taker_buy_quote_volume=np.array([merged[int(ts)][1] for ts in times], dtype=np.float64),
    )


def _merge_price(old: MarkBars | None, new: MarkBars) -> MarkBars:
    """按 `open_time` 合并标记价格 / 溢价指数（后到的数据覆盖重复行）。"""
    if old is None:
        return new
    merged: dict[int, tuple[float, float, float, float]] = {
        int(ts): (float(open_), float(high), float(low), float(close))
        for ts, open_, high, low, close in zip(
            old.open_time, old.open, old.high, old.low, old.close, strict=True
        )
    }
    for index, ts in enumerate(new.open_time):
        merged[int(ts)] = (
            float(new.open[index]),
            float(new.high[index]),
            float(new.low[index]),
            float(new.close[index]),
        )
    times = np.array(sorted(merged), dtype=np.int64)
    return MarkBars(
        symbol=new.symbol,
        open_time=times,
        open=np.array([merged[int(ts)][0] for ts in times], dtype=np.float64),
        high=np.array([merged[int(ts)][1] for ts in times], dtype=np.float64),
        low=np.array([merged[int(ts)][2] for ts in times], dtype=np.float64),
        close=np.array([merged[int(ts)][3] for ts in times], dtype=np.float64),
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
        with_premium: 是否拉取溢价指数（候选需要 `premium_1h` 时开启）。
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
        with_premium: bool = False,
    ) -> None:
        self._source = source
        self._rules = dict(rules)
        self._statuses = dict(statuses or {})
        self._universe = universe
        self._days_hourly = history_days_hourly
        self._days_daily = history_days_daily
        self._funding_limit = funding_limit
        self._with_premium = with_premium
        self._semaphore = asyncio.Semaphore(concurrency)
        self.hourly: dict[str, Bars] = {}
        self.daily: dict[str, Bars] = {}
        self.funding: dict[str, FundingSeries] = {}
        self.mark: dict[str, MarkBars] = {}
        self.flow: dict[str, FlowBars] = {}
        self.premium: dict[str, MarkBars] = {}

    def market(self) -> MarketData:
        """当前缓存组装成研究框架的 `MarketData`（与离线数据层同构）。"""
        return MarketData(
            hourly=self.hourly,
            daily=self.daily,
            funding=self.funding,
            universe=self._universe,
            rules=self._rules,
            mark=self.mark,
            flow=self.flow,
            premium=self.premium,
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
        self._store_rows(
            symbol,
            await self._fetch_rows(
                self._source.klines, symbol, "1h", now_ms - self._days_hourly * DAY_MS, now_ms
            ),
            interval="1h",
        )
        self._store_rows(
            symbol,
            await self._fetch_rows(
                self._source.klines, symbol, "1d", now_ms - self._days_daily * DAY_MS, now_ms
            ),
            interval="1d",
        )
        if symbol not in self.hourly:
            raise FeedError(f"{symbol} 回补 1h K 线为空（{self._days_hourly} 天窗口）")
        if symbol not in self.daily:
            raise FeedError(f"{symbol} 回补 1d K 线为空（{self._days_daily} 天窗口）")
        await self._load_prices(symbol, now_ms - self._days_hourly * DAY_MS, now_ms)
        rows = await _retry(self._source.funding_rate, symbol, limit=self._funding_limit)
        if rows:
            series = bars_from_funding(symbol, rows)
            if series is not None:
                self.funding[symbol] = series

    async def _refresh_symbol(self, symbol: str, now_ms: int, tail_hours: int) -> None:
        if symbol not in self.hourly:
            await self._load_symbol(symbol, now_ms)
            return
        start_ms = now_ms - tail_hours * HOUR_MS
        rows = await self._fetch_rows(self._source.klines, symbol, "1h", start_ms, now_ms)
        self._merge_rows(symbol, rows, interval="1h")
        daily_rows = await self._fetch_rows(
            self._source.klines, symbol, "1d", now_ms - 3 * DAY_MS, now_ms
        )
        self._merge_rows(symbol, daily_rows, interval="1d")
        await self._load_prices(symbol, start_ms, now_ms)
        rows = await _retry(self._source.funding_rate, symbol, limit=self._funding_limit)
        if rows:
            series = bars_from_funding(symbol, rows)
            if series is not None:
                self.funding[symbol] = series

    async def _load_prices(self, symbol: str, start_ms: int, end_ms: int) -> None:
        """拉取标记价格（止损基准）与（启用时）溢价指数 1h K 线（docs/13 §1、§4）。"""
        mark_rows = await self._fetch_rows(
            self._source.mark_price_klines, symbol, "1h", start_ms, end_ms
        )
        mark = price_from_rows(symbol, mark_rows, label="mark_1h")
        if mark is None:
            if symbol not in self.mark:
                raise FeedError(f"{symbol} 回补标记价格为空（止损基准缺失，红线 12）")
        else:
            self.mark[symbol] = _merge_price(self.mark.get(symbol), mark)
        if not self._with_premium:
            return
        premium_rows = await self._fetch_rows(
            self._source.premium_index_klines, symbol, "1h", start_ms, end_ms
        )
        premium = price_from_rows(symbol, premium_rows, label="premium_1h")
        if premium is None:
            if symbol not in self.premium:
                raise FeedError(f"{symbol} 回补溢价指数为空（候选使用 premium_1h）")
            return
        self.premium[symbol] = _merge_price(self.premium.get(symbol), premium)

    def _store_rows(self, symbol: str, rows: list[list[Any]], *, interval: str) -> None:
        """把首次回补的原始行写入缓存（1h 同时写入主动买入成交）。"""
        bars = bars_from_rows(symbol, rows)
        if bars is None:
            return
        if interval == "1h":
            self.hourly[symbol] = bars
            flow = flow_from_rows(symbol, rows)
            if flow is not None:
                self.flow[symbol] = flow
        else:
            self.daily[symbol] = bars

    def _merge_rows(self, symbol: str, rows: list[list[Any]], *, interval: str) -> None:
        """把增量行合并进缓存（后到的数据覆盖重复行）。"""
        bars = bars_from_rows(symbol, rows)
        if bars is None:
            return
        if interval == "1h":
            self.hourly[symbol] = merge_bars(self.hourly[symbol], bars) or self.hourly[symbol]
            flow = flow_from_rows(symbol, rows)
            if flow is not None and symbol in self.hourly:
                self.flow[symbol] = _merge_flow(self.flow.get(symbol), flow)
        else:
            self.daily[symbol] = merge_bars(self.daily[symbol], bars) or self.daily[symbol]

    async def _fetch_rows(
        self,
        fetch: Any,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[list[Any]]:
        """分页拉取原始 K 线行，只保留已收盘的（`open_time + 周期 <= end_ms`）。"""
        rows: list[list[Any]] = []
        cursor = start_ms
        step_ms = HOUR_MS if interval == "1h" else DAY_MS
        async with self._semaphore:
            while cursor < end_ms:
                batch = await _retry(
                    fetch,
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
        return [row for row in rows if int(row[0]) + step_ms <= end_ms]
