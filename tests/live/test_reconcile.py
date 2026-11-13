"""对账 6 种情况 + 成交/资金费/手续费差异单测。

规格：docs/07-实盘与风控.md §6.2
"""

from __future__ import annotations

from trade.live.reconcile import reconcile_fills, reconcile_positions


def _pos(symbol: str, position_id: str, qty: float = 1.0) -> dict[str, object]:
    return {"position_id": position_id, "symbol": symbol, "qty": qty, "status": "OPEN"}


def test_reconcile_adopt_when_exchange_has_position() -> None:
    actions = reconcile_positions(
        local_open=[],
        exchange_positions={"SOLUSDT": 1.0},
        exchange_stop_by_symbol={},
        open_order_client_ids=set(),
        local_stop_by_symbol={},
        sl_max=0.02,
    )
    assert any(a.kind == "adopt" and a.symbol == "SOLUSDT" for a in actions)


def test_reconcile_mark_closed_when_exchange_no_position() -> None:
    actions = reconcile_positions(
        local_open=[_pos("SOLUSDT", "p1")],
        exchange_positions={},
        exchange_stop_by_symbol={},
        open_order_client_ids=set(),
        local_stop_by_symbol={},
        sl_max=0.02,
    )
    assert any(a.kind == "mark_closed" and a.symbol == "SOLUSDT" for a in actions)


def test_reconcile_update_qty_on_mismatch() -> None:
    actions = reconcile_positions(
        local_open=[_pos("SOLUSDT", "p1", qty=1.0)],
        exchange_positions={"SOLUSDT": 0.5},
        exchange_stop_by_symbol={},
        open_order_client_ids=set(),
        local_stop_by_symbol={},
        sl_max=0.02,
    )
    assert any(a.kind == "update_qty" for a in actions)


def test_reconcile_place_stop_when_no_exchange_stop() -> None:
    actions = reconcile_positions(
        local_open=[_pos("SOLUSDT", "p1")],
        exchange_positions={"SOLUSDT": 1.0},
        exchange_stop_by_symbol={},
        open_order_client_ids=set(),
        local_stop_by_symbol={"SOLUSDT": 95.0},
        sl_max=0.02,
    )
    assert any(a.kind == "place_stop" and a.symbol == "SOLUSDT" for a in actions)


def test_reconcile_place_stop_when_exchange_stop_lower() -> None:
    actions = reconcile_positions(
        local_open=[_pos("SOLUSDT", "p1")],
        exchange_positions={"SOLUSDT": 1.0},
        exchange_stop_by_symbol={"SOLUSDT": 94.0},
        open_order_client_ids=set(),
        local_stop_by_symbol={"SOLUSDT": 95.0},
        sl_max=0.02,
    )
    assert any(a.kind == "place_stop" for a in actions)


def test_reconcile_cancel_orphan_order() -> None:
    actions = reconcile_positions(
        local_open=[],
        exchange_positions={},
        exchange_stop_by_symbol={},
        open_order_client_ids={"trd-SOL-x-S1"},
        local_stop_by_symbol={},
        sl_max=0.02,
    )
    assert any(a.kind == "cancel_order" for a in actions)


def test_reconcile_fills_six_differences() -> None:
    result = reconcile_fills(
        local_fill_ids={1},
        local_income_tran_ids={10},
        exchange_trades=[
            {"id": 1, "symbol": "SOLUSDT"},  # 已记
            {"id": 2, "symbol": "SOLUSDT"},  # 成交未记
        ],
        exchange_income=[
            {"tranId": 10, "incomeType": "FUNDING_FEE"},  # 已记
            {"tranId": 11, "incomeType": "COMMISSION"},  # 资金费/手续费未记
        ],
        local_open_client_ids={"trd-A", "trd-B"},
        exchange_open_client_ids={"trd-A"},  # trd-B 记了未成交/撤单
    )
    assert [t["id"] for t in result.missing_fills] == [2]
    assert [i["tranId"] for i in result.missing_income] == [11]
    assert result.cancelled_client_ids == ["trd-B"]


def test_返佣流水只统计返佣类型() -> None:
    from trade.live.reconcile import sum_rebate_income

    rows = [
        {"incomeType": "COMMISSION_REBATE", "income": "0.12"},
        {"incomeType": "FUNDING_FEE", "income": "-0.30"},
        {"incomeType": "FEE_RETURN", "income": "0.03"},
        {"incomeType": "REALIZED_PNL", "income": "1.00"},
    ]
    assert sum_rebate_income(rows) == 0.15
    assert sum_rebate_income([]) == 0.0
