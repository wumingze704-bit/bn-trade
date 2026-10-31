"""按月交易范围与数据段辅助函数测试（T0.15，03 §6、06 §3.1 条件 8）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from trade.data.loader import (
    new_entry_symbols,
    segment_ids,
    universe_trade_mask,
)


def _ms(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp() * 1000)


def test_数据段划分() -> None:
    """相邻 open_time 间隔超过 1 分钟视为段界（03 §6）。"""
    contiguous = np.arange(10) * 60_000 + _ms(2026, 1, 1)
    assert np.all(segment_ids(contiguous) == 0)
    # 第 5 根之后出现超过 1 分钟的缺口 → 拆为两段
    gapped = contiguous.copy()
    gapped[5:] += 30 * 86_400_000  # 跳到 30 天后
    ids = segment_ids(gapped)
    assert ids[0] == 0 and ids[4] == 0 and ids[5] == 1


def test_交易范围掩码_不在范围月份不可交易() -> None:
    membership = {
        "2026-01": frozenset({"A", "B"}),
        "2026-02": frozenset({"A"}),
        "2026-03": frozenset({"A", "C"}),
    }
    open_time = np.array([_ms(2026, 1, 15), _ms(2026, 2, 15), _ms(2026, 3, 10)], dtype="int64")
    mask_a = universe_trade_mask(open_time, "A", membership, new_entry_delay_days=2)
    assert mask_a.tolist() == [True, True, True]
    # B 只在 1 月入选，2/3 月不可交易
    mask_b = universe_trade_mask(open_time, "B", membership, new_entry_delay_days=2)
    assert mask_b.tolist() == [True, False, False]


def test_新进入币前2天不可开仓() -> None:
    membership = {"2026-02": frozenset({"A"}), "2026-03": frozenset({"C"})}
    # C 3 月首次入选（新进入），3 月 1 日 06:00 在前 2 天内 → False；3 月 10 日 → True
    open_time = np.array([_ms(2026, 3, 1, 6), _ms(2026, 3, 10)], dtype="int64")
    mask_c = universe_trade_mask(open_time, "C", membership, new_entry_delay_days=2)
    assert mask_c.tolist() == [False, True]


def test_新进入组_训练段从未入选() -> None:
    membership = {
        "2026-01": frozenset({"A", "B"}),
        "2026-02": frozenset({"A"}),
        "2026-03": frozenset({"A", "C"}),
    }
    # 前 2 个月为训练段：C 首次入选在第 3 个月 → 新进入组
    assert new_entry_symbols(membership, tuning_months=2) == frozenset({"C"})
    # 前 3 个月为训练段：C 在训练段内 → 不再算新进入
    assert new_entry_symbols(membership, tuning_months=3) == frozenset()
