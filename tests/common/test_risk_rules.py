"""风控 R5~R8 状态机（`trade.common.risk_rules`）的停机与恢复。

规格：docs/13-研究框架.md §4（`reset` 模式停机 `halt_reset_days` 天后以当时权益继续）。
"""

from __future__ import annotations

import pytest

from trade.common.risk_rules import RiskLimits, RiskState, risk_step

_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_T0 = 1_646_582_400_000  # 2022-03-01 00:00 UTC


def _limits(halt_mode: str) -> RiskLimits:
    return RiskLimits(
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        halt_mode=halt_mode,
        halt_reset_days=7,
    )


def test_reset模式停机到期后按恢复时权益继续() -> None:
    """复位模式：到期恢复时峰值重设为恢复时权益，不得按停机前的峰值立即再次触发 R8。

    修复前 `risk_step` 用 `max(旧峰值, 恢复时权益)`，回撤始终按停机前的高点计算，
    于是每次到期后立刻再触发 R8、再停 7 天，整条拼接路径永久不再交易。
    """
    limits = _limits("reset")
    state = RiskState(peak_equity=10_000.0)
    assert risk_step(state, ts_ms=_T0, equity=10_000.0, limits=limits) is None
    action = risk_step(state, ts_ms=_T0 + _HOUR_MS, equity=7_900.0, limits=limits)
    assert action == "flatten"  # 单日亏损 21% ≥ 8% → R6 停机
    assert state.halted_until_ms == _T0 + _HOUR_MS + 7 * _DAY_MS
    resume_ts = state.halted_until_ms
    assert risk_step(state, ts_ms=resume_ts, equity=7_900.0, limits=limits) is None
    assert state.halted_until_ms == 0
    assert state.peak_equity == pytest.approx(7_900.0)
    # 恢复后回撤按新峰值计算：再跌 4.4% 既不到 R5 也不到 R8
    assert risk_step(state, ts_ms=resume_ts + _HOUR_MS, equity=7_550.0, limits=limits) is None


def test_strict模式停机后不再恢复() -> None:
    """严格模式：触发后一直停机（影子盘、测试网与实盘使用该模式）。"""
    limits = _limits("strict")
    state = RiskState(peak_equity=10_000.0)
    assert risk_step(state, ts_ms=_T0, equity=10_000.0, limits=limits) is None
    assert risk_step(state, ts_ms=_T0 + _HOUR_MS, equity=7_900.0, limits=limits) == "flatten"
    far = _T0 + 365 * _DAY_MS
    assert state.halted(far)
    assert risk_step(state, ts_ms=far, equity=7_900.0, limits=limits) is None
    assert state.halted(far + _HOUR_MS)
