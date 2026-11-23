"""实盘 SQLite 状态库。

规格：docs/07-实盘与风控.md §6.1；docs/10-可视化看板.md §5

开启 WAL 模式供看板并发只读；所有写操作在事务中完成。`orders`/`fills`/`positions`
永久保留，其余按 `retention_days` 清理（07 §6.1）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import time
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_id TEXT PRIMARY KEY,
    exchange_id INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    price REAL,
    qty REAL,
    status TEXT NOT NULL,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    exchange_trade_id INTEGER PRIMARY KEY,
    client_id TEXT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    fee REAL,
    fee_asset TEXT,
    realized_pnl REAL,
    ts_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    alias TEXT,
    signal_t INTEGER,
    p REAL,
    m REAL,
    entry_price REAL,
    qty REAL,
    margin REAL,
    leverage INTEGER,
    sl REAL,
    atr_abs REAL,
    stop_price REAL,
    cap_price REAL,
    hh REAL,
    stop_updates INTEGER DEFAULT 0,
    exit_state_json TEXT,
    t_exit_deadline INTEGER,
    status TEXT NOT NULL,
    exit_type TEXT,
    exit_price REAL,
    fee REAL,
    funding REAL,
    pnl REAL,
    position_frac REAL
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    p REAL,
    m REAL,
    opened INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    ts_ms INTEGER PRIMARY KEY,
    wallet_balance REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    available_margin REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS latency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    stage TEXT NOT NULL,
    ms REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_state (
    date TEXT PRIMARY KEY,
    realized_pnl REAL NOT NULL DEFAULT 0,
    peak_equity REAL NOT NULL DEFAULT 0,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    halt_flag INTEGER NOT NULL DEFAULT 0,
    safe_mode_reason TEXT
);
CREATE TABLE IF NOT EXISTS feature_state (
    symbol TEXT NOT NULL,
    feature TEXT NOT NULL,
    ewm_mu REAL NOT NULL,
    ewm_var REAL NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (symbol, feature)
);
CREATE TABLE IF NOT EXISTS runtime_overrides (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    symbol TEXT,
    detail_json TEXT,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scan_cycles (
    open_time INTEGER PRIMARY KEY,
    started_ms INTEGER NOT NULL,
    finished_ms INTEGER NOT NULL,
    n_symbols INTEGER NOT NULL DEFAULT 0,
    n_prefilter INTEGER NOT NULL DEFAULT 0,
    n_signal INTEGER NOT NULL DEFAULT 0,
    n_opened INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scan_state (
    symbol TEXT NOT NULL,
    alias TEXT,
    open_time INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL,
    prefilter INTEGER,
    p REAL,
    m REAL,
    decision TEXT,
    skip_reason TEXT,
    spread_bp REAL,
    processed_ms REAL,
    PRIMARY KEY (symbol, open_time)
);
"""

# 低频引擎新增表（docs/07 §6.1）。旧表结构保持不变，保证看板旧接口与测试可继续使用。
_ENGINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_status (
    candidate TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    pid INTEGER,
    started_ms INTEGER NOT NULL,
    heartbeat_ms INTEGER NOT NULL,
    last_rebalance_ms INTEGER,
    next_rebalance_ms INTEGER,
    version TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS rebalance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate TEXT NOT NULL,
    mode TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    finished_ms INTEGER NOT NULL,
    equity REAL NOT NULL,
    long_notional REAL NOT NULL,
    short_notional REAL NOT NULL,
    cost_bp REAL,
    est_cost_bp REAL,
    n_targets INTEGER NOT NULL DEFAULT 0,
    n_orders INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS consistency_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    agree_ratio REAL,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS halt_flags (
    candidate TEXT NOT NULL,
    mode TEXT NOT NULL,
    halted INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (candidate, mode)
);
CREATE TABLE IF NOT EXISTS control_flags (
    candidate TEXT PRIMARY KEY,
    pause_open INTEGER NOT NULL DEFAULT 0,
    flatten_requested INTEGER NOT NULL DEFAULT 0,
    updated_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_positions (
    candidate TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side INTEGER NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    sl REAL NOT NULL,
    opened_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (candidate, symbol)
);
"""


def _now_ms() -> int:
    return int(time() * 1000)


class Store:
    """实盘状态库读写封装（07 §6.1）。

    Args:
        db_path: SQLite 数据库文件路径。
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.executescript(_ENGINE_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> None:
        self._conn.execute(sql, params)

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self._conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def commit(self) -> None:
        """提交当前事务（07 §6.1：所有写入在事务中完成）。"""
        self._conn.commit()

    # ---- orders ----

    def upsert_order(
        self,
        *,
        client_id: str,
        symbol: str,
        side: str,
        order_type: str,
        price: float | None,
        qty: float,
        status: str,
        exchange_id: int | None = None,
        raw_json: dict[str, Any] | None = None,
    ) -> None:
        """写入或更新订单记录（07 §5.4、§6.1）。"""
        now = _now_ms()
        raw = json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None
        self._execute(
            """
            INSERT INTO orders
                (client_id, exchange_id, symbol, side, type, price, qty, status,
                 created_ms, updated_ms, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                exchange_id=excluded.exchange_id, status=excluded.status,
                updated_ms=excluded.updated_ms, raw_json=excluded.raw_json
            """,
            (client_id, exchange_id, symbol, side, order_type, price, qty, status, now, now, raw),
        )

    def get_order(self, client_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM orders WHERE client_id = ?", (client_id,))
        return rows[0] if rows else None

    def open_orders(self) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM orders WHERE status IN ('NEW', 'PARTIALLY_FILLED', 'PENDING_NEW')"
        )

    # ---- fills ----

    def insert_fill(
        self,
        *,
        exchange_trade_id: int,
        client_id: str,
        symbol: str,
        price: float,
        qty: float,
        fee: float,
        fee_asset: str,
        realized_pnl: float,
        ts_ms: int,
    ) -> None:
        self._execute(
            """
            INSERT OR IGNORE INTO fills
                (exchange_trade_id, client_id, symbol, price, qty, fee, fee_asset,
                 realized_pnl, ts_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (exchange_trade_id, client_id, symbol, price, qty, fee, fee_asset, realized_pnl, ts_ms),
        )

    # ---- positions ----

    def upsert_position(self, position: dict[str, Any]) -> None:
        """写入或更新持仓记录（07 §6.1）。"""
        self._execute(
            """
            INSERT INTO positions
                (position_id, symbol, alias, signal_t, p, m, entry_price, qty, margin,
                 leverage, sl, atr_abs, stop_price, cap_price, hh, stop_updates,
                 exit_state_json, t_exit_deadline, status, exit_type, exit_price, fee,
                 funding, pnl, position_frac)
            VALUES
                (:position_id, :symbol, :alias, :signal_t, :p, :m, :entry_price, :qty,
                 :margin, :leverage, :sl, :atr_abs, :stop_price, :cap_price, :hh,
                 :stop_updates, :exit_state_json, :t_exit_deadline, :status, :exit_type,
                 :exit_price, :fee, :funding, :pnl, :position_frac)
            ON CONFLICT(position_id) DO UPDATE SET
                stop_price=excluded.stop_price, cap_price=excluded.cap_price,
                hh=excluded.hh, stop_updates=excluded.stop_updates,
                exit_state_json=excluded.exit_state_json, status=excluded.status,
                exit_type=excluded.exit_type, exit_price=excluded.exit_price,
                fee=excluded.fee, funding=excluded.funding, pnl=excluded.pnl
            """,
            position,
        )

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM positions WHERE position_id = ?", (position_id,))
        return rows[0] if rows else None

    def open_positions(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM positions WHERE status = 'OPEN'")

    # ---- signals / equity / latency ----

    def insert_signal(
        self,
        *,
        symbol: str,
        open_time: int,
        p: float | None,
        m: float | None,
        opened: int,
        skip_reason: str | None,
    ) -> None:
        self._execute(
            "INSERT INTO signals (symbol, open_time, p, m, opened, skip_reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, open_time, p, m, opened, skip_reason),
        )

    def insert_equity_snapshot(
        self, *, ts_ms: int, wallet_balance: float, unrealized_pnl: float, available_margin: float
    ) -> None:
        self._execute(
            "INSERT INTO equity_snapshots "
            "(ts_ms, wallet_balance, unrealized_pnl, available_margin) VALUES (?, ?, ?, ?)",
            (ts_ms, wallet_balance, unrealized_pnl, available_margin),
        )

    def latest_equity_snapshot(self) -> dict[str, Any] | None:
        """读取最近一条权益快照（shadow 虚拟账户重启后恢复钱包余额）。"""
        rows = self._query("SELECT * FROM equity_snapshots ORDER BY ts_ms DESC LIMIT 1")
        return rows[0] if rows else None

    def insert_latency(self, *, ts_ms: int, stage: str, ms: float) -> None:
        self._execute("INSERT INTO latency (ts_ms, stage, ms) VALUES (?, ?, ?)", (ts_ms, stage, ms))

    # ---- risk_state ----

    def get_risk_state(self, date: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM risk_state WHERE date = ?", (date,))
        return rows[0] if rows else None

    def set_halt_flag(self, value: int, *, date: str) -> None:
        self._execute(
            """
            INSERT INTO risk_state (date, halt_flag) VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET halt_flag=excluded.halt_flag
            """,
            (date, value),
        )

    def set_safe_mode_reason(self, reason: str | None, *, date: str) -> None:
        self._execute(
            """
            INSERT INTO risk_state (date, safe_mode_reason) VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET safe_mode_reason=excluded.safe_mode_reason
            """,
            (date, reason),
        )

    def update_risk_state(self, date: str, **fields: Any) -> None:
        columns = ", ".join(f"{key} = ?" for key in fields)
        self._execute(f"UPDATE risk_state SET {columns} WHERE date = ?", (*fields.values(), date))

    # ---- feature_state ----

    def save_feature_state(
        self, *, symbol: str, feature: str, ewm_mu: float, ewm_var: float, updated_ms: int
    ) -> None:
        self._execute(
            """
            INSERT INTO feature_state (symbol, feature, ewm_mu, ewm_var, updated_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, feature) DO UPDATE SET
                ewm_mu=excluded.ewm_mu, ewm_var=excluded.ewm_var, updated_ms=excluded.updated_ms
            """,
            (symbol, feature, ewm_mu, ewm_var, updated_ms),
        )

    def load_feature_state(self, symbol: str) -> dict[str, dict[str, Any]]:
        rows = self._query("SELECT * FROM feature_state WHERE symbol = ?", (symbol,))
        return {row["feature"]: row for row in rows}

    # ---- runtime_overrides ----

    def set_override(self, *, key: str, value: str, source: str) -> None:
        self._execute(
            """
            INSERT INTO runtime_overrides (key, value, updated_ms, source) VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                updated_ms=excluded.updated_ms, source=excluded.source
            """,
            (key, value, _now_ms(), source),
        )

    def get_overrides(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT * FROM runtime_overrides")}

    def reset_overrides(self) -> None:
        self._execute("DELETE FROM runtime_overrides")

    # ---- events ----

    def insert_event(
        self,
        *,
        level: str,
        event: str,
        symbol: str | None,
        detail: dict[str, Any] | None,
        notified: int = 0,
    ) -> int:
        detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None
        cursor = self._conn.execute(
            "INSERT INTO events (ts_ms, level, event, symbol, detail_json, notified) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_now_ms(), level, event, symbol, detail_json, notified),
        )
        return int(cursor.lastrowid or 0)

    def mark_event_notified(self, event_id: int) -> None:
        self._execute("UPDATE events SET notified = 1 WHERE id = ?", (event_id,))

    # ---- scan_cycles / scan_state（07 §3.4、T5-12）----

    def write_scan(self, *, cycle: dict[str, Any], states: list[dict[str, Any]]) -> None:
        """每轮决策结束一次性事务写入扫描周期与每币状态（07 §3.4）。"""
        self._execute(
            """
            INSERT INTO scan_cycles
                (open_time, started_ms, finished_ms, n_symbols, n_prefilter, n_signal, n_opened)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle["open_time"],
                cycle["started_ms"],
                cycle["finished_ms"],
                cycle["n_symbols"],
                cycle["n_prefilter"],
                cycle["n_signal"],
                cycle["n_opened"],
            ),
        )
        for state in states:
            self._execute(
                """
                INSERT INTO scan_state
                    (symbol, alias, open_time, seq, status, prefilter, p, m, decision,
                     skip_reason, spread_bp, processed_ms)
                VALUES
                    (:symbol, :alias, :open_time, :seq, :status, :prefilter, :p, :m,
                     :decision, :skip_reason, :spread_bp, :processed_ms)
                ON CONFLICT(symbol, open_time) DO UPDATE SET
                    seq=excluded.seq, status=excluded.status, prefilter=excluded.prefilter,
                    p=excluded.p, m=excluded.m, decision=excluded.decision,
                    skip_reason=excluded.skip_reason, spread_bp=excluded.spread_bp,
                    processed_ms=excluded.processed_ms
                """,
                state,
            )

    # ---- 低频引擎：运行状态、调仓、停机与控制（docs/07 §6.1）----

    def upsert_engine_status(
        self,
        *,
        candidate: str,
        mode: str,
        pid: int | None,
        started_ms: int,
        heartbeat_ms: int,
        last_rebalance_ms: int | None,
        next_rebalance_ms: int | None,
        version: str,
        note: str | None = None,
    ) -> None:
        """写入或更新引擎心跳（每 5 分钟一次；看门狗与看板读取）。"""
        self._execute(
            """
            INSERT INTO engine_status
                (candidate, mode, pid, started_ms, heartbeat_ms, last_rebalance_ms,
                 next_rebalance_ms, version, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate) DO UPDATE SET
                mode=excluded.mode, pid=excluded.pid, started_ms=excluded.started_ms,
                heartbeat_ms=excluded.heartbeat_ms, last_rebalance_ms=excluded.last_rebalance_ms,
                next_rebalance_ms=excluded.next_rebalance_ms, version=excluded.version,
                note=excluded.note
            """,
            (
                candidate,
                mode,
                pid,
                started_ms,
                heartbeat_ms,
                last_rebalance_ms,
                next_rebalance_ms,
                version,
                note,
            ),
        )

    def get_engine_status(self, candidate: str) -> dict[str, Any] | None:
        """读取某候选的引擎心跳（不存在返回 None）。"""
        rows = self._query("SELECT * FROM engine_status WHERE candidate = ?", (candidate,))
        return rows[0] if rows else None

    def list_engine_status(self) -> list[dict[str, Any]]:
        """读取全部候选的引擎心跳，按最近心跳倒序（看板总览用）。"""
        return self._query("SELECT * FROM engine_status ORDER BY heartbeat_ms DESC")

    def insert_rebalance_log(
        self,
        *,
        candidate: str,
        mode: str,
        open_time: int,
        finished_ms: int,
        equity: float,
        long_notional: float,
        short_notional: float,
        cost_bp: float | None,
        est_cost_bp: float | None,
        n_targets: int,
        n_orders: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """写入一条调仓记录（目标、成交与成本对比，docs/07 §6.7）。"""
        detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None
        self._execute(
            """
            INSERT INTO rebalance_log
                (candidate, mode, open_time, finished_ms, equity, long_notional,
                 short_notional, cost_bp, est_cost_bp, n_targets, n_orders, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate,
                mode,
                open_time,
                finished_ms,
                equity,
                long_notional,
                short_notional,
                cost_bp,
                est_cost_bp,
                n_targets,
                n_orders,
                detail_json,
            ),
        )

    def list_rebalance_log(self, candidate: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """按时间倒序读取调仓记录。"""
        return self._query(
            "SELECT * FROM rebalance_log WHERE candidate = ? ORDER BY open_time DESC LIMIT ?",
            (candidate, limit),
        )

    def insert_consistency(
        self,
        *,
        candidate: str,
        kind: str,
        ts_ms: int,
        agree_ratio: float | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """写入一条一致性比对结果（shadow 与回放、live 与 shadow）。"""
        detail_json = json.dumps(detail, ensure_ascii=False) if detail is not None else None
        self._execute(
            "INSERT INTO consistency_log (candidate, kind, ts_ms, agree_ratio, detail_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (candidate, kind, ts_ms, agree_ratio, detail_json),
        )

    def list_consistency(self, candidate: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """按时间倒序读取一致性比对结果。"""
        return self._query(
            "SELECT * FROM consistency_log WHERE candidate = ? ORDER BY ts_ms DESC LIMIT ?",
            (candidate, limit),
        )

    def set_engine_halt(
        self, *, candidate: str, mode: str, halted: bool, reason: str | None
    ) -> None:
        """写入停机标志（引擎拒绝开新仓；只能由使用者用 kill_switch 清除）。"""
        self._execute(
            """
            INSERT INTO halt_flags (candidate, mode, halted, reason, updated_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(candidate, mode) DO UPDATE SET
                halted=excluded.halted, reason=excluded.reason, updated_ms=excluded.updated_ms
            """,
            (candidate, mode, int(halted), reason, _now_ms()),
        )

    def get_engine_halt(self, *, candidate: str, mode: str) -> dict[str, Any] | None:
        """读取停机标志（不存在返回 None）。"""
        rows = self._query(
            "SELECT * FROM halt_flags WHERE candidate = ? AND mode = ?", (candidate, mode)
        )
        return rows[0] if rows else None

    def clear_engine_halt(self, *, candidate: str, mode: str) -> None:
        """清除停机标志（仅供使用者显式确认后调用）。"""
        self._execute("DELETE FROM halt_flags WHERE candidate = ? AND mode = ?", (candidate, mode))

    def set_control_flags(
        self,
        *,
        candidate: str,
        pause_open: bool | None = None,
        flatten_requested: bool | None = None,
    ) -> None:
        """写入暂停开仓 / 请求平仓标志（看板控制页与 set_sizing.py 共用）。"""
        current = self.get_control_flags(candidate)
        pause = current["pause_open"] if pause_open is None else int(pause_open)
        flatten = (
            current["flatten_requested"] if flatten_requested is None else int(flatten_requested)
        )
        self._execute(
            """
            INSERT INTO control_flags (candidate, pause_open, flatten_requested, updated_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(candidate) DO UPDATE SET
                pause_open=excluded.pause_open, flatten_requested=excluded.flatten_requested,
                updated_ms=excluded.updated_ms
            """,
            (candidate, pause, flatten, _now_ms()),
        )

    def get_control_flags(self, candidate: str) -> dict[str, Any]:
        """读取控制标志；没有记录时返回关闭状态。"""
        rows = self._query("SELECT * FROM control_flags WHERE candidate = ?", (candidate,))
        if rows:
            return rows[0]
        return {"candidate": candidate, "pause_open": 0, "flatten_requested": 0, "updated_ms": 0}

    def clear_flatten_request(self, candidate: str) -> None:
        """清除一次性平仓请求。"""
        self._execute(
            "UPDATE control_flags SET flatten_requested = 0, updated_ms = ? WHERE candidate = ?",
            (_now_ms(), candidate),
        )

    # ---- 影子盘虚拟持仓（shadow 模式，docs/07 §6.4）----

    def upsert_shadow_position(
        self,
        *,
        candidate: str,
        symbol: str,
        side: int,
        qty: float,
        entry_price: float,
        sl: float,
        opened_ms: int,
    ) -> None:
        """写入或更新影子盘虚拟持仓。"""
        self._execute(
            """
            INSERT INTO shadow_positions
                (candidate, symbol, side, qty, entry_price, sl, opened_ms, updated_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate, symbol) DO UPDATE SET
                side=excluded.side, qty=excluded.qty, entry_price=excluded.entry_price,
                sl=excluded.sl, updated_ms=excluded.updated_ms
            """,
            (candidate, symbol, side, qty, entry_price, sl, opened_ms, _now_ms()),
        )

    def delete_shadow_position(self, *, candidate: str, symbol: str) -> None:
        """删除影子盘虚拟持仓（平仓或止损后）。"""
        self._execute(
            "DELETE FROM shadow_positions WHERE candidate = ? AND symbol = ?", (candidate, symbol)
        )

    def list_shadow_positions(self, candidate: str) -> list[dict[str, Any]]:
        """读取影子盘全部虚拟持仓。"""
        return self._query(
            "SELECT * FROM shadow_positions WHERE candidate = ? ORDER BY symbol", (candidate,)
        )
