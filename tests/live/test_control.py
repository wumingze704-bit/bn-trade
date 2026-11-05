"""白名单覆盖单测。

规格：docs/07-实盘与风控.md §7.1.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade.common.sizing import SizingConfig
from trade.live.control import ControlError, apply_override, effective_sizing
from trade.live.store import Store


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=1000,
        mode="fixed_fraction",
        position_frac=0.2,
        leverage=5,
        max_positions=3,
        base="equity",
        risk_per_trade=0.005,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "state.db")
    yield s
    s.close()


def test_effective_sizing_applies_override() -> None:
    merged = effective_sizing(_sizing(), {"position_frac": "0.3"})
    assert merged.position_frac == 0.3
    assert merged.max_positions == 3


def test_apply_override_rejects_non_whitelist_key(store: Store) -> None:
    with pytest.raises(ControlError):
        apply_override(
            store,
            key="leverage_limit",
            value="10",
            source="cli",
            sizing=_sizing(),
            equity=1000.0,
            min_notional_median=5.0,
            sl_max=0.02,
            max_leverage=20,
        )


def test_apply_override_rejects_invalid_sizing(store: Store) -> None:
    with pytest.raises(ControlError):
        apply_override(
            store,
            key="position_frac",
            value="0.99",  # position_frac × max_positions > 0.95
            source="cli",
            sizing=_sizing(),
            equity=1000.0,
            min_notional_median=5.0,
            sl_max=0.02,
            max_leverage=20,
        )


def test_apply_override_accepts_valid(store: Store) -> None:
    merged = apply_override(
        store,
        key="position_frac",
        value="0.25",
        source="cli",
        sizing=_sizing(),
        equity=1000.0,
        min_notional_median=5.0,
        sl_max=0.02,
        max_leverage=20,
    )
    assert merged.position_frac == 0.25
    assert store.get_overrides()["position_frac"] == "0.25"
