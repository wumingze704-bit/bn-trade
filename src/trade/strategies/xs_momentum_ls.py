"""R1 截面动量多空（中性，docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View
from trade.strategies.common import (
    atr_stop,
    funding_quantiles,
    select_extremes,
)


class XsMomentumLs:
    """打分 = 过去 L 天对数收益（截止决策前 4 小时）÷ 过去 L 天 1h 收益标准差。"""

    name = "R1_xs_momentum_ls"
    exposure = EXPOSURE_NEUTRAL

    def __init__(
        self,
        *,
        lookback_days: int,
        top_k: int,
        rebalance_hours: int,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        min_listing_days: int = 90,
        funding_hours: int = 24,
        score_lag_hours: int = 4,
    ) -> None:
        self.lookback_days = lookback_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.min_listing_days = min_listing_days
        self.funding_hours = funding_hours
        self.score_lag_hours = score_lag_hours

    def score(self, view: View, symbol: str) -> float | None:
        """动量打分（无量纲）。"""
        hours = self.lookback_days * 24
        lookback = view.log_return(symbol, hours, end_offset=self.score_lag_hours)
        vol = view.hourly_vol(symbol, hours, end_offset=self.score_lag_hours)
        if lookback is None or vol is None or vol <= 0.0:
            return None
        return lookback / vol

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """选动量最高 / 最低的 K 个币，并应用资金费与上线天数过滤。"""
        universe = view.universe_symbols()
        scores: dict[str, float] = {}
        funding: dict[str, float] = {}
        ages: dict[str, float] = {}
        for symbol in sorted(universe):
            if not view.has(symbol):
                continue
            value = self.score(view, symbol)
            if value is None:
                continue
            scores[symbol] = value
            rate = view.funding_hourly_mean(symbol, self.funding_hours)
            if rate is not None:
                funding[symbol] = rate
            age = view.listing_age_days(symbol)
            if age is not None:
                ages[symbol] = age
        low, high = funding_quantiles(funding)
        exclude_long = {symbol for symbol, rate in funding.items() if rate > high and rate > 0.0}
        exclude_short = {symbol for symbol, rate in funding.items() if rate < low and rate < 0.0}
        exclude_short |= {
            symbol for symbol, age in ages.items() if age < float(self.min_listing_days)
        }
        filtered = {
            symbol: value
            for symbol, value in scores.items()
            if symbol not in exclude_long and symbol not in exclude_short
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
            filtered,
            k=self.top_k,
            buffer_k=2 * self.top_k,
            held=held,
            high_side_long=True,
            stop_for=stop_for,
        )
        return Decision(tuple(targets), EXPOSURE_NEUTRAL)


def build(params: Mapping[str, float], *, stop_max: float) -> XsMomentumLs:
    """从网格参数构造 R1（供 registry 与测试使用）。"""
    return XsMomentumLs(
        lookback_days=int(params["lookback_days"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
