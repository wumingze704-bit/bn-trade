"""分钟 K 线收盘判定、逐笔连续性与原子量聚合。

规格：docs/07-实盘与风控.md §3.2、§3.3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def agg_trade_gap(prev_id: int, curr_id: int) -> bool:
    """判断归集逐笔 ID 是否出现缺口（07 §3.2）。

    缺口定义为 `curr_id != prev_id + 1`；prev_id=0 表示首条，不判定缺口。
    """
    if prev_id == 0:
        return False
    return curr_id != prev_id + 1


def minute_complete(
    *, last_trade_ts_ms: int, minute_end_ms: int, now_ms: int, grace_ms: int = 300
) -> bool:
    """判断该分钟逐笔是否完整（07 §3.2）。

    收到 transact_time ≥ minute_end 的逐笔（属于下一分钟），或分钟结束已过
    `grace_ms`，视为完整。
    """
    return last_trade_ts_ms >= minute_end_ms or now_ms >= minute_end_ms + grace_ms


def aggregate_trades(
    trades: list[tuple[float, float, bool, int]], whale_thr: float
) -> dict[str, Any]:
    """把一分钟内的归集逐笔聚合为原子量（03 §2.3）。

    Args:
        trades: (price, qty, is_buyer_maker, ts_ms) 列表。
        whale_thr: 大单阈值（quote 金额）。

    Returns:
        原子量字段字典（不含 open_time 与 OHLC，由 K 线补齐）。
    """
    volume = 0.0
    quote_volume = 0.0
    count = 0
    taker_buy_quote = 0.0
    buy_quote = 0.0
    sell_quote = 0.0
    max_trade_quote = 0.0
    whale_buy_quote = 0.0
    whale_sell_quote = 0.0
    # 1 秒桶用于 max_buy_1s_quote（03 §2.3）
    buy_by_second: dict[int, float] = {}

    for price, qty, is_buyer_maker, ts_ms in trades:
        quote = price * qty
        volume += qty
        quote_volume += quote
        count += 1
        max_trade_quote = max(max_trade_quote, quote)
        second = ts_ms // 1000
        if is_buyer_maker:
            sell_quote += quote
            if quote >= whale_thr:
                whale_sell_quote += quote
        else:
            taker_buy_quote += quote
            buy_quote += quote
            buy_by_second[second] = buy_by_second.get(second, 0.0) + quote
            if quote >= whale_thr:
                whale_buy_quote += quote

    max_buy_1s_quote = max(buy_by_second.values()) if buy_by_second else 0.0
    return {
        "volume": volume,
        "quote_volume": quote_volume,
        "count": count,
        "taker_buy_quote_volume": taker_buy_quote,
        "buy_quote": buy_quote,
        "sell_quote": sell_quote,
        "max_trade_quote": max_trade_quote,
        "max_buy_1s_quote": max_buy_1s_quote,
        "whale_buy_quote": whale_buy_quote,
        "whale_sell_quote": whale_sell_quote,
    }


@dataclass
class BarBuilder:
    """逐币分钟聚合器（07 §3.2、§3.3）。

    累积 `kline` 与 `aggTrade` 事件，在分钟结束时产出一条 bar 记录。
    """

    symbol: str
    _trades: list[tuple[float, float, bool, int]] = field(default_factory=list)
    _kline: dict[str, Any] = field(default_factory=dict)
    _last_agg_id: int = 0

    def on_kline(self, kline: dict[str, Any]) -> None:
        """接收已收盘 K 线（07 §3.2）。"""
        self._kline = kline

    def on_agg_trade(
        self, *, agg_id: int, price: float, qty: float, is_buyer_maker: bool, ts_ms: int
    ) -> bool:
        """接收一条归集逐笔；返回是否检测到缺口（07 §3.2）。"""
        gap = agg_trade_gap(self._last_agg_id, agg_id)
        self._last_agg_id = agg_id
        self._trades.append((price, qty, is_buyer_maker, ts_ms))
        return gap

    def build_bar(self, *, open_time_ms: int, whale_thr: float) -> dict[str, Any]:
        """把当前累积的 K 线与逐笔组装为一条 bar（03 §2.3）。"""
        atomics = aggregate_trades(self._trades, whale_thr)
        bar = {
            "open_time": open_time_ms,
            "open": float(self._kline.get("open", 0.0)),
            "high": float(self._kline.get("high", 0.0)),
            "low": float(self._kline.get("low", 0.0)),
            "close": float(self._kline.get("close", 0.0)),
            **atomics,
        }
        self._reset()
        return bar

    def _reset(self) -> None:
        self._trades = []
        self._kline = {}
        self._last_agg_id = 0
