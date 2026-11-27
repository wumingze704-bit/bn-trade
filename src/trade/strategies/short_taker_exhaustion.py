"""X4 做空主动买入衰竭的币（docs/13 §10）。

规格：1 号第八部分 X4；只出报告，不冻结、不进入最终检验。
"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import (
    alt_universe,
    cross_section_zscore,
    flat_stop_targets,
    short_exclusions,
)


class ShortTakerExhaustion:
    """打分 = 截面 z(主动买入成交额占比) + 截面 z(过去 D 天对数收益)，做空最高的 K 个。"""

    name = "X4_short_taker_exhaustion"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 24

    def __init__(
        self,
        *,
        window_days: int,
        top_k: int,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.window_days = window_days
        self.top_k = top_k
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def scores_for(self, view: View) -> dict[str, float]:
        """主动买入占比与价格涨幅的截面 z 分数之和；缺数据的币不参与。"""
        pool = alt_universe(view, self.universe_top_n)
        ratios: dict[str, float] = {}
        returns: dict[str, float] = {}
        for symbol in pool:
            ratio = view.taker_buy_ratio(symbol, self.window_days)
            ret = view.log_return(symbol, self.window_days * 24)
            if ratio is None or ret is None:
                continue
            ratios[symbol] = ratio
            returns[symbol] = ret
        if len(ratios) < 2:
            return {}
        ratio_z = cross_section_zscore(ratios)
        return_z = cross_section_zscore(returns)
        return {symbol: ratio_z[symbol] + return_z[symbol] for symbol in ratios}

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """做空打分最高的 K 个。"""
        scores = self.scores_for(view)
        if not scores:
            return Decision((), EXPOSURE_DIRECTIONAL)
        excluded = short_exclusions(view, alt_universe(view, self.universe_top_n))
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        picked = [symbol for symbol, _value in ordered if symbol not in excluded][: self.top_k]
        return Decision(tuple(flat_stop_targets(picked, -1, self.stop_max)), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> ShortTakerExhaustion:
    """从网格参数构造 X4。"""
    return ShortTakerExhaustion(
        window_days=int(params["window_days"]),
        top_k=int(params["top_k"]),
        stop_max=stop_max,
    )
