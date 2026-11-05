"""一次性修复脚本的纯函数测试（T1-9 遗留缺陷修复）。

规格：docs/03-数据与选币.md §2.2（`whale_thr(D) = p99(D-1)`）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.repair_first_day_whale import _atomic_from_bars, _find_targets, _needs_repair
from trade.data.aggregate import MINUTE_ATOMIC_COLUMNS

_DAY_MS = 86_400_000
_START_MS = 1_735_689_600_000  # 2025-01-01T00:00Z


def _bars(*, first_day_threshold: bool, first_day_missing: bool = False) -> pd.DataFrame:
    """构造两天的最小月文件：首日阈值可选、首日逐笔可整日缺失。"""
    times = np.arange(_START_MS, _START_MS + 2 * _DAY_MS, 60_000, dtype="int64")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "whale_thr": 100.0 if first_day_threshold else np.nan,
            "aggtrades_missing": 0,
            "quote_volume": 10.0,
        }
    )
    if first_day_missing:
        frame.loc[frame["open_time"] < _START_MS + _DAY_MS, "aggtrades_missing"] = 1
    return frame


def test_首日阈值缺失且有成交时需要修复() -> None:
    assert _needs_repair(_bars(first_day_threshold=False)) is True


def test_首日阈值已存在时不需要修复() -> None:
    assert _needs_repair(_bars(first_day_threshold=True)) is False


def test_首日整日无逐笔归档时不算可修复缺陷() -> None:
    # 整日归档缺失是真缺数据（不可修复），不进入修复清单
    assert _needs_repair(_bars(first_day_threshold=False, first_day_missing=True)) is False


def _atomic_frame() -> pd.DataFrame:
    times = np.arange(_START_MS, _START_MS + 3 * 60_000, 60_000, dtype="int64")
    frame = pd.DataFrame(
        {column: 1.0 for column in MINUTE_ATOMIC_COLUMNS},
        index=range(len(times)),
    )
    frame["open_time"] = times
    frame["agg_n"] = 5
    frame["aggtrades_missing"] = [0, 1, 0]
    return frame


def test_原子量取回时把缺失分钟恢复为NaN() -> None:
    atomic = _atomic_from_bars(_atomic_frame())

    assert atomic.loc[0, "buy_quote"] == 1.0
    assert atomic.loc[2, "buy_quote"] == 1.0
    assert pd.isna(atomic.loc[1, "buy_quote"])
    assert int(atomic.loc[1, "open_time"]) == _START_MS + 60_000


def test_find_targets按别名与月份筛选(tmp_path: Path) -> None:
    bars_root = tmp_path / "bars"
    for alias in ("C001", "C002"):
        directory = bars_root / alias
        directory.mkdir(parents=True)
        frame = _bars(first_day_threshold=alias == "C002")
        frame.to_parquet(directory / "202501.parquet", index=False)

    targets = _find_targets(bars_root, {"AAAUSDT": "C001", "BBBUSDT": "C002"}, set(), set())

    assert [(item.symbol, item.month) for item in targets] == [("AAAUSDT", "2025-01")]
    assert _find_targets(bars_root, {"AAAUSDT": "C001"}, {"C001"}, {"2025-02"}) == []


def test_find_targets跳过不在别名表中的市场环境目录(tmp_path: Path) -> None:
    bars_root = tmp_path / "bars"
    btc = bars_root / "BTCUSDT"
    btc.mkdir(parents=True)
    _bars(first_day_threshold=False).to_parquet(btc / "202501.parquet", index=False)

    assert _find_targets(bars_root, {"AAAUSDT": "C001"}, set(), set()) == []
