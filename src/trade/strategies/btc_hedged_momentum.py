"""R9 BTC 对冲的单边动量（方向性，docs/13 §5）。"""

from __future__ import annotations

import math
from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision, Target
from trade.research.view import View
from trade.strategies.common import atr_stop, select_extremes

_BTC = "BTCUSDT"
_ALTS_EXCLUDED = frozenset({"BTCUSDT", "ETHUSDT"})


class BtcHedgedMomentum:
    """按 BTC 中期趋势决定做多强势组或做空弱势组，并用 BTC 反向头寸对冲。"""

    name = "R9_btc_hedged_momentum"
    exposure = EXPOSURE_DIRECTIONAL

    def __init__(
        self,
        *,
        lookback_days: int,
        trend_days: int,
        top_k: int,
        rebalance_hours: int = 168,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        hedge_weight: float = 1.0,
        score_lag_hours: int = 4,
    ) -> None:
        self.lookback_days = lookback_days
        self.trend_days = trend_days
        self.top_k = top_k
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.hedge_weight = hedge_weight
        self.score_lag_hours = score_lag_hours

    def momentum_score(self, view: View, symbol: str) -> float | None:
        """t 统计量量纲的动量。"""
        hours = self.lookback_days * 24
        ret = view.log_return(symbol, hours, end_offset=self.score_lag_hours)
        vol = view.hourly_vol(symbol, hours, end_offset=self.score_lag_hours)
        if ret is None or vol is None or vol <= 0.0:
            return None
        return ret / (vol * math.sqrt(hours))

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """BTC 趋势决定方向，选山寨单边 K 个，再挂 BTC 反向对冲。"""
        btc_trend = view.log_return(_BTC, self.trend_days * 24)
        btc_stop = atr_stop(
            view,
            _BTC,
            bucket_hours=4,
            period=14,
            mult=self.stop_mult,
            max_stop=self.stop_max,
        )
        if btc_trend is None or btc_stop is None:
            return Decision((), EXPOSURE_DIRECTIONAL)
        scores: dict[str, float] = {}
        for symbol in sorted(view.universe_symbols()):
            if symbol in _ALTS_EXCLUDED or not view.has(symbol):
                continue
            value = self.momentum_score(view, symbol)
            if value is not None:
                scores[symbol] = value
        if not scores:
            return Decision((), EXPOSURE_DIRECTIONAL)
        side = 1 if btc_trend > 0.0 else -1

        def stop_for(symbol: str) -> float | None:
            return atr_stop(
                view,
                symbol,
                bucket_hours=4,
                period=14,
                mult=self.stop_mult,
                max_stop=self.stop_max,
            )

        picked = select_extremes(
            scores,
            k=self.top_k,
            buffer_k=2 * self.top_k,
            held=held,
            high_side_long=True,
            stop_for=stop_for,
        )
        # 只保留策略方向那一侧：BTC 上涨取多头（强势组），下跌取空头（弱势组）。
        targets = [target for target in picked if target.side == side]
        targets.append(Target(symbol=_BTC, side=-side, sl=btc_stop, weight=self.hedge_weight))
        return Decision(tuple(targets), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> BtcHedgedMomentum:
    """从网格参数构造 R9。"""
    return BtcHedgedMomentum(
        lookback_days=int(params["lookback_days"]),
        trend_days=int(params["trend_days"]),
        top_k=int(params["top_k"]),
        rebalance_hours=int(params["rebalance_hours"]),
        stop_max=stop_max,
    )
