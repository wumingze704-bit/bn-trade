"""R8 波动率择时的横截面动量（中性，docs/13 §5）。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision, Target
from trade.research.view import View
from trade.strategies.common import atr_stop, select_extremes

_ANNUAL_HOURS = 24 * 365


class VolTimedMomentum:
    """动量选币 + 按组合波动估计缩放暴露（权重 ≤ 1）。"""

    name = "R8_vol_timed_momentum"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        target_annual_vol: float,
        top_k: int = 5,
        rebalance_hours: int = 168,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        score_lag_hours: int = 4,
        vol_hours: int = 24,
    ) -> None:
        self.lookback_days = lookback_days
        self.target_annual_vol = target_annual_vol
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.score_lag_hours = score_lag_hours
        self.vol_hours = vol_hours

    def momentum_score(self, view: View, symbol: str) -> float | None:
        """t 统计量量纲的动量（与 R6 的动量分量一致）。"""
        hours = self.lookback_days * 24
        ret = view.log_return(symbol, hours, end_offset=self.score_lag_hours)
        vol = view.hourly_vol(symbol, hours, end_offset=self.score_lag_hours)
        if ret is None or vol is None or vol <= 0.0:
            return None
        return ret / (vol * math.sqrt(hours))

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """动量选币后按近 24 小时波动估计缩放权重。"""
        scores: dict[str, float] = {}
        for symbol in sorted(view.universe_symbols()):
            if not view.has(symbol):
                continue
            value = self.momentum_score(view, symbol)
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
        if not targets:
            return Decision((), EXPOSURE_NEUTRAL)
        vols: list[float] = []
        usable: list[Target] = []
        for target in targets:
            value = view.hourly_vol(target.symbol, self.vol_hours)
            if value is None or value <= 0.0:
                # 波动数据缺失的目标不参与（不按默认值补齐，红线 12）。
                continue
            vols.append(value)
            usable.append(target)
        if not usable:
            return Decision((), EXPOSURE_NEUTRAL)
        basket_vol = float(sum(vols) / len(vols)) * math.sqrt(_ANNUAL_HOURS)
        scale = 1.0 if basket_vol <= 0.0 else min(1.0, self.target_annual_vol / basket_vol)
        scaled = tuple(
            Target(symbol=t.symbol, side=t.side, sl=t.sl, weight=t.weight * scale) for t in usable
        )
        return Decision(scaled, EXPOSURE_NEUTRAL)


def build(params: Mapping[str, float], *, stop_max: float) -> VolTimedMomentum:
    """从网格参数构造 R8。"""
    return VolTimedMomentum(
        lookback_days=int(params["lookback_days"]),
        target_annual_vol=float(params["target_annual_vol"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
