"""R10 按流动性分层的动量（中性，docs/13 §5）。"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import atr_stop, cross_section_zscore, select_extremes


class LiquidityTieredMomentum:
    """动量在 30 天成交额分层的层内做 z 分数，再跨层选多空。"""

    name = "R10_liquidity_tiered_momentum"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        top_k: int,
        rebalance_hours: int = 168,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        score_lag_hours: int = 4,
        tiers: int = 3,
        liquidity_days: int = 30,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.score_lag_hours = score_lag_hours
        self.tiers = tiers
        self.liquidity_days = liquidity_days

    def momentum_score(self, view: View, symbol: str) -> float | None:
        """t 统计量量纲的动量。"""
        hours = self.lookback_days * 24
        ret = view.log_return(symbol, hours, end_offset=self.score_lag_hours)
        vol = view.hourly_vol(symbol, hours, end_offset=self.score_lag_hours)
        if ret is None or vol is None or vol <= 0.0:
            return None
        return ret / (vol * math.sqrt(hours))

    def scores_for(self, view: View) -> dict[str, float]:
        """层内 z 分数（层内不足 2 个币时该层不参与）。"""
        momentum: dict[str, float] = {}
        volume: dict[str, float] = {}
        for symbol in sorted(view.universe_symbols()):
            if not view.has(symbol):
                continue
            value = self.momentum_score(view, symbol)
            median_volume = view.median_daily_quote_volume(symbol, self.liquidity_days)
            if value is None or median_volume is None or median_volume <= 0.0:
                continue
            momentum[symbol] = value
            volume[symbol] = median_volume
        ordered = np.asarray(
            sorted(momentum, key=lambda symbol: volume[symbol], reverse=True), dtype=object
        )
        scores: dict[str, float] = {}
        for chunk in np.array_split(ordered, self.tiers):
            members = [str(symbol) for symbol in chunk]
            if len(members) < 2:
                continue
            scores.update(cross_section_zscore({symbol: momentum[symbol] for symbol in members}))
        return scores

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """按层内 z 分数选多空各 K 个。"""
        scores = self.scores_for(view)
        if not scores:
            return Decision((), EXPOSURE_NEUTRAL)

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


def build(params: Mapping[str, float], *, stop_max: float) -> LiquidityTieredMomentum:
    """从网格参数构造 R10。"""
    return LiquidityTieredMomentum(
        lookback_days=int(params["lookback_days"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
