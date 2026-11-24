"""策略视角：只暴露决策时刻之前已收盘的数据（红线 1）。

规格：docs/13-研究框架.md §5
"""

from __future__ import annotations

import numpy as np

from trade.data.universe import SymbolMeta
from trade.research.data import DAY_MS, HOUR_MS, Bars, MarketData


class LookaheadError(RuntimeError):
    """策略请求决策时刻之后的数据。"""


class View:
    """t 时刻的策略视角：`open_time + 1h <= now_ms` 的 K 线才可见。"""

    def __init__(self, market: MarketData, now_ms: int) -> None:
        self.market = market
        self.now_ms = int(now_ms)

    def require_closed(self, ts_ms: int) -> None:
        """校验某个时刻的 1h K 线已收盘，否则抛出 LookaheadError。"""
        if ts_ms + HOUR_MS > self.now_ms:
            raise LookaheadError(f"请求了未收盘的 K 线：{ts_ms}（决策时刻 {self.now_ms}）")

    def closed_index(self, symbol: str) -> tuple[Bars, int]:
        """返回 (bars, 已收盘根数)。"""
        bars = self.market.bars(symbol)
        return bars, bars.closed_count(self.now_ms)

    def close(self, symbol: str, offset: int = 0) -> float | None:
        """第 offset 根已收盘 K 线的收盘价（0 = 最近一根）。"""
        bars, count = self.closed_index(symbol)
        index = count - 1 - offset
        if index < 0:
            return None
        return float(bars.close[index])

    def log_return(self, symbol: str, hours: int, end_offset: int = 0) -> float | None:
        """最近 `hours` 根已收盘 1h 的对数收益（可为负）。"""
        bars, count = self.closed_index(symbol)
        end = count - 1 - end_offset
        start = end - hours
        if start < 0 or end < 0:
            return None
        if bars.close[start] <= 0.0 or bars.close[end] <= 0.0:
            return None
        return float(np.log(bars.close[end] / bars.close[start]))

    def hourly_vol(self, symbol: str, hours: int, end_offset: int = 0) -> float | None:
        """最近 `hours` 根已收盘 1h 对数收益的标准差（可再往前错开 end_offset 根）。"""
        bars, count = self.closed_index(symbol)
        end = count - end_offset
        if end < hours + 1:
            return None
        closes = bars.close[end - hours - 1 : end]
        returns = np.diff(np.log(closes))
        value = float(np.std(returns, ddof=1))
        return value if np.isfinite(value) else None

    def mean_quote_volume(self, symbol: str, hours: int) -> float | None:
        """最近 `hours` 根已收盘 1h 的平均成交额（USDT）。"""
        bars, count = self.closed_index(symbol)
        if count < hours:
            return None
        value = float(np.mean(bars.quote_volume[count - hours : count]))
        return value if np.isfinite(value) else None

    def median_daily_quote_volume(self, symbol: str, days: int) -> float | None:
        """最近 `days` 个已收盘日线的成交额中位数。"""
        bars = self.market.daily.get(symbol)
        if bars is None:
            return None
        count = int(np.searchsorted(bars.open_time, self.now_ms - DAY_MS, side="right"))
        if count < days:
            return None
        return float(np.median(bars.quote_volume[count - days : count]))

    def daily_returns(self, symbol: str, days: int) -> tuple[np.ndarray, np.ndarray] | None:
        """最近 `days` 个已收盘自然日的日对数收益与对应日期（docs/13 §3）。

        日线 `open_time` 为当日 00:00；只有当 `open_time + 1d <= now_ms` 的日线才视为已收盘，
        与 1h 视角同一规则（红线 1）。

        Args:
            symbol: 币种。
            days: 需要的收益个数（每个收益需要前一日收盘价）。

        Returns:
            (日期 open_time 数组, 日对数收益数组)；日线缺失或历史不足 `days + 1` 根时返回 None。
        """
        bars = self.market.daily.get(symbol)
        if bars is None:
            return None
        count = int(np.searchsorted(bars.open_time, self.now_ms - DAY_MS, side="right"))
        if count < days + 1:
            return None
        closes = bars.close[count - days - 1 : count]
        if np.any(closes <= 0.0):
            return None
        returns = np.diff(np.log(closes))
        return bars.open_time[count - days : count], returns

    def funding_hourly_mean(self, symbol: str, hours: int) -> float | None:
        """过去 `hours` 小时的平均每小时资金费率；没有结算返回 None。"""
        series = self.market.funding_series(symbol)
        if series is None:
            return None
        return series.mean_between(self.now_ms - hours * HOUR_MS, self.now_ms)

    def listing_age_days(self, symbol: str) -> float | None:
        """该币在已收盘数据中的上市天数（首根 1h 至今）。"""
        bars = self.market.bars(symbol)
        if bars.open_time.size == 0:
            return None
        first = int(bars.open_time[0])
        return (self.now_ms - first) / DAY_MS

    def atr_hours(self, symbol: str, period: int, bucket_hours: int) -> float | None:
        """按 `bucket_hours` 聚合 1h K 线后的 ATR（绝对价格，docs/13 §5）。"""
        bars, count = self.closed_index(symbol)
        need = period * bucket_hours + bucket_hours
        if count < need:
            return None
        high, low, close = _resample(
            bars.open_time[count - need : count],
            bars.high[count - need : count],
            bars.low[count - need : count],
            bars.close[count - need : count],
            bucket_hours * HOUR_MS,
        )
        return _atr(high, low, close, period)

    def atr_daily(self, symbol: str, period: int) -> float | None:
        """日线 ATR（绝对价格，docs/13 §5）。"""
        bars = self.market.daily.get(symbol)
        if bars is None:
            return None
        count = int(np.searchsorted(bars.open_time, self.now_ms - DAY_MS, side="right"))
        if count < period + 1:
            return None
        return _atr(bars.high[:count], bars.low[:count], bars.close[:count], period)

    def universe_symbols(self) -> frozenset[str]:
        """当月选币快照 U(R)。"""
        return self.market.universe.symbols_at(self.now_ms)

    def meta(self, symbol: str) -> SymbolMeta:
        """该币的交易规则快照。"""
        return self.market.rule(symbol)

    def mark_close(self, symbol: str, offset: int = 0) -> float | None:
        """第 offset 根已收盘标记价格 K 线的收盘价（0 = 最近一根）。"""
        mark = self.market.mark_bars(symbol)
        if mark is None:
            return None
        count = int(np.searchsorted(mark.open_time, self.now_ms - HOUR_MS, side="right"))
        index = count - 1 - offset
        if index < 0:
            return None
        return float(mark.close[index])

    def has_mark(self, symbol: str) -> bool:
        """是否有该币的标记价格数据（止损触发基准，docs/13 §4）。"""
        return self.market.mark_bars(symbol) is not None

    def funding_available(self, symbol: str, hours: int = 7 * 24) -> bool:
        """过去 `hours` 小时是否有资金费结算记录（1 号 B 第 1 条：没有则不允许开仓）。"""
        return self.funding_hourly_mean(symbol, hours) is not None

    def has(self, symbol: str) -> bool:
        """研究数据中是否有该币。"""
        return symbol in self.market.hourly


def _resample(
    open_time: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    bucket_ms: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按 `open_time // bucket_ms` 聚合 K 线（取组内最高、最低与最后收盘）。"""
    buckets = open_time // bucket_ms
    starts = np.concatenate([[0], np.nonzero(buckets[1:] != buckets[:-1])[0] + 1])
    ends = np.concatenate([starts[1:], [open_time.size]])
    highs = np.array([high[start:end].max() for start, end in zip(starts, ends, strict=True)])
    lows = np.array([low[start:end].min() for start, end in zip(starts, ends, strict=True)])
    closes = np.array([close[end - 1] for end in ends])
    return highs, lows, closes


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> float | None:
    """Wilder 风格的简单平均真实波幅（ATR，取最近 period 根）。"""
    if high.size < period + 1:
        return None
    prev_close = close[:-1]
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )
    value = float(np.mean(true_range[-period:]))
    return value if np.isfinite(value) and value > 0.0 else None
