"""成交成本模型与压力情景（06 §4）。

规格：docs/06-回测引擎.md §3.2、§4
"""

from __future__ import annotations

from dataclasses import dataclass

# 纯函数移入 trade.common.costs（避免 trade.common → trade.backtest 循环导入），此处再导出
from trade.common.costs import breakeven_slip, half_spread, slip

__all__ = [
    "FeeSchedule",
    "Scenario",
    "breakeven_slip",
    "half_spread",
    "scenario",
    "slip",
]


@dataclass(frozen=True)
class FeeSchedule:
    """挂单/吃单费率（base.yaml，06 §4.1）。"""

    maker: float
    taker: float


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


_SCENARIOS: dict[str, Scenario] = {
    "base": Scenario("base", 1000, 1.0, 1.0, None, False, False),
    "stress": Scenario("stress", 3000, 2.0, 2.0, None, True, False),
    "hell": Scenario("hell", 3000, 1.0, 1.0, 0.0015, True, True),
}


def scenario(name: str) -> Scenario:
    """返回成本情景（06 §4.3）。breakeven 由二分搜索单独求。"""
    if name not in _SCENARIOS:
        raise ValueError(f"未知成本情景：{name}（仅 {sorted(_SCENARIOS)}）")
    return _SCENARIOS[name]
