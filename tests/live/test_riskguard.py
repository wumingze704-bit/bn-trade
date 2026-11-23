"""低频引擎风控测试（docs/07 §6.6、§7）。"""

from __future__ import annotations

from trade.common.risk_rules import RiskLimits
from trade.live.riskguard import RiskGuard


def _limits() -> RiskLimits:
    return RiskLimits(
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=3,
        consecutive_loss_pause_min=120,
        halt_mode="reset",
        halt_reset_days=1,
    )


def _guard() -> RiskGuard:
    return RiskGuard(
        limits=_limits(),
        net_exposure_cap=0.5,
        net_exposure_grace_min=10,
        clock_drift_limit_ms=1000,
        order_error_limit=5,
    )


def test_R6日亏损熔断触发平仓并停机() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    assert guard.on_equity(ts_ms=ts, equity=100.0) is False
    assert guard.on_equity(ts_ms=ts + 60_000, equity=91.5) is True
    assert guard.state.halt_reason == "R6"
    assert guard.open_blocked_reason(ts + 120_000) is not None


def test_R8峰值回撤触发平仓() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    guard.on_equity(ts_ms=ts, equity=100.0)
    # 次日权益 79：当日亏损为 0（不触发 R6），但相对峰值回撤 21% 触发 R8
    assert guard.on_equity(ts_ms=ts + 86_400_000, equity=79.0) is True
    assert guard.state.halt_reason == "R8"


def test_R5当日停止开仓但仍可交易已有仓位() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    guard.on_equity(ts_ms=ts, equity=100.0)
    assert guard.on_equity(ts_ms=ts + 60_000, equity=94.9) is False
    assert "R5" in (guard.open_blocked_reason(ts + 120_000) or "")


def test_R7连续止损暂停开仓() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    guard.on_equity(ts_ms=ts, equity=100.0)
    paused = False
    for index in range(3):
        paused = guard.record_exit(ts_ms=ts + index * 1000, net_pnl=-1.0, hit_stop_type=True)
    assert paused is True
    assert "R7" in (guard.open_blocked_reason(ts + 5000) or "")


def test_行情过期与时钟漂移阻止开仓() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    guard.on_equity(ts_ms=ts, equity=100.0)
    guard.set_data_stale(True)
    assert "行情过期" in (guard.open_blocked_reason(ts) or "")
    guard.set_data_stale(False)
    guard.set_clock_drift(5000)
    assert "时钟漂移" in (guard.open_blocked_reason(ts) or "")


def test_净敞口超限需持续宽限期() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    assert guard.update_net_exposure(net=80.0, equity=100.0, ts_ms=ts) is False
    assert guard.update_net_exposure(net=80.0, equity=100.0, ts_ms=ts + 9 * 60_000) is False
    assert guard.update_net_exposure(net=80.0, equity=100.0, ts_ms=ts + 10 * 60_000) is True
    guard.enter_safe_mode("净敞口超限")
    assert "安全模式" in (guard.open_blocked_reason(ts + 11 * 60_000) or "")


def test_下单错误计数达到阈值阻止开仓() -> None:
    guard = _guard()
    ts = 1_700_000_000_000
    guard.on_equity(ts_ms=ts, equity=100.0)
    for index in range(5):
        guard.record_order_error(ts + index * 1000)
    assert "下单错误过多" in (guard.open_blocked_reason(ts + 5000) or "")
    assert guard.open_blocked_reason(ts + 2 * 3_600_000) is None or "R5" in (
        guard.open_blocked_reason(ts + 2 * 3_600_000) or ""
    )
