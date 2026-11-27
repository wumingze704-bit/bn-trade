"""X1 只在 BTC 下降趋势时做空山寨篮子（docs/13 §10）。

规格：1 号第八部分 X1；只出报告，不冻结、不进入最终检验。
"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision
from trade.research.view import View
from trade.strategies.common import alt_universe, flat_stop_targets, short_exclusions


class ShortBasketBtcRegime:
    """X0 的篮子，只在 BTCUSDT 最近已收盘日线收盘价低于 `ma_days` 日均线时持有。

    每 24 小时检查一次状态（UTC 00 点后第一根 1h），只用已收盘日线。
    """

    name = "X1_short_basket_btc_regime"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 24

    def __init__(
        self,
        *,
        basket_n: int,
        ma_days: int,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.basket_n = basket_n
        self.ma_days = ma_days
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def bearish(self, view: View) -> bool:
        """BTC 是否处于下降趋势（低于日线均线）。

        Raises:
            ValueError: BTCUSDT 日线不足 `ma_days` 根（红线 12：不得当作牛市静默跳过）。
        """
        below = view.daily_close_below_ma("BTCUSDT", self.ma_days)
        if below is None:
            raise ValueError(
                f"{view.now_ms} BTCUSDT 日线不足 {self.ma_days} 根，无法判断 X1 的市场状态"
                "（docs/13 §10，红线 12）"
            )
        return below

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """熊市状态持有 X0 的篮子，否则在该调仓时刻全部平掉。"""
        if not self.bearish(view):
            return Decision((), EXPOSURE_DIRECTIONAL)
        pool = alt_universe(view, self.universe_top_n)
        excluded = short_exclusions(view, pool)
        picked = [symbol for symbol in pool if symbol not in excluded][: self.basket_n]
        return Decision(tuple(flat_stop_targets(picked, -1, self.stop_max)), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> ShortBasketBtcRegime:
    """从网格参数构造 X1。"""
    return ShortBasketBtcRegime(
        basket_n=int(params["basket_n"]),
        ma_days=int(params["ma_days"]),
        stop_max=stop_max,
    )
