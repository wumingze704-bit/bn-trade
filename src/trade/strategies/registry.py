"""策略注册表：按家族名构造策略（docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping

from trade.research.interface import Strategy
from trade.strategies.alt_short_btc_long import build as build_alt_short_btc_long
from trade.strategies.btc_hedged_momentum import build as build_btc_hedged
from trade.strategies.ensemble import build as build_ensemble
from trade.strategies.funding_adj_momentum import build as build_funding_momentum
from trade.strategies.funding_carry_ls import build as build_funding
from trade.strategies.liquidity_tiered_momentum import build as build_liquidity_tiered
from trade.strategies.short_basket_baseline import build as build_short_basket
from trade.strategies.short_basket_btc_regime import build as build_short_basket_regime
from trade.strategies.short_crowded_pump import build as build_short_crowded_pump
from trade.strategies.short_premium_rich import build as build_short_premium_rich
from trade.strategies.short_seasoned_listing import build as build_short_seasoned_listing
from trade.strategies.short_taker_exhaustion import build as build_short_taker_exhaustion
from trade.strategies.ts_trend import build as build_trend
from trade.strategies.vol_timed_momentum import build as build_vol_timed
from trade.strategies.xs_low_vol_ls import build as build_low_vol
from trade.strategies.xs_momentum_ls import build as build_momentum
from trade.strategies.xs_reversal_ls import build as build_reversal

_BUILDERS = {
    "xs_momentum_ls": build_momentum,
    "funding_carry_ls": build_funding,
    "ts_trend": build_trend,
    "xs_reversal_ls": build_reversal,
    "ensemble": build_ensemble,
    "funding_adj_momentum": build_funding_momentum,
    "xs_low_vol_ls": build_low_vol,
    "vol_timed_momentum": build_vol_timed,
    "btc_hedged_momentum": build_btc_hedged,
    "liquidity_tiered_momentum": build_liquidity_tiered,
    "short_seasoned_listing": build_short_seasoned_listing,
    "alt_short_btc_long": build_alt_short_btc_long,
    "short_basket_baseline": build_short_basket,
    "short_basket_btc_regime": build_short_basket_regime,
    "short_crowded_pump": build_short_crowded_pump,
    "short_premium_rich": build_short_premium_rich,
    "short_taker_exhaustion": build_short_taker_exhaustion,
}


def build_strategy(
    family: str,
    params: Mapping[str, object],
    *,
    stop_max: float,
    components: Mapping[str, Mapping[str, object]] | None = None,
) -> Strategy:
    """按家族名与参数字典构造策略。

    Args:
        family: `xs_momentum_ls` / `funding_carry_ls` / `ts_trend` / `xs_reversal_ls` /
            `ensemble` / `funding_adj_momentum` / `xs_low_vol_ls` / `vol_timed_momentum` /
            `btc_hedged_momentum` / `liquidity_tiered_momentum`。
        params: 该组参数（来自 `configs/strategies/` 的网格）。
        stop_max: 止损距离硬上限（来自 research.yaml）。
        components: R5 的逐折成分参数（`{family: params}`）；其它家族不接受。

    Returns:
        Strategy 实例。

    Raises:
        ValueError: 未知家族、R5 缺少成分参数或其它家族传入了成分参数。
    """
    if family not in _BUILDERS:
        raise ValueError(f"未知策略家族：{family}（可用 {sorted(_BUILDERS)}）")
    builder = _BUILDERS[family]
    if family == "ensemble":
        return build_ensemble(params, stop_max=stop_max, components=components)
    if components is not None:
        raise ValueError(f"{family} 不接受 components（只有 R5 组合使用，1 号第八部分）")
    return builder(params, stop_max=stop_max)  # type: ignore[arg-type]
