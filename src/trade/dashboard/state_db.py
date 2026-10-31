"""状态库只读访问。

规格：docs/07-实盘与风控.md §6.1；docs/10-可视化看板.md §9
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


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
