"""策略接口：声明暴露方向、调仓时刻与目标仓位（docs/13 §5）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from trade.research.data import HOUR_MS
from trade.research.view import View

EXPOSURE_NEUTRAL = "neutral"
EXPOSURE_DIRECTIONAL = "directional"


@dataclass(frozen=True)
class Target:
    """单个目标仓位：方向、止损距离与相对权重。"""

    symbol: str
    side: int  # +1 多，-1 空
    sl: float  # 止损距离，比例（0.03 = 3%）
    weight: float = 1.0  # 同侧内相对权重（组合层按权重缩放风险预算）


@dataclass(frozen=True)
class Decision:
    """一次调仓决策。"""

    targets: tuple[Target, ...]
    exposure: str


class Strategy(Protocol):
    """策略协议：`decide` 只能通过 View 读取已收盘数据。"""

    name: str
    rebalance_hours: int
    exposure: str

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """返回本次调仓的目标仓位；`held` 为当前持仓方向（symbol → ±1）。"""
        ...


def rebalance_due(ts_ms: int, rebalance_hours: int) -> bool:
    """UTC 整点对齐的调仓时刻判断（8h → 00/08/16）。"""
    return ts_ms % (rebalance_hours * HOUR_MS) == 0
