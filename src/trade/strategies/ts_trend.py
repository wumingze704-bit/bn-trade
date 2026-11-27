"""R3 时间序列趋势（方向性，docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import EXPOSURE_DIRECTIONAL, Decision, Target
from trade.research.view import View
from trade.strategies.common import top_volume_universe


class TsTrend:
    """过去 L 天收益 > 0 做多、< 0 做空；按波动等风险；日线 ATR 止损。"""

    name = "R3_ts_trend"
    exposure = EXPOSURE_DIRECTIONAL

    def __init__(
        self,
        *,
        lookback_days: int,
        btc_filter: bool,
        rebalance_hours: int = 24,
        stop_mult: float = 3.0,
        stop_max: float = 0.15,
        top_n: int = 8,
        atr_period: int = 14,
    ) -> None:
        self.lookback_days = lookback_days
        self.btc_filter = btc_filter
        self.rebalance_hours = rebalance_hours
        self.stop_mult = stop_mult
        self.stop_max = stop_max
        self.top_n = top_n
        self.atr_period = atr_period

    def universe(self, view: View) -> list[str]:
        """BTC、ETH 与当月 U(R) 成交额前 8，按去重后的顺序。"""
        alts = top_volume_universe(view, view.universe_symbols(), self.top_n)
        symbols: list[str] = []
        for symbol in ("BTCUSDT", "ETHUSDT", *alts):
            if symbol not in symbols and view.has(symbol):
                symbols.append(symbol)
        return symbols

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """按趋势方向配置：模式 A 权重固定为 1，模式 B 由组合层按波动率定权重。"""
        hours = self.lookback_days * 24
        returns: dict[str, float] = {}
        for symbol in self.universe(view):
            value = view.log_return(symbol, hours)
            if value is None:
                continue
            returns[symbol] = value
        btc_return = returns.get("BTCUSDT")
        picks: list[tuple[str, int]] = []
        for symbol, value in returns.items():
            if value == 0.0:
                continue
            if (
                self.btc_filter
                and symbol != "BTCUSDT"
                and btc_return is not None
                and (value > 0.0) != (btc_return > 0.0)
            ):
                continue
            picks.append((symbol, 1 if value > 0.0 else -1))
        targets: list[Target] = []
        for symbol, side in picks:
            price = view.close(symbol)
            atr = view.atr_daily(symbol, self.atr_period)
            if price is None or price <= 0.0 or atr is None:
                continue
            stop = min(self.stop_mult * atr / price, self.stop_max)
            if stop <= 0.0:
                continue
            # 不再叠加 1/波动 权重：组合层模式 B 已按波动率定权重，模式 A 权重固定为 1（docs/13 §3）
            targets.append(Target(symbol=symbol, side=side, sl=stop, weight=1.0))
        return Decision(tuple(targets), EXPOSURE_DIRECTIONAL)


def build(params: Mapping[str, float], *, stop_max: float) -> TsTrend:
    """从网格参数构造 R3。"""
    return TsTrend(
        lookback_days=int(params["lookback_days"]),
        btc_filter=bool(int(params["btc_filter"])),
        stop_max=stop_max,
    )
