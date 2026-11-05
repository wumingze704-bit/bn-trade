"""状态库查询测试。

规格：docs/10-可视化看板.md §5.1、§6、§9、§12
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from trade.dashboard.state_db import (
    open_state_db_readonly,
    query_closed_fills,
    query_equity_curve,
    query_events,
    query_latest_cycle,
    query_latest_equity,
    query_orders,
    query_positions,
    query_risk_state,
    query_scan_states,
    query_status,
)


def _open(path: Path) -> sqlite3.Connection:
    return open_state_db_readonly(path)


def test_扫描状态按seq升序(state_db: Path) -> None:
    """点亮顺序必须与 scan_state.seq 一致（10 §6.2）。"""
    connection = _open(state_db)
    try:
        states = query_scan_states(connection)
    finally:
        connection.close()
    assert [state["seq"] for state in states] == [1, 2, 3]
    assert [state["alias"] for state in states] == ["C002", "C001", "C003"]
    assert states[1]["prefilter"] == 1
    assert states[1]["decision"] == "open"


def test_扫描统计(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        cycle = query_latest_cycle(connection)
        status = query_status(connection)
    finally:
        connection.close()
    assert cycle is not None
    assert cycle["n_symbols"] == 3
    assert cycle["n_prefilter"] == 1
    assert cycle["n_signal"] == 1
    assert cycle["n_opened"] == 1
    assert cycle["finished_ms"] - cycle["started_ms"] == 182
    assert status["heartbeat_ms"] == cycle["finished_ms"]
    assert status["halted"] is False


def test_持仓挂单与事件(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        positions = query_positions(connection)
        orders = query_orders(connection)
        events = query_events(connection, limit=2)
    finally:
        connection.close()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAAUSDT"
    assert positions[0]["stop_price"] == pytest.approx(1.02)

    by_id = {order["client_id"]: order for order in orders}
    assert by_id["trd-stop-1"]["valid"] is True
    assert by_id["trd-tp-1"]["valid"] is False

    assert len(events) == 2
    assert events[0]["level"] == "error"  # 最新的一条
    assert events[0]["notified"] == 0


def test_权益曲线与回撤(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        latest = query_latest_equity(connection)
        points = query_equity_curve(connection, range_key="1D", now_ms=int(time.time() * 1000))
    finally:
        connection.close()
    assert latest is not None
    assert latest["equity"] == pytest.approx(10.35)
    assert len(points) == 2
    assert points[-1]["equity"] == pytest.approx(10.35)
    assert points[-1]["drawdown"] == pytest.approx(0.0)


def test_权益曲线范围过滤(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        points = query_equity_curve(
            connection,
            range_key="1D",
            now_ms=int(time.time() * 1000) + 2 * 86_400_000,
        )
    finally:
        connection.close()
    assert points == []


def test_今日平仓成交(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        fills = query_closed_fills(
            connection,
            start_ms=int(time.time() * 1000) - 86_400_000,
            end_ms=int(time.time() * 1000),
        )
    finally:
        connection.close()
    assert len(fills) == 2
    assert sum(fill["realized_pnl"] for fill in fills) == pytest.approx(0.23)


def test_风控状态(state_db: Path) -> None:
    connection = _open(state_db)
    try:
        risk = query_risk_state(connection)
    finally:
        connection.close()
    assert risk is not None
    assert risk["realized_pnl"] == pytest.approx(0.23)
    assert risk["halt_flag"] == 0


def test_只读连接拒绝写入(state_db: Path) -> None:
    """看板只读状态库（10 §9）。"""
    connection = _open(state_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM orders")
    finally:
        connection.close()
