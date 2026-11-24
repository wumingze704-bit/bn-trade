"""实盘 / 测试网 / 影子盘的停机方式固定为 strict。

规格：docs/07-实盘与风控.md §6、docs/13-研究框架.md §2（reset 只用于研究与最终检验）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade.common.risk_rules import halt_until_ms
from trade.live.runner import RunnerError, load_risk_limits
from trade.research.config import load_research_sizing

ROOT = Path(__file__).resolve().parents[2]
_MS_PER_DAY = 86_400_000


def _sizing():  # type: ignore[no-untyped-def]
    return load_research_sizing(ROOT / "configs" / "sizing_research.yaml").with_tier()


def test_三种运行模式不会恢复停机() -> None:
    """shadow / paper / live 的 halt_mode 恒为 strict，停机后不再按 reset 恢复。"""
    ts = 1_700_000_000_000
    for mode in ("shadow", "paper", "live"):
        limits = load_risk_limits(_sizing(), mode)
        assert limits.halt_mode == "strict"
        # strict 的恢复时刻是一个不会到达的时间戳
        assert halt_until_ms(ts, limits) > ts + 100 * 365 * _MS_PER_DAY


def test_未知运行模式直接报错() -> None:
    """模式不是 shadow / paper / live 时抛错，不静默使用默认停机方式。"""
    with pytest.raises(RunnerError):
        load_risk_limits(_sizing(), "research")
