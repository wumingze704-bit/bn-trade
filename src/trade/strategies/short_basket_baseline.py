"""X0 无条件下做空山寨篮子（纯做空探索轨的基准，docs/13 §10）。

规格：1 号第八部分 X0；只出报告，不冻结、不进入最终检验。
"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import alt_universe, flat_stop_targets, short_exclusions


class ShortBasketBaseline:
    """每 168 小时按 30 天成交额取前 N 个山寨做空，不加择时与选币条件。"""

    name = "X0_short_basket_baseline"
    exposure = EXPOSURE_DIRECTIONAL
    # 168h 锚定 UTC 周一 00 点（docs/13 §5）
    rebalance_hours = 168

    def __init__(self, *, basket_n: int, stop_max: float, universe_top_n: int = 30) -> None:
        self.basket_n = basket_n
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """以成交额前 `basket_n` 个山寨为篮子，等权做空；止损固定为 `stop_max`（模式 B）。"""
        pool = alt_universe(view, self.universe_top_n)
        excluded = short_exclusions(view, pool)
        picked = [symbol for symbol in pool if symbol not in excluded][: self.basket_n]
        return Decision(tuple(flat_stop_targets(picked, -1, self.stop_max)), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> ShortBasketBaseline:
    """从网格参数构造 X0（供 registry 与测试使用）。"""
    return ShortBasketBaseline(basket_n=int(params["basket_n"]), stop_max=stop_max)
