"""策略权益、专用账户与密钥文件权限测试（3 号第五部分第 3 条、第八部分 6c）。

规格：docs/07-实盘与风控.md §5、§6
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trade.live.credentials import CredentialError, check_secret_permissions
from trade.live.runner import dedicated_account_errors, strategy_equity
from trade.live.store import Store


def test_策略权益按启动余额与上限取小() -> None:
    """账户里有 1000 USDT、上限 500 USDT 时，权益基准是 500 而不是 1000。"""
    equity = strategy_equity(
        wallet_start=1000.0, wallet_now=1000.0, unrealized=0.0, capital_cap=500.0
    )
    assert equity == pytest.approx(500.0)


def test_策略权益只计启动以来的盈亏() -> None:
    """R5~R8 的权益 = min(启动余额, 上限) + 启动以来已实现与未实现盈亏。"""
    equity = strategy_equity(
        wallet_start=1000.0, wallet_now=940.0, unrealized=-10.0, capital_cap=500.0
    )
    assert equity == pytest.approx(500.0 - 60.0 - 10.0)


def test_启动余额低于上限时直接使用() -> None:
    equity = strategy_equity(
        wallet_start=400.0, wallet_now=420.0, unrealized=5.0, capital_cap=500.0
    )
    assert equity == pytest.approx(425.0)


def test_专用账户检查通过() -> None:
    errors = dedicated_account_errors(
        wallet_balance=480.0,
        capital_cap=500.0,
        positions={"AAAUSDT": 1.0},
        known_symbols={"AAAUSDT"},
        open_client_ids={"trd-1"},
        known_client_ids={"trd-1"},
    )
    assert errors == []


def test_存在外部持仓时拒绝启动() -> None:
    errors = dedicated_account_errors(
        wallet_balance=480.0,
        capital_cap=500.0,
        positions={"AAAUSDT": 1.0, "BBBUSDT": -2.0},
        known_symbols={"AAAUSDT"},
        open_client_ids=set(),
        known_client_ids=set(),
    )
    assert any("BBBUSDT" in error for error in errors)


def test_存在外部挂单时拒绝启动() -> None:
    errors = dedicated_account_errors(
        wallet_balance=480.0,
        capital_cap=500.0,
        positions={},
        known_symbols=set(),
        open_client_ids={"trd-x"},
        known_client_ids=set(),
    )
    assert any("trd-x" in error for error in errors)


def test_余额超过上限一点五倍时拒绝启动() -> None:
    errors = dedicated_account_errors(
        wallet_balance=800.0,
        capital_cap=500.0,
        positions={},
        known_symbols=set(),
        open_client_ids=set(),
        known_client_ids=set(),
    )
    assert any("1.5" in error or "1.5 倍" in error for error in errors)


def test_影子盘权益不重复计算未实现盈亏(tmp_path: Path) -> None:
    """影子盘权益 = 初始权益 + 累计已实现（含手续费） + 未实现，只加一次。"""
    import asyncio
    from types import SimpleNamespace

    import numpy as np

    from trade.live.runner import Runner
    from trade.research.data import Bars

    store = Store(tmp_path / "state.db")
    try:
        store.update_risk_state("2024-01-01", realized_pnl=-5.0)
        store.upsert_shadow_position(
            candidate="C1",
            symbol="AAAUSDT",
            side=1,
            qty=2.0,
            entry_price=100.0,
            sl=0.05,
            opened_ms=1_704_067_200_000,
        )
        store.commit()
        bars = Bars(
            symbol="AAAUSDT",
            open_time=np.array([1_704_067_200_000], dtype=np.int64),
            open=np.array([100.0]),
            high=np.array([111.0]),
            low=np.array([99.0]),
            close=np.array([110.0]),
            quote_volume=np.array([1e7]),
        )
        runner = Runner.__new__(Runner)
        runner.mode = "shadow"
        runner.candidate = SimpleNamespace(candidate_id="C1")  # type: ignore[assignment]
        runner._store = store
        runner._sizing = SimpleNamespace(initial_equity=500.0)  # type: ignore[assignment]
        runner._feed = SimpleNamespace(hourly={"AAAUSDT": bars})  # type: ignore[assignment]
        runner._equity_baseline = None
        equity = asyncio.run(runner._equity({"AAAUSDT": 2.0}))
    finally:
        store.close()
    assert equity == pytest.approx(500.0 - 5.0 + 20.0)


def _fake_stat(mode: int, uid: int):  # type: ignore[no-untyped-def]
    def stat_fn(path: Path) -> os.stat_result:
        return os.stat_result((mode, 0, 0, 0, uid, 0, 0, 0, 0, 0))

    return stat_fn


def test_密钥文件权限不是600时拒绝(tmp_path: Path) -> None:
    path = tmp_path / ".env.live"
    path.write_text("X=1", encoding="utf-8")
    with pytest.raises(CredentialError, match="600"):
        check_secret_permissions([path], stat_fn=_fake_stat(0o644, 1000), expected_uid=1000)


def test_密钥文件属主不是运行用户时拒绝(tmp_path: Path) -> None:
    path = tmp_path / ".env.live"
    path.write_text("X=1", encoding="utf-8")
    with pytest.raises(CredentialError, match="属主"):
        check_secret_permissions([path], stat_fn=_fake_stat(0o600, 0), expected_uid=1000)


def test_密钥文件权限与属主正确时通过(tmp_path: Path) -> None:
    path = tmp_path / ".env.live"
    path.write_text("X=1", encoding="utf-8")
    check_secret_permissions([path], stat_fn=_fake_stat(0o600, 1000), expected_uid=1000)


def test_密钥文件不存在时跳过(tmp_path: Path) -> None:
    """没有实盘密钥时（shadow / paper）不做检查，也不创建文件。"""
    check_secret_permissions([tmp_path / "missing.env"], expected_uid=None)


def test_权益基准与策略持仓写入状态库(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    try:
        assert store.get_equity_baseline(candidate="C1", mode="live") is None
        store.set_equity_baseline(candidate="C1", mode="live", wallet_balance=480.0)
        store.commit()
        row = store.get_equity_baseline(candidate="C1", mode="live")
        assert row is not None and row["wallet_balance"] == pytest.approx(480.0)
        store.sync_strategy_positions(
            candidate="C1",
            rows=[{"symbol": "AAAUSDT", "qty": 1.5, "entry_price": 10.0, "sl": 0.01}],
        )
        store.commit()
        assert [row["symbol"] for row in store.list_strategy_positions("C1")] == ["AAAUSDT"]
        store.sync_strategy_positions(candidate="C1", rows=[])
        store.commit()
        assert store.list_strategy_positions("C1") == []
    finally:
        store.close()
