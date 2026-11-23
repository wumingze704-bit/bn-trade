"""R2 资金费拥挤多空（中性，docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, select_extremes


class FundingCarryLs:
    """按过去 D 天平均每小时资金费率排序：做空最高、做多最低。"""

    name = "R2_funding_carry_ls"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        top_k: int,
        rebalance_hours: int = 8,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max

    def score(self, view: View, symbol: str) -> float | None:
        """过去 D 天平均每小时资金费率（缺失返回 None）。"""
        return view.funding_hourly_mean(symbol, self.lookback_days * 24)

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """资金费率越高越拥挤 → 做空；越低（负得越多）→ 做多。"""
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
            high_side_long=False,
            stop_for=stop_for,
        )
        return Decision(tuple(targets), EXPOSURE_NEUTRAL)


def build(params: Mapping[str, float], *, stop_max: float) -> FundingCarryLs:
    """从网格参数构造 R2。"""
    return FundingCarryLs(
        lookback_days=int(params["lookback_days"]),
        top_k=int(params["top_k"]),
        stop_max=stop_max,
    )
