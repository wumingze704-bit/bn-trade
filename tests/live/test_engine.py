"""启动流程纯函数单测。

规格：docs/07-实盘与风控.md §2
"""

from __future__ import annotations

from trade.live.engine import fee_reject_live, startup_clock_ok


def test_startup_clock_ok_within_limit() -> None:
    assert startup_clock_ok(400) is True
    assert startup_clock_ok(600) is False


def test_fee_reject_live_above_ratio() -> None:
    assert fee_reject_live(0.0007, 0.0005, 1.2) is True
    assert fee_reject_live(0.00055, 0.0005, 1.2) is False
