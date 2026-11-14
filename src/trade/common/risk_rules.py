"""回测与实盘共用的风控规则 R5~R8（纯函数 + 逐分钟状态）。

规格：docs/06-回测引擎.md §3.1 条件 4、§3.4；docs/07-实盘与风控.md §7.1

回测的组合模拟（`trade.backtest.engine.simulate_portfolio`）与实盘
（`trade.live.risk.RiskEngine`）调用同一组纯函数，禁止各写一份判定（AGENTS.md §6 红线 4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

_MS_PER_DAY = 86_400_000

# 停机模式（06 §2 risk.halt_mode）：strict = 触发后停止交易至运行结束；
# reset = 停 N 天后按当时权益继续
HALT_STRICT = "strict"
HALT_RESET = "reset"

# strict 模式的恢复时刻用一个不会到达的时间戳表示（不再恢复交易）
_NEVER_MS = 1 << 62


def utc_day(ts_ms: int) -> str:
    """返回 UTC 日 `YYYY-MM-DD`（06 §3.1 条件 4）。"""
    day = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}-{day.day:02d}"


@dataclass(frozen=True)
class RiskLimits:
    """回测风控参数（来自 `sizing.yaml` 与 `backtest.yaml: risk`，06 §2、§5.4）。"""

    daily_loss_stop: float
    daily_loss_halt: float
    max_drawdown_halt: float
    max_consecutive_losses: int
    consecutive_loss_pause_min: int
    halt_mode: str
    halt_reset_days: int


@dataclass
class RiskEvent:
    """一次风控触发记录（06 §6、§8 报告用）。"""

    ts_ms: int
    day: str
    rule: str  # R5 / R6 / R7 / R8
    action: str  # stop_open / flatten_all / pause


@dataclass
class RiskState:
    """逐分钟风控状态（回测内使用；实盘用 `trade.live.risk.RiskEngine` 的上下文）。"""

    peak_equity: float
    day: str = ""
    day_start_equity: float = 0.0
    daily_stop_day: str | None = None
    halted_until_ms: int = 0
    halt_reason: str | None = None
    consecutive_losses: int = 0
    pause_until_ms: int = 0
    events: list[RiskEvent] = field(default_factory=list)

    def halted(self, ts_ms: int) -> bool:
        """当前是否处于 R6/R8 停机状态（07 §7.1）。"""
        return ts_ms < self.halted_until_ms

    def paused(self, ts_ms: int) -> bool:
        """当前是否处于 R7 连续止损暂停（07 §7.1）。"""
        return ts_ms < self.pause_until_ms

    def daily_stop(self, day: str) -> bool:
        """当日是否已触发 R5 停止开仓（06 §3.1 条件 4）。"""
        return self.daily_stop_day == day


def daily_loss_pnl(daily_pnl: float, day_start_equity: float, ratio: float) -> bool:
    """R5/R6 判定：当日亏损（已实现 + 未实现）达到 `day_start_equity × ratio`（07 §7.1）。

    Args:
        daily_pnl: 当日盯市盈亏 = 当前盯市权益 − 当日 00:00 盯市权益，USDT。
        day_start_equity: 当日 00:00 盯市权益，USDT。
        ratio: 阈值比例（R5 用 `daily_loss_stop`，R6 用 `daily_loss_halt`）。

    Returns:
        True 表示已达到或超过阈值。
    """
    return daily_pnl <= -day_start_equity * ratio


def drawdown_hit(equity: float, peak_equity: float, ratio: float) -> bool:
    """R8 判定：盯市权益从峰值回撤达到 `ratio`（07 §7.1）。"""
    if peak_equity <= 0:
        return False
    return (peak_equity - equity) / peak_equity >= ratio


def consecutive_losses_hit(n_losses: int, limit: int) -> bool:
    """R7 判定：连续止损出场次数达到上限（07 §7.1）。"""
    return n_losses >= limit


def halt_until_ms(ts_ms: int, limits: RiskLimits) -> int:
    """R6/R8 触发后的恢复时刻（06 §2 `risk.halt_mode`）。"""
    if limits.halt_mode == HALT_STRICT:
        return _NEVER_MS
    return ts_ms + limits.halt_reset_days * _MS_PER_DAY


def risk_step(state: RiskState, *, ts_ms: int, equity: float, limits: RiskLimits) -> str | None:
    """在一个分钟收盘点上推进风控状态并返回应执行的动作（06 §3.4、07 §7.1）。

    动作：`"flatten"` 表示 R6/R8 触发、需在下一分钟平掉全部持仓；`None` 表示继续。

    Args:
        state: 逐分钟风控状态（原地更新）。
        ts_ms: 当前分钟的开盘时间（毫秒，UTC）。
        equity: 当前盯市权益（钱包余额 + 未实现盈亏），USDT。
        limits: 风控参数。

    Returns:
        `"flatten"` 或 `None`。
    """
    day = utc_day(ts_ms)
    if state.halted_until_ms:
        if ts_ms < state.halted_until_ms:
            return None  # 停机中：不交易、不平仓
        # reset 模式到期恢复：峰值按恢复时权益重设，避免立刻再次触发 R8
        state.peak_equity = max(state.peak_equity, equity)
        state.halted_until_ms = 0
        state.halt_reason = None
    if state.day != day:
        state.day = day
        state.day_start_equity = equity

    state.peak_equity = max(state.peak_equity, equity)
    daily_pnl = equity - state.day_start_equity

    if daily_loss_pnl(daily_pnl, state.day_start_equity, limits.daily_loss_halt):
        state.events.append(RiskEvent(ts_ms, day, "R6", "flatten_all"))
        state.daily_stop_day = day
        state.halted_until_ms = halt_until_ms(ts_ms, limits)
        state.halt_reason = "R6"
        return "flatten"

    if drawdown_hit(equity, state.peak_equity, limits.max_drawdown_halt):
        state.events.append(RiskEvent(ts_ms, day, "R8", "flatten_all"))
        state.daily_stop_day = day
        state.halted_until_ms = halt_until_ms(ts_ms, limits)
        state.halt_reason = "R8"
        return "flatten"

    if not state.daily_stop(day) and daily_loss_pnl(
        daily_pnl, state.day_start_equity, limits.daily_loss_stop
    ):
        state.events.append(RiskEvent(ts_ms, day, "R5", "stop_open"))
        state.daily_stop_day = day
    return None


def register_exit(state: RiskState, *, ts_ms: int, net_pnl: float, hit_stop_type: bool) -> None:
    """登记一次离场，更新 R7 连续止损计数（07 §7.1）。

    Args:
        state: 风控状态（原地更新）。
        ts_ms: 离场 K 线开盘时间（毫秒）。
        net_pnl: 该笔净盈亏，USDT。
        hit_stop_type: 是否为初始止损/保本/追踪出场（07 §7.1 R7 口径）。
    """
    if hit_stop_type and net_pnl < 0.0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0


def register_pause_if_needed(state: RiskState, *, ts_ms: int, limits: RiskLimits) -> bool:
    """R7：连续止损达到上限时开始暂停开仓（07 §7.1）。

    Args:
        state: 风控状态（原地更新）。
        ts_ms: 触发该次判定的离场时间（毫秒）。
        limits: 风控参数。

    Returns:
        True 表示本次触发了新的暂停窗口。
    """
    if not consecutive_losses_hit(state.consecutive_losses, limits.max_consecutive_losses):
        return False
    state.events.append(RiskEvent(ts_ms, utc_day(ts_ms), "R7", "pause"))
    state.pause_until_ms = ts_ms + limits.consecutive_loss_pause_min * 60_000
    state.consecutive_losses = 0
    return True
