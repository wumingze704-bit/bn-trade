"""双端共用基础能力。

规格：docs/02-系统架构与部署.md §2
"""

from trade.common.exits import (
    ExitConfig,
    ExitResult,
    ExitType,
    exit_policy_hash,
    simulate_exit,
)
from trade.common.sizing import (
    SizingConfig,
    SizingResult,
    calc_notional,
    equity_floor_hit,
    loss_rate,
    validate_sizing,
)

__all__ = [
    "ExitConfig",
    "ExitResult",
    "ExitType",
    "SizingConfig",
    "SizingResult",
    "calc_notional",
    "equity_floor_hit",
    "exit_policy_hash",
    "loss_rate",
    "simulate_exit",
    "validate_sizing",
]
