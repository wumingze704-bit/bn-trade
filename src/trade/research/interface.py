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
    """调仓时刻判断：8h / 24h 锚定 UTC 00 点，168h 锚定 UTC 周一 00 点（docs/13 §5）。

    168h 不能用 Unix 纪元取模：纪元从周四 00:00 UTC 起算，取模会落在周四。

    Args:
        ts_ms: 当前 K 线 `open_time`（UTC 毫秒）。
        rebalance_hours: 调仓周期（小时）。

    Returns:
        是否为调仓时刻。

    Raises:
        ValueError: 周期不能整除 24 小时（无法锚定 UTC 整点）。
    """
    if rebalance_hours <= 0 or (rebalance_hours != 168 and 24 % rebalance_hours != 0):
        raise ValueError(f"调仓周期必须整除 24 小时：{rebalance_hours}（docs/13 §5）")
    hour_ms = HOUR_MS
    day_ms = 24 * hour_ms
    if ts_ms % hour_ms != 0:
        return False
    if rebalance_hours == 168:
        # 1970-01-01 是周四：加 4 天偏移使取模对齐到周一 00:00 UTC
        return ts_ms % day_ms == 0 and (ts_ms // day_ms + 4) % 7 == 0
    return ts_ms % (rebalance_hours * hour_ms) == 0
