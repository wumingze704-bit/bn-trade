"""06 §5.4 仓位速查表数值复现测试（T3-5）。

规格：docs/06-回测引擎.md §5.4（误差 < 5%）
"""

from __future__ import annotations

import pytest

_NOTIONAL = 100.0
# 06 §5.4 估算口径：止损滑点 0.04% + 点差 0.01% + 双边吃单费 0.10%
_COST = 0.0004 + 0.0001 + 0.0010


def _loss_usdt(price_move: float) -> float:
    """价格变动对应的净盈亏（USDT，100 USDT 名义价值）。"""
    return _NOTIONAL * (price_move - _COST)


@pytest.mark.parametrize(
    ("price_move", "expected"),
    [
        (-0.010, -1.15),  # 最窄初始止损 sl_min
        (-0.015, -1.65),  # 典型初始止损
        (-0.025, -2.65),  # 最宽初始止损 sl_max
        (0.0015, 0.0),  # 保本止损
        (0.016, 1.4),  # 追踪止损（典型 2R）
        (0.032, 3.1),  # 封顶止盈（4R）
    ],
)
def test_仓位速查表复现(price_move: float, expected: float) -> None:
    actual = _loss_usdt(price_move)
    if expected == 0.0:
        assert abs(actual) <= 0.005
    else:
        assert actual == pytest.approx(expected, rel=0.05)


def test_速查表占保证金比例() -> None:
    """占该笔保证金（20 USDT）的比例与 06 §5.4 一致（容差 5%）。"""
    margin = 20.0
    assert _loss_usdt(-0.010) / margin == pytest.approx(-0.058, rel=0.05)
    assert _loss_usdt(-0.015) / margin == pytest.approx(-0.083, rel=0.05)
    assert _loss_usdt(-0.025) / margin == pytest.approx(-0.133, rel=0.05)
