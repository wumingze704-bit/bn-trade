"""实时行情管理：流名称、连接消费与重连、缺口回补、listenKey 续期与月度再平衡。

规格：docs/07-实盘与风控.md §3.1、§3.2、§3.5；docs/09-币安数据与API.md §3.3
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd  # type: ignore[import-untyped]  # pandas 无官方类型存根

from trade.data.universe import (
    SymbolMeta,
    UniverseConfig,
    UniverseSnapshot,
    assign_aliases,
    build_universe,
    load_aliases,
    write_universe_snapshot,
)
from trade.live.exchange.client import ExchangeClient
from trade.live.exchange.endpoints import Endpoints
from trade.live.exchange.ws import WsConnection

# 连接轮换周期（07 §3.1：23 小时主动新建连接）
_ROTATE_MS = 23 * 60 * 60 * 1000
# listenKey 续期间隔（07 §3.1：30 分钟）
_LISTEN_KEY_RENEW_MS = 30 * 60 * 1000
# 重连指数退避：首次 1 秒、上限 30 秒（07 §3.1）
_RECONNECT_BASE_MS = 1_000
_RECONNECT_CAP_MS = 30_000
# 1m K 线周期（07 §3.2）
_KLINE_INTERVAL_MS = 60_000
# 单次 REST K 线请求上限（09 §3.1）
_KLINE_LIMIT = 1_500


def subscription_symbols(
    *,
    trading_symbols: set[str],
    open_position_symbols: set[str],
    btc_context: str,
) -> tuple[list[str], list[str]]:
    """计算 K 线/逐笔订阅与 bookTicker 订阅的币种列表（07 §3.1）。

    Args:
        trading_symbols: 当月交易范围 symbol 集合。
        open_position_symbols: 持仓中（含已退出交易范围）的 symbol 集合。
        btc_context: BTC 环境币（如 BTCUSDT）。

    Returns:
        (kline_aggtrade_symbols, bookticker_symbols)：前者含 BTC，后者不含 BTC。
    """
    all_symbols = sorted(trading_symbols | open_position_symbols | {btc_context})
    bookticker_symbols = sorted((trading_symbols | open_position_symbols) - {btc_context})
    return all_symbols, bookticker_symbols


def should_rotate_connection(*, connected_ms: int, now_ms: int) -> bool:
    """连接满 23 小时即应主动轮换（07 §3.1）。"""
    return now_ms - connected_ms >= _ROTATE_MS


def should_renew_listen_key(*, last_renew_ms: int, now_ms: int) -> bool:
    """listenKey 每 30 分钟续期（07 §3.1）。"""
    return now_ms - last_renew_ms >= _LISTEN_KEY_RENEW_MS


def rebalance_due(*, now_ms: int, last_rebalance_ms: int, rebalance_utc: str) -> bool:
    """判断是否到了月初 00:05 再平衡时点（07 §3.5）。"""
    from datetime import UTC, datetime

    hour, minute = (int(x) for x in rebalance_utc.split(":"))
    now = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    if now.hour != hour or now.minute != minute:
        return False
    # 同一分钟内已执行过则跳过
    return (now_ms - last_rebalance_ms) > 60_000


def new_entrants(prev: UniverseSnapshot, curr: UniverseSnapshot) -> set[str]:
    """新进入交易范围的 symbol 集合（07 §3.5：只监控 + 订单流预热 2 天）。"""
    prev_symbols = {coin.symbol for coin in prev.coins}
    return {coin.symbol for coin in curr.coins} - prev_symbols


def exited_symbols(prev: UniverseSnapshot, curr: UniverseSnapshot) -> set[str]:
    """退出交易范围的 symbol 集合（07 §3.5：保留持仓至平仓）。"""
    curr_symbols = {coin.symbol for coin in curr.coins}
    return {coin.symbol for coin in prev.coins} - curr_symbols


def top_gainers(
    tickers: list[dict[str, float | str]], size: int, quote_asset: str = "USDT"
) -> list[str]:
    """从 24 小时行情中取涨幅榜前 `size` 的 USDT 永续 symbol（07 §3.5）。

    Args:
        tickers: `GET /fapi/v1/ticker/24hr` 解析后的列表（含 symbol、priceChangePercent）。
        size: 取前 N。
        quote_asset: 报价资产（默认 USDT）。

    Returns:
        按 `priceChangePercent` 降序的前 N 个 symbol。
    """
    usdt = [t for t in tickers if str(t.get("symbol", "")).endswith(quote_asset)]
    ranked = sorted(usdt, key=lambda t: float(t.get("price_change_percent", 0.0)), reverse=True)
    return [str(t["symbol"]) for t in ranked[:size]]


def trading_scope(universe_symbols: set[str], candidate_pool_symbols: set[str]) -> set[str]:
    """交易范围 = 月度选币 ∪ 动态候选池（取并集，范围最大）。

    规格：docs/07-实盘与风控.md §3.5；AGENTS.md §6 红线 6（实盘候选池为动态涨幅榜前 20，
    与训练/回测名单无关）。使用者决定两者并存、取并集。
    """
    return universe_symbols | candidate_pool_symbols


@dataclass
class Rebalancer:
    """月度再平衡编排（07 §3.5）。

    复用 `trade.data.universe.build_universe()`（与回测/训练同一份代码，AGENTS.md §6 红线 4）。
    """

    def __init__(self, *, cfg: UniverseConfig, universe_dir: Path, btc_context: str) -> None:
        self._cfg = cfg
        self._universe_dir = universe_dir
        self._btc_context = btc_context

    def run(
        self,
        *,
        rebalance_ms: int,
        daily_bars: dict[str, pd.DataFrame],
        rules: dict[str, SymbolMeta],
        quality_cache: pd.DataFrame | None = None,
        aliases: dict[str, str] | None = None,
    ) -> tuple[UniverseSnapshot, dict[str, str]]:
        """执行一次再平衡，生成并落盘月度快照（07 §3.5、03 §4.2）。

        Args:
            rebalance_ms: 再平衡时点（UTC 毫秒）。
            daily_bars: symbol -> 日线 DataFrame（至少含 open_time、close、quote_volume）。
            rules: symbol -> SymbolMeta（由 exchangeInfo/leverageBracket 构建）。
            quality_cache: 可选质量缓存（03 §2.4）。
            aliases: 现有别名映射（只增不改，03 §4.5）。

        Returns:
            (快照, 更新后的别名映射)。
        """
        snapshot = build_universe(
            rebalance_ms,
            daily_bars,
            rules,
            self._cfg,
            quality_cache=quality_cache,
        )
        if aliases is None:
            aliases = load_aliases(self._universe_dir / "aliases.json")
        snapshot, aliases = assign_aliases(snapshot, aliases)
        month = snapshot.month
        write_universe_snapshot(self._universe_dir / f"{month}.json", snapshot)
        return snapshot, aliases


def now_ms() -> int:
    """当前 UTC 毫秒时间戳。"""
    return int(time.time() * 1000)


# ---- WebSocket 流名称（09 §3.3）----


def kline_streams(symbols: Iterable[str], interval: str = "1m") -> list[str]:
    """构造 K 线流名称（09 §3.3、07 §3.1）。

    币安要求流名称使用小写交易对：`<symbol>@kline_<interval>`。

    Args:
        symbols: 交易对集合（如 `{"BTCUSDT", "SOLUSDT"}`）。
        interval: K 线周期，默认 `1m`。

    Returns:
        排序后的流名称列表。
    """
    return [f"{symbol.lower()}@kline_{interval}" for symbol in sorted(symbols)]


def agg_trade_streams(symbols: Iterable[str]) -> list[str]:
    """构造归集逐笔流名称 `<symbol>@aggTrade`（09 §3.3、07 §3.1）。"""
    return [f"{symbol.lower()}@aggTrade" for symbol in sorted(symbols)]


def book_ticker_streams(symbols: Iterable[str]) -> list[str]:
    """构造最优买卖价流名称 `<symbol>@bookTicker`（09 §3.3、07 §3.1）。"""
    return [f"{symbol.lower()}@bookTicker" for symbol in sorted(symbols)]


def reconnect_delay_ms(
    attempt: int, *, base_ms: int = _RECONNECT_BASE_MS, cap_ms: int = _RECONNECT_CAP_MS
) -> int:
    """第 `attempt` 次重连前的等待时间（07 §3.1）。

    Args:
        attempt: 连续失败次数，从 1 开始。
        base_ms: 首次等待毫秒数。
        cap_ms: 等待上限毫秒数。

    Returns:
        等待毫秒数；`attempt <= 0` 时为 0。
    """
    if attempt <= 0:
        return 0
    return min(cap_ms, base_ms * (1 << (attempt - 1)))


def missing_minutes(
    *, last_open_time_ms: int, now_ms: int, interval_ms: int = _KLINE_INTERVAL_MS
) -> list[int]:
    """断线期间缺失的、已收盘 K 线的 open_time 列表（07 §3.2）。

    Args:
        last_open_time_ms: 断线前收到的最后一根 K 线 open_time（毫秒）。
        now_ms: 当前 UTC 毫秒。
        interval_ms: K 线周期毫秒，默认 60000。

    Returns:
        缺失 K 线的 open_time 升序列表；当前分钟尚未收盘时不计入。
    """
    if last_open_time_ms <= 0:
        return []
    first = last_open_time_ms + interval_ms
    last_closed = (now_ms // interval_ms) * interval_ms - interval_ms
    if last_closed < first:
        return []
    return list(range(first, last_closed + interval_ms, interval_ms))


# ---- 组合流行情消费（07 §3.1、§3.2）----

OnMessage = Callable[[dict[str, Any]], None]
OnReconnect = Callable[[int, int], None]
FeedReason = Literal["rotate", "stop"]


@dataclass
class FeedStats:
    """单条行情连接的统计（07 §3.1，供日志与 R10 判定使用）。"""

    messages: int = 0
    reconnects: int = 0
    rotated: int = 0
    last_event_ms: int = 0
    connected_ms: int = 0
    by_event: Counter[str] = field(default_factory=Counter)


class MarketDataFeed:
    """组合流行情消费循环：订阅、断线重连、23 小时轮换（07 §3.1、§3.2）。

    断线（网络中断、交易所 24 小时断连、连接异常）后按指数退避重连；重连成功后
    调用 `on_reconnect(断线前最后事件时间, 当前时间)`，由上层用 REST 回补缺口。
    23 小时轮换时先建立新连接，再关闭旧连接（07 §3.1）。

    Args:
        endpoints: 环境端点配置（`paper` 模式用 testnet）。
        streams: 组合流名称列表，可由 `kline_streams` 等构造。
        on_message: 每收到一条流消息（已解包组合流的 `data` 字段）调用一次。
        on_reconnect: 重连成功后调用，参数为 (断线前最后事件毫秒, 当前毫秒)。
        max_attempts: 连续重连失败上限；None 表示不设限。
        clock: UTC 毫秒时间源（测试注入）。
        rotate_ms: 主动轮换周期毫秒，默认 23 小时。

    Raises:
        ValueError: `streams` 为空。
    """

    def __init__(
        self,
        *,
        endpoints: Endpoints,
        streams: list[str],
        on_message: OnMessage,
        on_reconnect: OnReconnect | None = None,
        max_attempts: int | None = None,
        clock: Callable[[], int] = now_ms,
        rotate_ms: int = _ROTATE_MS,
    ) -> None:
        if not streams:
            raise ValueError("streams 不可为空")
        self._url = endpoints.ws_stream_url(streams)
        self._on_message = on_message
        self._on_reconnect = on_reconnect
        self._max_attempts = max_attempts
        self._clock = clock
        self._rotate_ms = rotate_ms
        self.stats = FeedStats()

    @property
    def url(self) -> str:
        """组合流 WebSocket 地址（09 §2）。"""
        return self._url

    async def run(self, *, max_runtime_ms: int | None = None) -> FeedStats:
        """建立连接并持续消费，直到达到 `max_runtime_ms` 或重连次数超限。

        Args:
            max_runtime_ms: 最长运行毫秒数；None 表示无限运行（实盘）。

        Returns:
            累计统计（含重连与轮换次数）。

        Raises:
            RuntimeError: 连续重连次数超过 `max_attempts`。
        """
        deadline_ms = None if max_runtime_ms is None else self._clock() + max_runtime_ms
        conn = await self._connect(attempt=0, deadline_ms=deadline_ms)
        while True:
            try:
                reason = await self._consume(conn, deadline_ms=deadline_ms)
            except Exception:  # noqa: BLE001 - 任何传输异常都必须进入重连流程
                await conn.close()
                conn = await self._connect(attempt=0, is_reconnect=True, deadline_ms=deadline_ms)
                continue
            if reason == "stop":
                await conn.close()
                return self.stats
            old = conn
            # 23 小时轮换：先建立新连接，成功后再关闭旧连接（07 §3.1）
            conn = await self._connect(attempt=0)
            await old.close()
            self.stats.rotated += 1

    async def _connect(
        self, *, attempt: int, is_reconnect: bool = False, deadline_ms: int | None = None
    ) -> WsConnection:
        """按指数退避建立连接，直到成功（07 §3.1）。

        Args:
            attempt: 起始失败计数（0 表示首次尝试）。
            is_reconnect: 是否为断线后的重连；成功后累加重连次数并触发缺口回补回调。
            deadline_ms: 运行期限；为 None 表示不限期（实盘）。

        Returns:
            已建立的连接。

        Raises:
            RuntimeError: 连续失败次数超过 `max_attempts`，或在运行期限内未能恢复。
        """
        while True:
            attempt += 1
            conn = WsConnection(self._url)
            try:
                await conn.connect()
            except Exception:  # noqa: BLE001 - 连接阶段的异常一律计入重试
                if self._max_attempts is not None and attempt > self._max_attempts:
                    raise RuntimeError(f"行情连接连续 {attempt} 次连接失败") from None
                if deadline_ms is not None and self._clock() >= deadline_ms:
                    raise RuntimeError("行情连接在运行期限内未能恢复") from None
                await asyncio.sleep(reconnect_delay_ms(attempt) / 1000)
                continue
            self.stats.connected_ms = self._clock()
            if is_reconnect:
                self.stats.reconnects += 1
                if self._on_reconnect is not None:
                    self._on_reconnect(self.stats.last_event_ms, self._clock())
            return conn

    async def _consume(self, conn: WsConnection, *, deadline_ms: int | None) -> FeedReason:
        """消费一条连接直到需要轮换或到达运行期限（07 §3.1）。"""
        async for message in conn:
            data = message.get("data")
            payload = data if isinstance(data, dict) else message
            if not payload:
                continue
            self.stats.messages += 1
            event = str(payload.get("e", ""))
            if event:
                self.stats.by_event[event] += 1
            event_ms = payload.get("E")
            if isinstance(event_ms, int):
                self.stats.last_event_ms = max(self.stats.last_event_ms, event_ms)
            self._on_message(payload)
            now = self._clock()
            if deadline_ms is not None and now >= deadline_ms:
                return "stop"
            if now - self.stats.connected_ms >= self._rotate_ms:
                return "rotate"
        raise ConnectionError("行情连接已被对端关闭")


class KlineGapFiller:
    """断线后按 REST K 线补齐缺口（07 §3.2）。

    Args:
        client: 币安客户端（`trade.live.exchange.ExchangeClient`）。
    """

    def __init__(self, client: ExchangeClient) -> None:
        self._client = client

    async def fill(
        self, symbol: str, *, last_open_time_ms: int, now_ms: int
    ) -> list[dict[str, Any]]:
        """拉取断线期间缺失的已收盘 1m K 线。

        Args:
            symbol: 交易对。
            last_open_time_ms: 断线前收到的最后一根 K 线 open_time（毫秒）。
            now_ms: 当前 UTC 毫秒。

        Returns:
            缺失 K 线列表，每项含 `open_time_ms`、`open`、`high`、`low`、`close`、
            `volume`、`quote_volume`，按时间升序。
        """
        missing = missing_minutes(last_open_time_ms=last_open_time_ms, now_ms=now_ms)
        if not missing:
            return []
        rows = await self._client.klines(
            symbol,
            "1m",
            limit=min(len(missing), _KLINE_LIMIT),
            start_time=missing[0],
            end_time=missing[-1] + _KLINE_INTERVAL_MS - 1,
        )
        bars: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 9:
                continue
            bars.append(
                {
                    "open_time_ms": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "quote_volume": float(row[7]),
                }
            )
        return bars
