"""X3 做空永续持续溢价的币（docs/13 §10）。

规格：1 号第八部分 X3；只出报告，不冻结、不进入最终检验。
"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import alt_universe, flat_stop_targets, short_exclusions


class ShortPremiumRich:
    """打分 = 过去 W 小时溢价指数收盘均值；做空打分 > 0 且最高的 K 个。"""

    name = "X3_short_premium_rich"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 24

    def __init__(
        self,
        *,
        window_hours: int,
        top_k: int,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.window_hours = window_hours
        self.top_k = top_k
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def scores_for(self, view: View) -> dict[str, float]:
        """溢价指数均值打分；溢价数据缺失的币不参与（不按 0 计，红线 12）。"""
        scores: dict[str, float] = {}
        for symbol in alt_universe(view, self.universe_top_n):
            value = view.premium_mean(symbol, self.window_hours)
            if value is None or value <= 0.0:
                continue
            scores[symbol] = value
        return scores

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """做空溢价最高的 K 个（只取打分为正）。"""
        scores = self.scores_for(view)
        if not scores:
            return Decision((), EXPOSURE_DIRECTIONAL)
        excluded = short_exclusions(view, alt_universe(view, self.universe_top_n))
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        picked = [symbol for symbol, _value in ordered if symbol not in excluded][: self.top_k]
        return Decision(tuple(flat_stop_targets(picked, -1, self.stop_max)), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> ShortPremiumRich:
    """从网格参数构造 X3。"""
    return ShortPremiumRich(
        window_hours=int(params["window_hours"]),
        top_k=int(params["top_k"]),
        stop_max=stop_max,
    )
