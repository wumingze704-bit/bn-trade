"""R6 资金费调整的横截面动量（中性，docs/13 §5）。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, cross_section_zscore, select_extremes


class FundingAdjMomentum:
    """打分 = 截面 z(动量) − λ × 截面 z(过去 D 天平均小时化资金费)。

    量纲与 R1 不同：动量先除以 `1h 收益标准差 × √窗口小时数`，即 t 统计量，再与其他币比较。
    """

    name = "R6_funding_adj_momentum"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        funding_days: int,
        funding_weight: float,
        top_k: int,
        rebalance_hours: int = 168,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        score_lag_hours: int = 4,
    ) -> None:
        self.lookback_days = lookback_days
        self.funding_days = funding_days
        self.funding_weight = funding_weight
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.score_lag_hours = score_lag_hours

    def momentum_score(self, view: View, symbol: str) -> float | None:
        """t 统计量量纲的动量（无量纲，可为负）。"""
        hours = self.lookback_days * 24
        ret = view.log_return(symbol, hours, end_offset=self.score_lag_hours)
        vol = view.hourly_vol(symbol, hours, end_offset=self.score_lag_hours)
        if ret is None or vol is None or vol <= 0.0:
            return None
        return ret / (vol * math.sqrt(hours))

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """资金费作为连续惩罚项修正动量排序，选多空各 K 个。"""
        momentum: dict[str, float] = {}
        funding: dict[str, float] = {}
        for symbol in sorted(view.universe_symbols()):
            if not view.has(symbol):
                continue
            value = self.momentum_score(view, symbol)
            rate = view.funding_hourly_mean(symbol, self.funding_days * 24)
            # 资金费缺失的币不参与打分：既不按 0 计，也不用其他默认值（红线 12）。
            if value is None or rate is None:
                continue
            momentum[symbol] = value
            funding[symbol] = rate
        if not momentum:
            return Decision((), EXPOSURE_NEUTRAL)
        momentum_z = cross_section_zscore(momentum)
        funding_z = cross_section_zscore(funding)
        scores = {
            symbol: momentum_z[symbol] - self.funding_weight * funding_z[symbol]
            for symbol in momentum_z
        }

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


def build(params: Mapping[str, float], *, stop_max: float) -> FundingAdjMomentum:
    """从网格参数构造 R6。"""
    return FundingAdjMomentum(
        lookback_days=int(params["lookback_days"]),
        funding_days=int(params["funding_days"]),
        funding_weight=float(params["funding_weight"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
