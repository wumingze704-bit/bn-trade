"""R4 短期反转多空（中性，docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, select_extremes


class XsReversalLs:
    """过去 L 天收益最低的 K 个做多、最高的 K 个做空（排除缩量币）。"""

    name = "R4_xs_reversal_ls"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        top_k: int,
        rebalance_hours: int = 24,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        liquidity_days: int = 30,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.liquidity_days = liquidity_days

    def score(self, view: View, symbol: str) -> float | None:
        """反转打分 = −过去 L 天收益；今天成交额低于 30 天中位数一半时排除。"""
        ret = view.log_return(symbol, self.lookback_days * 24)
        if ret is None:
            return None
        hourly = view.mean_quote_volume(symbol, 24)
        median_daily = view.median_daily_quote_volume(symbol, self.liquidity_days)
        if hourly is None or median_daily is None:
            return None
        if hourly * 24.0 < 0.5 * median_daily:
            return None
        return -ret

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """选反转最强的多空各 K 个。"""
        scores: dict[str, float] = {}
        for symbol in sorted(view.universe_symbols()):
            if not view.has(symbol):
                continue
            value = self.score(view, symbol)
            if value is not None:
                scores[symbol] = value

        def stop_for(symbol: str) -> float | None:
            return atr_stop(
                view,
                symbol,
                bucket_hours=4,
                period=14,
                mult=self.stop_mult,
                max_stop=self.stop_max,
            )

        targets = select_extremes(
            scores,
            k=self.top_k,
            buffer_k=2 * self.top_k,
            held=held,
            high_side_long=True,
            stop_for=stop_for,
        )
        return Decision(tuple(targets), EXPOSURE_NEUTRAL)


def build(params: Mapping[str, float], *, stop_max: float) -> XsReversalLs:
    """从网格参数构造 R4。"""
    return XsReversalLs(
        lookback_days=int(params["lookback_days"]),
        top_k=int(params["top_k"]),
        stop_max=stop_max,
    )
