"""X2 做空多头拥挤的短期暴涨币（docs/13 §10）。

规格：1 号第八部分 X2；只出报告，不冻结、不进入最终检验。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import (
    alt_universe,
    flat_stop_targets,
    short_exclusions,
)

# 候选门槛（1 号第八部分 X2）：收益位于前 30%、资金费位于前 20%（即 ≥ 80% 分位）
_RETURN_QUANTILE = 0.70
_FUNDING_QUANTILE = 0.80
# 资金费窗口固定 3 天（1 号第八部分 X2）
_FUNDING_HOURS = 3 * 24


class ShortCrowdedPump:
    """过去 L 天大涨且资金费显著为正（多头拥挤）的币，按资金费从高到低做空前 K 个。"""

    name = "X2_short_crowded_pump"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 24

    def __init__(
        self,
        *,
        lookback_days: int,
        top_k: int,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def candidates(self, view: View) -> list[str]:
        """满足“短期暴涨 + 资金费高且为正”的币，按资金费从高到低排序。"""
        pool = alt_universe(view, self.universe_top_n)
        returns: dict[str, float] = {}
        funding: dict[str, float] = {}
        for symbol in pool:
            ret = view.log_return(symbol, self.lookback_days * 24)
            rate = view.funding_hourly_mean(symbol, _FUNDING_HOURS)
            if ret is None or rate is None:
                continue
            returns[symbol] = ret
            funding[symbol] = rate
        if len(returns) < 2:
            return []
        return_threshold = float(np.quantile(np.asarray(list(returns.values())), _RETURN_QUANTILE))
        funding_threshold = float(
            np.quantile(np.asarray(list(funding.values())), _FUNDING_QUANTILE)
        )
        picked = [
            symbol
            for symbol in pool
            if symbol in returns
            and returns[symbol] >= return_threshold
            and funding[symbol] >= funding_threshold
            and funding[symbol] > 0.0
        ]
        picked.sort(key=lambda symbol: funding[symbol], reverse=True)
        return picked

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """按资金费排序取前 K 个做空；不再满足条件的仓位在本次调仓平掉。"""
        pool = alt_universe(view, self.universe_top_n)
        excluded = short_exclusions(view, pool)
        picked = [symbol for symbol in self.candidates(view) if symbol not in excluded]
        return Decision(
            tuple(flat_stop_targets(picked[: self.top_k], -1, self.stop_max)),
            EXPOSURE_DIRECTIONAL,
        )


def build(params: Mapping[str, float], *, stop_max: float) -> ShortCrowdedPump:
    """从网格参数构造 X2。"""
    return ShortCrowdedPump(
        lookback_days=int(params["lookback_days"]),
        top_k=int(params["top_k"]),
        stop_max=stop_max,
    )
