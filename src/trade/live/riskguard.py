"""低频引擎风控：R5~R8 与行情 / 下单 / 时钟 / 敞口 / 价差规则（docs/07 §6.6）。

保留对低频策略有意义的部分，复用 `trade.common.risk_rules`（红线 4，不另写判定）。

规格：docs/07-实盘与风控.md §6.6、§7
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from trade.common.risk_rules import (
    RiskEvent,
    RiskLimits,
    RiskState,
    daily_loss_pnl,
    drawdown_hit,
    halt_until_ms,
    register_exit,
    register_pause_if_needed,
    utc_day,
)

_MS_PER_HOUR = 3_600_000


@dataclass
class RiskGuard:
    """把 R5~R8 与低频健康规则合并为一个状态机。

    Args:
        limits: 来自 `configs/sizing_research.yaml` 与 `configs/backtest.yaml: risk`。
        net_exposure_cap: 净敞口上限（权益比例）。
        net_exposure_grace_min: 净敞口超限持续多久进入安全模式（分钟）。
        clock_drift_limit_ms: 时钟漂移上限（毫秒）。
        order_error_limit: 1 小时内下单错误上限。
        stale_cycles: 行情过期超过几个调仓周期算不健康（1 表示超过 1 个周期）。
    """

    limits: RiskLimits
    net_exposure_cap: float
    net_exposure_grace_min: int
    clock_drift_limit_ms: int
    order_error_limit: int
    stale_cycles: int = 1
    state: RiskState = field(default_factory=lambda: RiskState(peak_equity=0.0))
    safe_reason: str | None = None
    data_stale: bool = False
    clock_drift_ms: int = 0
    exposure_exceeded_since_ms: int | None = None
    _order_errors: deque[int] = field(default_factory=deque)

    def restore(
        self,
        *,
        peak_equity: float,
        day: str,
        day_start_equity: float,
        consecutive_losses: int,
        halted: bool,
        halt_reason: str | None,
    ) -> None:
        """从状态库恢复上次运行的风控状态（重启后不丢失峰值与连续止损计数）。"""
        self.state.peak_equity = peak_equity
        self.state.day = day
        self.state.day_start_equity = day_start_equity
        self.state.consecutive_losses = consecutive_losses
        if halted:
            self.state.halted_until_ms = halt_until_ms(0, self.limits)
            self.state.halt_reason = halt_reason

    def on_equity(self, *, ts_ms: int, equity: float) -> bool:
        """推进日切 / 峰值 / R6 / R8；返回是否需要全部平仓。"""
        day = utc_day(ts_ms)
        if self.state.halted_until_ms:
            if ts_ms < self.state.halted_until_ms:
                return False
            self.state.peak_equity = max(self.state.peak_equity, equity)
            self.state.halted_until_ms = 0
            self.state.halt_reason = None
        if self.state.day != day or self.state.day_start_equity <= 0.0:
            self.state.day = day
            self.state.day_start_equity = equity
            self.state.daily_stop_day = None
        self.state.peak_equity = max(self.state.peak_equity, equity)
        daily_pnl = equity - self.state.day_start_equity
        if daily_loss_pnl(daily_pnl, self.state.day_start_equity, self.limits.daily_loss_halt):
            self._append_event(ts_ms, "R6", "flatten_all")
            self.state.daily_stop_day = day
            self.state.halted_until_ms = halt_until_ms(ts_ms, self.limits)
            self.state.halt_reason = "R6"
            return True
        if drawdown_hit(equity, self.state.peak_equity, self.limits.max_drawdown_halt):
            self._append_event(ts_ms, "R8", "flatten_all")
            self.state.daily_stop_day = day
            self.state.halted_until_ms = halt_until_ms(ts_ms, self.limits)
            self.state.halt_reason = "R8"
            return True
        if not self.state.daily_stop(day) and daily_loss_pnl(
            daily_pnl, self.state.day_start_equity, self.limits.daily_loss_stop
        ):
            self._append_event(ts_ms, "R5", "stop_open")
            self.state.daily_stop_day = day
        return False

    def record_exit(self, *, ts_ms: int, net_pnl: float, hit_stop_type: bool) -> bool:
        """登记离场并判断 R7 暂停开仓；返回是否新触发暂停。"""
        register_exit(self.state, ts_ms=ts_ms, net_pnl=net_pnl, hit_stop_type=hit_stop_type)
        return register_pause_if_needed(self.state, ts_ms=ts_ms, limits=self.limits)

    def record_order_error(self, ts_ms: int) -> bool:
        """记录一次下单错误；返回是否达到 1 小时阈值。"""
        self._order_errors.append(ts_ms)
        self._purge_errors(ts_ms)
        return len(self._order_errors) >= self.order_error_limit

    def set_data_stale(self, stale: bool) -> None:
        """设置行情是否过期（过期时只平不开）。"""
        self.data_stale = stale

    def set_clock_drift(self, drift_ms: int) -> None:
        """设置当前时钟漂移（毫秒）。"""
        self.clock_drift_ms = abs(int(drift_ms))

    def update_net_exposure(self, *, net: float, equity: float, ts_ms: int) -> bool:
        """更新净敞口状态；返回是否已持续超限超过宽限期。"""
        limit = self.net_exposure_cap * equity
        if limit <= 0.0:
            return False
        if abs(net) <= limit:
            self.exposure_exceeded_since_ms = None
            return False
        if self.exposure_exceeded_since_ms is None:
            self.exposure_exceeded_since_ms = ts_ms
            return False
        return ts_ms - self.exposure_exceeded_since_ms >= self.net_exposure_grace_min * 60_000

    def enter_safe_mode(self, reason: str) -> None:
        """进入安全模式（只平不开；原因取第一个，避免被后续告警覆盖）。"""
        if self.safe_reason is None:
            self.safe_reason = reason

    def open_blocked_reason(self, ts_ms: int) -> str | None:
        """返回禁止开新仓的原因；None 表示可以开仓（docs/07 §6.6）。"""
        if self.safe_reason is not None:
            return f"安全模式：{self.safe_reason}"
        if self.state.halted(ts_ms):
            return f"停机中（{self.state.halt_reason or 'R6/R8'}）"
        if self.state.paused(ts_ms):
            return "R7 连续止损暂停"
        if self.state.daily_stop(utc_day(ts_ms)):
            return "R5 当日停止开仓"
        if self.data_stale:
            return "行情过期"
        if self.clock_drift_ms > self.clock_drift_limit_ms:
            return f"时钟漂移 {self.clock_drift_ms}ms"
        self._purge_errors(ts_ms)
        if len(self._order_errors) >= self.order_error_limit:
            return "下单错误过多"
        return None

    def halt_flag(self, ts_ms: int) -> bool:
        """当前是否处于停机（R6/R8，看板与状态库用）。"""
        return self.state.halted(ts_ms) or bool(self.state.halt_reason)

    def events(self) -> list[RiskEvent]:
        """返回本次运行累计的风控事件。"""
        return list(self.state.events)

    def _purge_errors(self, ts_ms: int) -> None:
        cutoff = ts_ms - _MS_PER_HOUR
        while self._order_errors and self._order_errors[0] < cutoff:
            self._order_errors.popleft()

    def _append_event(self, ts_ms: int, rule: str, action: str) -> None:
        self.state.events.append(RiskEvent(ts_ms, utc_day(ts_ms), rule, action))
