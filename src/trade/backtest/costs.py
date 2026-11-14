"""成交成本模型与压力情景（06 §4）。

规格：docs/06-回测引擎.md §3.2、§4
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# 纯函数移入 trade.common.costs（避免 trade.common → trade.backtest 循环导入），此处再导出
from trade.common.costs import (
    breakeven_slip,
    effective_fee,
    exit_fee_rate_on_entry,
    half_spread,
    net_return_after_fees,
    slip,
)

__all__ = [
    "FeeSchedule",
    "Scenario",
    "breakeven_slip",
    "effective_fee",
    "exit_fee_rate_on_entry",
    "half_spread",
    "net_return_after_fees",
    "scenario",
    "slip",
]


@dataclass(frozen=True)
class FeeSchedule:
    """挂单/吃单费率（base.yaml，06 §4.1）。

    `rebate_rate` 为上级代理返佣比例：返佣在成交之后另行到账，不计入交易所费率，
    因此只用于报告中的“含返佣”口径（`taker_eff`/`maker_eff`）。
    """

    maker: float
    taker: float
    rebate_rate: float = 0.0

    @property
    def taker_eff(self) -> float:
        """返佣后的吃单费率（06 §4.1）。"""
        return effective_fee(self.taker, self.rebate_rate)

    @property
    def maker_eff(self) -> float:
        """返佣后的挂单费率（06 §4.1）。"""
        return effective_fee(self.maker, self.rebate_rate)


@dataclass(frozen=True)
class Scenario:
    """成本情景参数（06 §4.3）。"""

    name: str
    latency_ms: int  # 执行延迟：1000 → p_1s；3000 → p_3s
    slip_mult: float  # 滑点放大倍数
    half_spread_mult: float  # 最小点差放大倍数
    fixed_slip: float | None  # 单边固定滑点（hell/breakeven 用），None 表示用动态滑点
    tp_taker: bool  # 止盈是否按吃单费率
    all_taker: bool  # 所有成交是否按吃单费率（hell）
    # 每币实测半价差下限（alias, 比例）；非空时覆盖 `half_spread_mult × half_spread_min`
    # 的全局口径，用于 `spread_measured` 情景（06 §4.3）
    half_spread_by_symbol: tuple[tuple[str, float], ...] = ()

    def half_spread_min_for(self, symbol: str, half_spread_min: float) -> float:
        """该币的最小半价差（06 §4.3）：有实测覆盖时用实测值，否则用全局值。"""
        if not self.half_spread_by_symbol:
            return half_spread_min
        return dict(self.half_spread_by_symbol).get(symbol, half_spread_min)


_SCENARIOS: dict[str, Scenario] = {
    "base": Scenario(
        name="base",
        latency_ms=1000,
        slip_mult=1.0,
        half_spread_mult=1.0,
        fixed_slip=None,
        tp_taker=False,
        all_taker=False,
    ),
    "stress": Scenario(
        name="stress",
        latency_ms=3000,
        slip_mult=2.0,
        half_spread_mult=2.0,
        fixed_slip=None,
        tp_taker=True,
        all_taker=False,
    ),
    "hell": Scenario(
        name="hell",
        latency_ms=3000,
        slip_mult=1.0,
        half_spread_mult=1.0,
        fixed_slip=0.0015,
        tp_taker=True,
        all_taker=True,
    ),
    # 实测点差压力情景（06 §4.3）：每币 half_spread_min = 该币相对点差 P75 / 2，
    # 由 `scripts/spread_profile.py` 产出、`trade.backtest.costs.spread_measured_scenario`
    # 注入；未提供覆盖时用全局 P75 / 2。只报告，不改变 `base`。
    "spread_measured": Scenario(
        name="spread_measured",
        latency_ms=1000,
        slip_mult=1.0,
        half_spread_mult=1.0,
        fixed_slip=None,
        tp_taker=False,
        all_taker=False,
    ),
}


def spread_measured_scenario(
    half_spread_by_symbol: dict[str, float] | None = None,
) -> Scenario:
    """构造实测点差情景（06 §4.3）。

    Args:
        half_spread_by_symbol: 币 → 该币最小半价差（相对点差 P75 / 2）；None 表示
            没有实测覆盖、沿用 `base` 的全局半价差下限（缺少录制数据时的降级路径由
            调用方决定并在报告中写明）。

    Returns:
        `spread_measured` 情景对象。
    """
    base = _SCENARIOS["spread_measured"]
    if not half_spread_by_symbol:
        return base
    return replace(
        base,
        half_spread_by_symbol=tuple(
            sorted((k, float(v)) for k, v in half_spread_by_symbol.items())
        ),
    )


def scenario(name: str) -> Scenario:
    """返回成本情景（06 §4.3）。breakeven 由二分搜索单独求。"""
    if name not in _SCENARIOS:
        raise ValueError(f"未知成本情景：{name}（仅 {sorted(_SCENARIOS)}）")
    return _SCENARIOS[name]
