"""状态库只读访问。

规格：docs/07-实盘与风控.md §6.1；docs/10-可视化看板.md §5.1、§6、§9
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# 有效挂单状态（10 §5.1：止损单不是“有效”时红色告警）
_VALID_ORDER_STATUS = frozenset({"NEW", "PARTIALLY_FILLED"})

_RANGE_MS = {
    "1D": 24 * 60 * 60 * 1000,
    "1W": 7 * 24 * 60 * 60 * 1000,
    "1M": 31 * 24 * 60 * 60 * 1000,
}


def state_db_exists(path: Path) -> bool:
    """判断状态库文件是否存在（10 §11：不存在则隐藏实盘页面）。"""
    return path.is_file()


def open_state_db_readonly(path: Path) -> sqlite3.Connection:
    """以只读 URI 打开状态库（10 §9）。

    `file:...?mode=ro` 只读连接，看板进程无法修改交易数据；
    WAL 模式下不阻塞交易进程写入。

    Args:
        path: `state.db` 路径。

    Returns:
        只读 SQLite 连接，`row_factory` 为 `sqlite3.Row`。
    """
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def has_table(connection: sqlite3.Connection, name: str) -> bool:
    """判断状态库中是否存在指定表（用于实盘页可用性判定）。"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def query_status(connection: sqlite3.Connection) -> dict[str, Any]:
    """读取顶栏与侧边栏需要的连接/风控状态（10 §5.0、§7）。

    状态库当前没有独立的 `heartbeat` 表（T5-12 只写入扫描表），心跳时间取
    最近一轮扫描的 `finished_ms`；没有扫描记录时返回 None。

    Args:
        connection: 只读状态库连接。

    Returns:
        含 `heartbeat_ms`、`halted`、`safe_mode_reason`、`weight_usage` 的字典。
    """
    heartbeat_ms: int | None = None
    if has_table(connection, "scan_cycles"):
        row = connection.execute("SELECT MAX(finished_ms) AS ts FROM scan_cycles").fetchone()
        heartbeat_ms = int(row["ts"]) if row is not None and row["ts"] is not None else None

    halted = False
    safe_mode_reason: str | None = None
    if has_table(connection, "risk_state"):
        row = connection.execute(
            "SELECT halt_flag, safe_mode_reason FROM risk_state ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            halted = bool(row["halt_flag"])
            safe_mode_reason = row["safe_mode_reason"]
    return {
        "heartbeat_ms": heartbeat_ms,
        "halted": halted,
        "safe_mode_reason": safe_mode_reason,
        # 请求权重用量由交易进程在告警/日志中体现，状态库没有该表（10 §5.0）。
        "weight_usage": None,
    }


def query_latest_cycle(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """读取最近一轮扫描统计（10 §6.1 `scan_cycles`）。"""
    if not has_table(connection, "scan_cycles"):
        return None
    row = connection.execute(
        "SELECT open_time, started_ms, finished_ms, n_symbols, n_prefilter, n_signal, n_opened "
        "FROM scan_cycles ORDER BY open_time DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def query_scan_states(
    connection: sqlite3.Connection, *, open_time: int | None = None
) -> list[dict[str, Any]]:
    """读取某轮扫描的全部币状态，按 `seq` 升序（10 §6.1、§6.2）。

    Args:
        connection: 只读连接。
        open_time: 指定轮次；None 时取最新一轮。

    Returns:
        状态行列表，按处理顺序 `seq` 升序；没有数据时返回空列表。
    """
    if not has_table(connection, "scan_state"):
        return []
    if open_time is None:
        row = connection.execute("SELECT MAX(open_time) AS t FROM scan_state").fetchone()
        if row is None or row["t"] is None:
            return []
        open_time = int(row["t"])
    rows = connection.execute(
        "SELECT symbol, alias, open_time, seq, status, prefilter, p, m, decision, "
        "skip_reason, spread_bp, processed_ms FROM scan_state "
        "WHERE open_time = ? ORDER BY seq ASC",
        (open_time,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_positions(
    connection: sqlite3.Connection, *, status: str = "OPEN"
) -> list[dict[str, Any]]:
    """读取持仓（默认未平仓），用于总览持仓卡与持仓页（10 §5.1、§5.2）。"""
    if not has_table(connection, "positions"):
        return []
    rows = connection.execute(
        "SELECT position_id, symbol, alias, signal_t, p, m, entry_price, qty, margin, "
        "leverage, sl, atr_abs, stop_price, cap_price, hh, stop_updates, "
        "t_exit_deadline, status, exit_type, exit_price, fee, funding, pnl, position_frac "
        "FROM positions WHERE status = ? ORDER BY symbol",
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_orders(connection: sqlite3.Connection, *, limit: int = 200) -> list[dict[str, Any]]:
    """读取当前挂单，并标记是否为“有效”状态（10 §5.1 红色告警）。"""
    if not has_table(connection, "orders"):
        return []
    rows = connection.execute(
        "SELECT client_id, exchange_id, symbol, side, type, price, qty, status, "
        "created_ms, updated_ms FROM orders ORDER BY updated_ms DESC LIMIT ?",
        (limit,),
    ).fetchall()
    orders = []
    for row in rows:
        item = dict(row)
        item["valid"] = str(item["status"]).upper() in _VALID_ORDER_STATUS
        orders.append(item)
    return orders


def query_events(connection: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    """读取最近事件（10 §5.1：最近 20 条，含是否已推送微信）。"""
    if not has_table(connection, "events"):
        return []
    rows = connection.execute(
        "SELECT id, ts_ms, level, event, symbol, detail_json, notified "
        "FROM events ORDER BY ts_ms DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_risk_state(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """读取最新风控状态（日已实现盈亏、峰值权益、停机标志）。"""
    if not has_table(connection, "risk_state"):
        return None
    row = connection.execute(
        "SELECT date, realized_pnl, peak_equity, consecutive_losses, halt_flag, safe_mode_reason "
        "FROM risk_state ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def query_latest_equity(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """读取最新权益快照：钱包余额 + 未实现盈亏（10 §5.1 指标卡）。"""
    if not has_table(connection, "equity_snapshots"):
        return None
    row = connection.execute(
        "SELECT ts_ms, wallet_balance, unrealized_pnl, available_margin "
        "FROM equity_snapshots ORDER BY ts_ms DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["equity"] = float(item["wallet_balance"]) + float(item["unrealized_pnl"])
    return item


def query_closed_fills(
    connection: sqlite3.Connection, *, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    """读取时间窗内已实现盈亏的成交（用于今日笔数/胜率/PF 指标卡）。

    状态库 `positions` 表没有平仓时间列，平仓统计改由 `fills.realized_pnl`
    按成交时间聚合；同一笔平仓可能有多条成交，按成交逐条计入（与交易所账单口径一致）。
    """
    if not has_table(connection, "fills"):
        return []
    rows = connection.execute(
        "SELECT exchange_trade_id, symbol, price, qty, fee, realized_pnl, ts_ms "
        "FROM fills WHERE realized_pnl IS NOT NULL AND ts_ms >= ? AND ts_ms < ? "
        "ORDER BY ts_ms ASC",
        (start_ms, end_ms),
    ).fetchall()
    return [dict(row) for row in rows]


def query_equity_curve(
    connection: sqlite3.Connection,
    *,
    range_key: str = "all",
    now_ms: int,
    max_points: int = 2000,
) -> list[dict[str, Any]]:
    """读取权益曲线（含盯市回撤），自动降采样（10 §5.1、§10）。

    Args:
        connection: 只读连接。
        range_key: `1D` / `1W` / `1M` / `all`。
        now_ms: 当前时间（UTC 毫秒），用于计算时间范围。
        max_points: 最多返回点数。

    Returns:
        `[{"ts_ms", "equity", "drawdown"}, ...]`，按时间升序。
    """
    if not has_table(connection, "equity_snapshots"):
        return []
    start_ms = now_ms - _RANGE_MS[range_key] if range_key in _RANGE_MS else None
    sql = (
        "SELECT ts_ms, wallet_balance, unrealized_pnl FROM equity_snapshots "
        + ("WHERE ts_ms >= ? " if start_ms is not None else "")
        + "ORDER BY ts_ms ASC"
    )
    rows = connection.execute(sql, (start_ms,) if start_ms is not None else ()).fetchall()
    points = [
        {
            "ts_ms": int(row["ts_ms"]),
            "equity": float(row["wallet_balance"]) + float(row["unrealized_pnl"]),
        }
        for row in rows
    ]
    return _with_drawdown(_downsample(points, max_points))


def _downsample(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """按固定步长降采样到最多 max_points 点（10 §10）。"""
    if max_points <= 0 or len(points) <= max_points:
        return points
    step = (len(points) + max_points - 1) // max_points
    sampled = points[::step]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled


def _with_drawdown(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按盯市权益计算回撤（06 §5.1：权益曲线按钱包余额 + 未实现盈亏）。"""
    peak = 0.0
    output: list[dict[str, Any]] = []
    for point in points:
        equity = point["equity"]
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        output.append({**point, "drawdown": drawdown})
    return output
