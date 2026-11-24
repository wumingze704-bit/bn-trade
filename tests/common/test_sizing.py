"""仓位计算与校验测试（06 §5）。"""

from __future__ import annotations

import pytest

from trade.common.sizing import (
    SizingConfig,
    calc_notional,
    equity_floor_hit,
    loss_rate,
    size_from_notional,
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


def _fixed_risk_cfg(**kwargs) -> SizingConfig:
    """fixed_risk 的实盘起始仓位（configs/sizing.yaml：8%、0.75%、5x）。"""
    params = dict(
        mode="fixed_risk",
        position_frac=0.08,
        risk_per_trade=0.0075,
        leverage=5,
        max_positions=5,
    )
    params.update(kwargs)
    return _cfg(**params)


@pytest.mark.parametrize("use_numba", [True, False])
def test_fixed_risk低波动币被每仓上限截断(use_numba):
    """止损距离小（低波动）时 notional_raw 超过每仓上限 → position_cap 截断（06 §5.3）。"""
    # B = 100，每仓上限 = 100 × 0.08 × 5 = 40
    # 止损距离 1% → notional_raw = 100 × 0.0075 / 0.01 = 75 > 40
    r = _calc(
        _fixed_risk_cfg(),
        base=100.0,
        sl=0.01,
        loss_rate=0.01,
        available_margin=100.0,
        mean_qv_60=1e7,
        use_numba=use_numba,
    )
    assert r.notional_raw == pytest.approx(75.0)
    assert r.notional == pytest.approx(40.0)
    assert r.capped_by == "position_cap"
    assert r.margin == pytest.approx(8.0)


@pytest.mark.parametrize("use_numba", [True, False])
def test_fixed_risk高波动币不被每仓上限截断(use_numba):
    """止损距离大（高波动）时 notional_raw 低于每仓上限 → 不截断（06 §5.3）。"""
    # 止损距离 5% → notional_raw = 100 × 0.0075 / 0.05 = 15 < 40
    r = _calc(
        _fixed_risk_cfg(),
        base=100.0,
        sl=0.05,
        loss_rate=0.05,
        available_margin=100.0,
        mean_qv_60=1e7,
        use_numba=use_numba,
    )
    assert r.notional == pytest.approx(15.0)
    assert r.capped_by is None
    assert r.margin == pytest.approx(3.0)


def test_fixed_fraction每仓上限行为不变():
    """fixed_fraction 的 notional_raw 已含 position_frac × leverage，不再叠加 position_cap。"""
    r = _calc(
        _cfg(position_frac=0.2, leverage=5), base=100.0, loss_rate=0.01, available_margin=100.0
    )
    assert r.notional == pytest.approx(100.0)
    assert r.capped_by is None


def test_多空名义比区间必须包含1():
    with pytest.raises(ValueError):
        _cfg(ls_ratio_range=(2.0, 0.5))
    assert _cfg(ls_ratio_range=(0.3, 3.0)).ls_ratio_range == (0.3, 3.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"min_notional": 20.0},
        {"entry_price": 6.0, "step_size": 1.0, "min_notional": 2.0},
    ],
)
def test_size_from_notional与calc_notional状态一致(overrides):
    """模式 B 直接给目标名义时，取整与状态判定必须与 calc_notional 同一实现（06 §5.3）。"""
    params = dict(entry_price=100.0, step_size=0.01, min_notional=5.0)
    params.update(overrides)
    result = _calc(_cfg(), **params)
    qty, status = size_from_notional(
        notional=result.notional,
        entry_price=params["entry_price"],
        step_size=params["step_size"],
        min_notional=params["min_notional"],
    )
    assert qty == pytest.approx(result.qty, rel=1e-12)
    assert status == result.status


def test_新配置字段默认保持模式A():
    """未写新字段的旧配置仍按模式 A 运行（默认值不改变旧行为）。"""
    cfg = _cfg()
    assert cfg.sizing_mode == "fixed_risk"
    assert cfg.target_vol == pytest.approx(0.20)
    assert cfg.vol_lookback_days == 30
    assert cfg.cov_lookback_days == 60
    assert cfg.beta_lookback_days == 60
    assert cfg.beta_abs_max == pytest.approx(0.10)
    assert cfg.ls_ratio_range == (0.5, 2.0)
