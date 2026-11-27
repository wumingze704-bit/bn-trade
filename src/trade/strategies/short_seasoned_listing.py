"""R11 上线后半年内的山寨做空（方向性，只做空，docs/13 §5）。

规格：1 号第八部分 R11。
"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import (
    SHORT_MIN_LISTING_DAYS,
    alt_universe,
    flat_stop_targets,
    short_exclusions,
)

# BTC 200 日均线的长度（1 号第八部分 R11：各固定一个值、不做网格）
BTC_MA_DAYS = 200


class ShortSeasonedListing:
    """每 168 小时做空上线天数 ∈ [60, age_max] 且最年轻的前 K 个山寨。"""

    name = "R11_short_seasoned_listing"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 168

    def __init__(
        self,
        *,
        age_max: int,
        top_k: int,
        btc_ma_filter: bool,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.age_max = age_max
        self.top_k = top_k
        self.btc_ma_filter = btc_ma_filter
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def bull_market(self, view: View) -> bool:
        """BTC 最近已收盘日线收盘价是否高于 200 日均线（牛市环境则空仓）。

        Raises:
            ValueError: BTCUSDT 日线不足 200 根（红线 12：不得当作熊市静默开仓）。
        """
        below = view.daily_close_below_ma("BTCUSDT", BTC_MA_DAYS)
        if below is None:
            raise ValueError(
                f"{view.now_ms} BTCUSDT 日线不足 {BTC_MA_DAYS} 根，无法判断 R11 的市场状态"
                "（docs/13 §5，红线 12）"
            )
        return not below

    def candidates(self, view: View) -> list[str]:
        """上线天数 ∈ [60, age_max] 且通过资金费过滤的币，按上线天数从小到大排序。"""
        pool = alt_universe(view, self.universe_top_n)
        excluded = short_exclusions(view, pool)
        ages: dict[str, float] = {}
        for symbol in pool:
            age = view.listing_age_days(symbol)
            if age is None or age < float(SHORT_MIN_LISTING_DAYS) or age > float(self.age_max):
                continue
            if symbol in excluded:
                continue
            ages[symbol] = age
        return sorted(ages, key=lambda symbol: ages[symbol])

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """牛市环境空仓；否则做空最年轻的前 K 个候选。"""
        if self.btc_ma_filter and self.bull_market(view):
            return Decision((), EXPOSURE_DIRECTIONAL)
        picked = self.candidates(view)[: self.top_k]
        return Decision(tuple(flat_stop_targets(picked, -1, self.stop_max)), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> ShortSeasonedListing:
    """从网格参数构造 R11。"""
    return ShortSeasonedListing(
        age_max=int(params["age_max"]),
        top_k=int(params["top_k"]),
        btc_ma_filter=bool(int(params["btc_ma_filter"])),
        stop_max=stop_max,
    )
