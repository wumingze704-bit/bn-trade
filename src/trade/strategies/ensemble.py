"""R5 组合：R1/R2/R4 的截面 z 分数等权相加（docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, cross_section_zscore, select_extremes
from trade.strategies.funding_carry_ls import FundingCarryLs
from trade.strategies.xs_momentum_ls import XsMomentumLs
from trade.strategies.xs_reversal_ls import XsReversalLs


class Ensemble:
    """成分参数事先固定，不拟合权重、不按结果筛选成分。"""

    name = "R5_ensemble"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        components: Mapping[str, Mapping[str, float]],
        top_k: int,
        rebalance_hours: int,
        stop_max: float,
    ) -> None:
        self.momentum = XsMomentumLs(
            lookback_days=int(components["xs_momentum_ls"]["lookback_days"]),
            top_k=int(components["xs_momentum_ls"]["top_k"]),
            rebalance_hours=int(rebalance_hours),
            stop_max=stop_max,
        )
        self.funding = FundingCarryLs(
            lookback_days=int(components["funding_carry_ls"]["lookback_days"]),
            top_k=int(components["funding_carry_ls"]["top_k"]),
            stop_max=stop_max,
        )
        self.reversal = XsReversalLs(
            lookback_days=int(components["xs_reversal_ls"]["lookback_days"]),
            top_k=int(components["xs_reversal_ls"]["top_k"]),
            stop_max=stop_max,
        )
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_max = stop_max

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """三个成分打分各自做截面 z 分数后等权相加。"""
        symbols = sorted(view.universe_symbols())
        parts: list[dict[str, float]] = []
        for strategy in (self.momentum, self.funding, self.reversal):
            raw = {
                symbol: value
                for symbol in symbols
                if view.has(symbol) and (value := strategy.score(view, symbol)) is not None
            }
            parts.append(cross_section_zscore(raw))
        common = set(parts[0]) & set(parts[1]) & set(parts[2])
        combined = {symbol: sum(part[symbol] for part in parts) for symbol in sorted(common)}

        def stop_for(symbol: str) -> float | None:
            return atr_stop(
                view, symbol, bucket_hours=4, period=14, mult=3.0, max_stop=self.stop_max
            )

        targets = select_extremes(
            combined,
            k=self.top_k,
            buffer_k=2 * self.top_k,
            held=held,
            high_side_long=True,
            stop_for=stop_for,
        )
        return Decision(tuple(targets), EXPOSURE_NEUTRAL)


def build(params: Mapping[str, object], *, stop_max: float) -> Ensemble:
    """从参数构造 R5（components 为三成分的固定参数）。"""
    components = params["components"]
    if not isinstance(components, Mapping):
        raise ValueError("R5 参数缺少 components 映射")
    return Ensemble(
        components={
            str(key): dict(value) for key, value in components.items() if isinstance(value, Mapping)
        },
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
