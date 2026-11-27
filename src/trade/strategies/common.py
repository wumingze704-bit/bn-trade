"""策略公用工具：打分、极值选择、缓冲区与 ATR 止损（docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from trade.research.interface import Target
from trade.research.view import View


def cross_section_zscore(scores: Mapping[str, float]) -> dict[str, float]:
    """截面 z 分数（标准差为 0 时全部置 0）。"""
    if not scores:
        return {}
    values = np.asarray(list(scores.values()), dtype="float64")
    std = float(values.std(ddof=1)) if values.size > 1 else 0.0
    if std <= 0.0:
        return {symbol: 0.0 for symbol in scores}
    mean = float(values.mean())
    return {symbol: (value - mean) / std for symbol, value in scores.items()}


def funding_quantiles(values: Mapping[str, float]) -> tuple[float, float]:
    """截面资金费率 10% / 90% 分位。"""
    if not values:
        return 0.0, 0.0
    array = np.asarray(list(values.values()), dtype="float64")
    return float(np.quantile(array, 0.10)), float(np.quantile(array, 0.90))


def atr_stop(
    view: View, symbol: str, *, bucket_hours: int, period: int, mult: float, max_stop: float
) -> float | None:
    """`mult × ATR / 价格`，封顶 max_stop；数据不足返回 None（不静默补值）。"""
    price = view.close(symbol)
    if price is None or price <= 0.0:
        return None
    atr = view.atr_hours(symbol, period=period, bucket_hours=bucket_hours)
    if atr is None:
        return None
    return min(mult * atr / price, max_stop)


def select_extremes(
    scores: Mapping[str, float],
    *,
    k: int,
    buffer_k: int,
    held: Mapping[str, int],
    high_side_long: bool,
    stop_for,
    require_long_positive: bool = False,
    require_short_negative: bool = False,
) -> list[Target]:
    """按分数选多空：高分做多（high_side_long）或低分做多。

    Args:
        scores: symbol → 分数。
        k: 每侧目标数量。
        buffer_k: 缓冲区名次（已持有且仍在 2K 名内继续持有）。
        held: 当前持仓方向。
        high_side_long: True 表示分数越高越适合做多（反之做空）。
        stop_for: symbol → 止损距离（None 表示数据不足，跳过）。
        require_long_positive: 只做多打分 > 0 的币（R1 规格）。
        require_short_negative: 只做空打分 < 0 的币（R1 规格）。

    Returns:
        目标仓位列表（先多后空，按分数强度排序）。
    """
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    long_ranked = ordered if high_side_long else list(reversed(ordered))
    short_ranked = list(reversed(long_ranked))
    longs = _pick(
        long_ranked,
        k=k,
        buffer_k=buffer_k,
        held=held,
        side=1,
        stop_for=stop_for,
        require_positive=require_long_positive,
    )
    shorts = _pick(
        short_ranked,
        k=k,
        buffer_k=buffer_k,
        held=held,
        side=-1,
        stop_for=stop_for,
        require_negative=require_short_negative,
    )
    return [*longs, *shorts]


def _pick(
    ordered: list[tuple[str, float]],
    *,
    k: int,
    buffer_k: int,
    held: Mapping[str, int],
    side: int,
    stop_for,
    require_positive: bool = False,
    require_negative: bool = False,
) -> list[Target]:
    def allowed(value: float) -> bool:
        if require_positive and value <= 0.0:
            return False
        return not (require_negative and value >= 0.0)

    picked: list[str] = []
    for symbol, value in ordered:
        if len(picked) >= k:
            break
        if not allowed(value):
            continue
        if held.get(symbol, 0) not in (0, side):
            continue
        if stop_for(symbol) is not None:
            picked.append(symbol)
    top = [
        symbol
        for symbol, value in ordered[:buffer_k]
        if allowed(value) and held.get(symbol) == side
    ]
    for symbol in top:
        if len(picked) >= k + buffer_k:
            break
        if symbol not in picked and stop_for(symbol) is not None:
            picked.append(symbol)
    targets: list[Target] = []
    for symbol in picked:
        stop = stop_for(symbol)
        if stop is None:
            continue
        targets.append(Target(symbol=symbol, side=side, sl=stop, weight=1.0))
    return targets


def top_volume_universe(view: View, symbols: frozenset[str], top_n: int) -> list[str]:
    """按 30 天日成交额中位数取前 `top_n`。"""
    scored: list[tuple[float, str]] = []
    for symbol in symbols:
        value = view.median_daily_quote_volume(symbol, 30)
        if value is not None:
            scored.append((value, symbol))
    scored.sort(reverse=True)
    return [symbol for _, symbol in scored[:top_n]]
