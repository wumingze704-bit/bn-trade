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
