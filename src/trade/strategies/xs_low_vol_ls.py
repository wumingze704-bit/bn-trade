"""R7 低波动异常多空（中性，docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, select_extremes


class XsLowVolLs:
    """打分 = −过去 W 天 1h 收益标准差：波动越低分数越高。"""

    name = "R7_xs_low_vol_ls"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        vol_days: int,
        top_k: int,
        rebalance_hours: int = 168,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        liquidity_days: int = 30,
    ) -> None:
        self.vol_days = vol_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.liquidity_days = liquidity_days

    def score(self, view: View, symbol: str) -> float | None:
        """低波动打分；缩量币返回 None（不进入截面）。"""
        vol = view.hourly_vol(symbol, self.vol_days * 24)
        if vol is None or vol <= 0.0:
            return None
        hourly = view.mean_quote_volume(symbol, 24)
        median_daily = view.median_daily_quote_volume(symbol, self.liquidity_days)
        if hourly is None or median_daily is None:
            return None
        if hourly * 24.0 < 0.5 * median_daily:
            return None
        return -vol

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """做多最低波动 K 个、做空最高波动 K 个。"""
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


def build(params: Mapping[str, float], *, stop_max: float) -> XsLowVolLs:
    """从网格参数构造 R7。"""
    return XsLowVolLs(
        vol_days=int(params["vol_days"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
