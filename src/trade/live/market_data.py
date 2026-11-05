"""实时行情管理：订阅范围、连接轮换、listenKey 续期与月度再平衡。

规格：docs/07-实盘与风控.md §3.1、§3.5
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

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

# 连接轮换周期（07 §3.1：23 小时主动新建连接）
_ROTATE_MS = 23 * 60 * 60 * 1000
# listenKey 续期间隔（07 §3.1：30 分钟）
_LISTEN_KEY_RENEW_MS = 30 * 60 * 1000


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
