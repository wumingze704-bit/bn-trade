"""实盘风控规则 R1~R14、安全模式判定。

规格：docs/07-实盘与风控.md §7.1
"""

from __future__ import annotations

from dataclasses import dataclass

from trade.common import risk_rules
from trade.common.sizing import SizingConfig, equity_floor_hit
from trade.live.config import HealthConfig, RiskConfig


@dataclass(frozen=True)
class RiskContext:
    """单次开仓决策所需的风控上下文（07 §7.1）。"""

    n_open_positions: int
    n_positions_for_symbol: int
    bars_since_stop: int
    chase_ratio: float  # (limit_price - best_ask) / best_ask
    daily_pnl: float  # 当日已实现 + 未实现（相对当日初权益）
    day_start_equity: float
    consecutive_losses: int
    current_equity: float
    peak_equity: float
    spread_bp: float  # 当前价差（基点）
    median_spread_bp: float  # 最近 60 分钟价差中位数（基点）
    clock_drift_ms: int
    order_errors_5m: int
    equity_floor: bool  # R14 预判（06 §5.3）
    notional_capped: bool  # R13 预判（06 §5.3）


@dataclass(frozen=True)
class OpenDecision:
    """开仓决策结果（07 §7.1）。"""

    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class HaltDecision:
    """停机/安全模式判定结果（07 §7.1、§7.2、§7.3）。"""

    halt: bool
    safe_mode: bool
    reason: str | None = None


def check_max_positions(n_open: int, max_positions: int) -> bool:
    """R2 最大持仓数（07 §7.1）。"""
    return n_open < max_positions


def check_per_symbol(n_positions_for_symbol: int) -> bool:
    """R2 每币最多 1 个持仓（07 §7.1）。"""
    return n_positions_for_symbol == 0


def check_cooldown(bars_since_stop: int, cooldown_bars: int) -> bool:
    """R3 止损冷却（07 §7.1）。"""
    return bars_since_stop >= cooldown_bars


def check_chase(chase_ratio: float, max_chase: float) -> bool:
    """R4 追价保护（07 §7.1）。"""
    return chase_ratio <= max_chase


def check_daily_loss(daily_pnl: float, day_start_equity: float, ratio: float) -> bool:
    """R5 日亏损停止（07 §7.1）：未达阈值返回 True。

    判定与回测共用 `trade.common.risk_rules.daily_loss_pnl`（06 §3.1 条件 4）。
    """
    return not risk_rules.daily_loss_pnl(daily_pnl, day_start_equity, ratio)


def check_daily_halt(daily_pnl: float, day_start_equity: float, ratio: float) -> bool:
    """R6 日亏损熔断（07 §7.1）：达到阈值返回 True。

    判定与回测共用 `trade.common.risk_rules.daily_loss_pnl`（06 §3.1 条件 4）。
    """
    return risk_rules.daily_loss_pnl(daily_pnl, day_start_equity, ratio)


def check_consecutive_losses(n: int, limit: int) -> bool:
    """R7 连续止损（07 §7.1）：未达上限返回 True。

    判定与回测共用 `trade.common.risk_rules.consecutive_losses_hit`。
    """
    return not risk_rules.consecutive_losses_hit(n, limit)


def check_drawdown(equity: float, peak_equity: float, ratio: float) -> bool:
    """R8 权益从峰值回撤（07 §7.1）：达到阈值返回 True。

    判定与回测共用 `trade.common.risk_rules.drawdown_hit`。
    """
    return risk_rules.drawdown_hit(equity, peak_equity, ratio)


def check_spread(spread_bp: float, median_spread_bp: float, mult: float) -> bool:
    """R9 价差异常（07 §7.1）。"""
    if median_spread_bp <= 0:
        return spread_bp <= 0
    return spread_bp <= median_spread_bp * mult


def check_clock_drift(drift_ms: int, limit_ms: int) -> bool:
    """R12 时钟漂移（07 §7.1）。"""
    return abs(drift_ms) <= limit_ms


def check_order_errors(n_errors: int, limit: int) -> bool:
    """R11 下单错误（07 §7.1）。"""
    return n_errors < limit


class RiskEngine:
    """风控规则引擎（07 §7.1）。

    Args:
        sizing: 生效仓位配置（含白名单覆盖，07 §7.1.1）。
        risk: 风险/追价参数（configs/live.yaml risk 节）。
        health: 健康阈值（configs/live.yaml health 节）。
    """

    def __init__(self, sizing: SizingConfig, risk: RiskConfig, health: HealthConfig) -> None:
        self._sizing = sizing
        self._risk = risk
        self._health = health

    def check_open(self, ctx: RiskContext) -> OpenDecision:
        """逐条评估开仓前风控，返回是否允许及拒绝原因（07 §7.1）。"""
        if not check_max_positions(ctx.n_open_positions, self._sizing.max_positions):
            return OpenDecision(False, "R2_max_positions")
        if not check_per_symbol(ctx.n_positions_for_symbol):
            return OpenDecision(False, "R2_per_symbol")
        if not check_cooldown(ctx.bars_since_stop, self._risk.cooldown_bars):
            return OpenDecision(False, "R3_cooldown")
        if not check_chase(ctx.chase_ratio, self._risk.max_chase):
            return OpenDecision(False, "R4_chase")
        if not check_daily_loss(ctx.daily_pnl, ctx.day_start_equity, self._sizing.daily_loss_stop):
            return OpenDecision(False, "R5_daily_loss_stop")
        if not check_consecutive_losses(
            ctx.consecutive_losses, self._sizing.max_consecutive_losses
        ):
            return OpenDecision(False, "R7_consecutive_losses")
        if not check_spread(ctx.spread_bp, ctx.median_spread_bp, self._risk.spread_mult_limit):
            return OpenDecision(False, "R9_spread")
        if not check_order_errors(ctx.order_errors_5m, self._health.order_error_limit_5m):
            return OpenDecision(False, "R11_order_errors")
        if not check_clock_drift(ctx.clock_drift_ms, self._health.clock_drift_ms):
            return OpenDecision(False, "R12_clock_drift")
        if ctx.notional_capped:
            return OpenDecision(False, "R13_notional_capped")
        if ctx.equity_floor:
            return OpenDecision(False, "R14_equity_floor")
        return OpenDecision(True)

    def check_halt(self, ctx: RiskContext) -> HaltDecision:
        """评估熔断与安全模式（07 §7.1、§7.2、§7.3）。"""
        if check_daily_halt(ctx.daily_pnl, ctx.day_start_equity, self._sizing.daily_loss_halt):
            return HaltDecision(True, True, "R6_daily_loss_halt")
        if check_drawdown(ctx.current_equity, ctx.peak_equity, self._sizing.max_drawdown_halt):
            return HaltDecision(True, True, "R8_max_drawdown")
        return HaltDecision(False, False)

    def equity_floor_hit(self, equity: float, min_notional: float) -> bool:
        """R14 权益下限（06 §5.3）。"""
        return equity_floor_hit(equity, self._sizing, min_notional)
