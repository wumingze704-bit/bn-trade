"""目标仓位对账测试（docs/07 §6.1）。"""

from __future__ import annotations

import pytest

from trade.live.targets import TradeIntent, held_directions, net_exposure, plan_delta


def test_空仓到目标先开仓() -> None:
    intents = plan_delta(
        {}, {"AAAUSDT": 2.0, "BBBUSDT": -3.0}, step_sizes={"AAAUSDT": 0.1, "BBBUSDT": 0.1}
    )
    assert [(item.symbol, item.qty, item.reduce_only) for item in intents] == [
        ("AAAUSDT", 2.0, False),
        ("BBBUSDT", -3.0, False),
    ]


def test_平仓与减仓使用reduce_only并排在开仓前() -> None:
    intents = plan_delta(
        {"AAAUSDT": 3.0, "BBBUSDT": -2.0},
        {"AAAUSDT": 0.0, "BBBUSDT": -1.0, "CCCUSDT": 1.0},
        step_sizes={"AAAUSDT": 0.1, "BBBUSDT": 0.1, "CCCUSDT": 0.1},
    )
    assert [item.symbol for item in intents] == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert all(item.reduce_only for item in intents[:2])
    assert intents[0].qty == pytest.approx(-3.0, rel=0, abs=1e-9)
    assert intents[1].qty == pytest.approx(1.0, rel=0, abs=1e-9)
    assert intents[2].reduce_only is False


def test_方向相反时先平后开() -> None:
    intents = plan_delta({"AAAUSDT": 2.0}, {"AAAUSDT": -2.5}, step_sizes={"AAAUSDT": 0.5})
    assert [(item.qty, item.reduce_only) for item in intents] == [(-2.0, True), (-2.5, False)]


def test_已对齐时无订单() -> None:
    assert plan_delta({"AAAUSDT": 2.0}, {"AAAUSDT": 2.0}, step_sizes={"AAAUSDT": 0.1}) == []


def test_数量按step向下取整() -> None:
    intents = plan_delta({}, {"AAAUSDT": 1.27}, step_sizes={"AAAUSDT": 0.1})
    assert intents[0].qty == pytest.approx(1.2, rel=0, abs=1e-9)


def test_缺少step_size直接报错() -> None:
    with pytest.raises(KeyError, match="stepSize"):
        plan_delta({}, {"AAAUSDT": 1.0}, step_sizes={})


def test_方向与敞口统计() -> None:
    qtys = {"AAAUSDT": 2.0, "BBBUSDT": -3.0}
    prices = {"AAAUSDT": 10.0, "BBBUSDT": 5.0}
    assert held_directions(qtys) == {"AAAUSDT": 1, "BBBUSDT": -1}
    assert net_exposure(qtys, prices) == pytest.approx(20.0 - 15.0)


def test_intent方向属性() -> None:
    assert TradeIntent("AAAUSDT", 1.0, False).side == "BUY"
    assert TradeIntent("AAAUSDT", -1.0, True).side == "SELL"
