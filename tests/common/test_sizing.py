"""仓位计算与校验测试（06 §5）。"""

from __future__ import annotations

import pytest

from trade.common.sizing import (
    SizingConfig,
    calc_notional,
    equity_floor_hit,
    loss_rate,
    validate_sizing,
)

_BASE = dict(
    initial_equity=10,
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


def _cfg(**kwargs) -> SizingConfig:
    params = dict(_BASE)
    params.update(kwargs)
    return SizingConfig(**params)


def _calc(cfg, **kwargs):
    defaults = dict(
        base=10.0,
        sl=0.008,
        loss_rate=0.01,
        entry_price=100.0,
        mean_qv_60=1e6,
        available_margin=10.0,
        step_size=0.01,
        min_notional=5.0,
    )
    defaults.update(kwargs)
    return calc_notional(cfg=cfg, **defaults)


def test_position_frac乘max_positions超095被拒绝():
    with pytest.raises(ValueError):
        _cfg(position_frac=0.5, max_positions=3)  # 1.5 > 0.95


def test_daily_loss_stop不小于halt被拒绝():
    with pytest.raises(ValueError):
        _cfg(daily_loss_stop=0.1, daily_loss_halt=0.08)


def test_fixed_fraction基本仓位():
    r = _calc(_cfg())
    # 10 USDT × 20% × 5 = 10 notional, qty = 10/100 = 0.1, margin = 2
    assert r.notional == pytest.approx(10.0)
    assert r.qty == pytest.approx(0.1)
    assert r.margin == pytest.approx(2.0)
    assert r.capped_by is None
    assert r.status == "accepted"


def test_fixed_risk仓位():
    # loss_rate = sl + slip_stop + half_spread + 2*fee = 0.008 + 0.0004 + 0.0001 + 0.001 = 0.0095
    lr = loss_rate(sl=0.008, slip_stop=0.0004, half_spread=0.0001, fee_taker=0.0005)
    cfg = _cfg(mode="fixed_risk", risk_per_trade=0.005)
    r = _calc(cfg, loss_rate=lr)
    assert r.notional_raw == pytest.approx(10 * 0.005 / lr)


def test_低于最小下单额被放弃():
    # min_notional=20 → qty×P = 10 < 20×1.1 → below_min_notional
    r = _calc(_cfg(), min_notional=20.0)
    assert r.status == "below_min_notional"


def test_取整损失过大被放弃():
    # notional=10, P=6, step=1 → qty=1, qty×P=6 < 0.9×10=9 且 ≥ 2.2 → step_rounding
    r = _calc(_cfg(), entry_price=6.0, step_size=1.0, min_notional=2.0)
    assert r.status == "step_rounding"


def test_风险硬上限截断():
    # max_risk_per_trade=0.025, loss_rate=0.01 → risk_cap = 10*0.025/0.01 = 25
    # notional_raw=10 < 25 → 未截断
    r = _calc(_cfg())
    assert r.capped_by is None
    # 提高 notional_raw：base=1000 → notional_raw=1000，risk_cap=2500 → 仍未截断
    r2 = _calc(_cfg(), base=1000.0, available_margin=1000.0)
    assert r2.capped_by is None
    # loss_rate 很大时 risk_cap 小 → 截断
    r3 = _calc(_cfg(), base=1000.0, available_margin=1000.0, loss_rate=0.5)
    assert r3.capped_by == "risk_cap"


def test_流动性上限截断():
    # liq_cap=0.005, mean_qv_60=1000 → liq_amount=5 < 10 → liquidity 截断
    r = _calc(_cfg(), mean_qv_60=1000.0)
    assert r.capped_by == "liquidity"
    assert r.notional == pytest.approx(5.0)


def test_权益下限判定():
    # B × position_frac × leverage = 4 < 5×1.1=5.5 → 触及权益下限
    assert equity_floor_hit(4.0, _cfg(), 5.0) is True
    assert equity_floor_hit(10.0, _cfg(), 5.0) is False


def test_强平距离校验():
    errors = validate_sizing(
        _cfg(),
        min_notional_median=5.0,
        equity=10.0,
        sl_max=0.015,
        max_leverage=10,
    )
    assert errors == []
    # sl_max 超过强平距离上限
    errors2 = validate_sizing(
        _cfg(),
        min_notional_median=5.0,
        equity=10.0,
        sl_max=0.10,  # 0.5*(1/5-0.01)=0.095 < 0.10
        max_leverage=10,
    )
    assert any("强平距离" in e for e in errors2)


@pytest.mark.parametrize("use_numba", [True, False])
def test_numba与python一致(use_numba):
    a = _calc(_cfg(), use_numba=True)
    b = _calc(_cfg(), use_numba=False)
    assert a == b
