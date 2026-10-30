"""bars 加载与时间切分测试。

规格：docs/03-数据与选币.md §5、§6
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.features.helpers import make_bars
from trade.common.config import BaseConfig
from trade.data.loader import load_bars, month_index, month_key, segment_of, window_bounds


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def test_load_bars_reads_and_filters(tmp_path) -> None:
    bars = make_bars(120, seed=1, base_ms=_ms(2026, 1, 1))
    data_root = tmp_path / "data"
    bars_dir = data_root / "bars" / "C01"
    bars_dir.mkdir(parents=True)
    bars.to_parquet(bars_dir / "2026-01.parquet", index=False)

    start = _ms(2026, 1, 1) + 10 * 60_000
    end = _ms(2026, 1, 1) + 20 * 60_000
    loaded = load_bars(data_root, "C01", start, end, holdout_start_ms=end)
    assert len(loaded) == 10
    assert loaded["open_time"].is_monotonic_increasing
    assert loaded["open_time"].iloc[0] == start


def test_load_bars_holdout_guard(tmp_path) -> None:
    bars = make_bars(10, seed=1, base_ms=_ms(2026, 1, 1))
    data_root = tmp_path / "data"
    bars_dir = data_root / "bars" / "C01"
    bars_dir.mkdir(parents=True)
    bars.to_parquet(bars_dir / "2026-01.parquet", index=False)

    with pytest.raises(ValueError, match="封存集"):
        load_bars(
            data_root,
            "C01",
            _ms(2026, 1, 1),
            _ms(2026, 2, 1),
            holdout_start_ms=_ms(2026, 1, 15),
            allow_holdout=False,
        )
    # allow_holdout=True 时放行
    loaded = load_bars(
        data_root,
        "C01",
        _ms(2026, 1, 1),
        _ms(2026, 1, 3),
        holdout_start_ms=_ms(2026, 1, 15),
        allow_holdout=True,
    )
    assert not loaded.empty


def test_month_key_and_index() -> None:
    assert month_key(_ms(2026, 1, 15)) == "2026-01"
    # end_month=2026-09, months=24 → M1=2024-10，2026-01 = M16
    assert month_index(_ms(2026, 1, 15), "2026-09", 24) == 16
    assert month_index(_ms(2024, 9, 30), "2026-09", 24) is None  # 窗口前


def test_segment_of() -> None:
    assert segment_of(_ms(2024, 10, 1), "2026-09", 24, 12, 2) == "tuning"  # M1
    assert segment_of(_ms(2025, 10, 1), "2026-09", 24, 12, 2) == "test"  # M13
    assert segment_of(_ms(2026, 8, 1), "2026-09", 24, 12, 2) == "holdout"  # M23
    assert segment_of(_ms(2024, 9, 1), "2026-09", 24, 12, 2) == "warmup"


def _base_cfg(tmp_path) -> BaseConfig:
    return BaseConfig(
        market="usdm",
        quote_asset="USDT",
        data_window={
            "end_month": "2026-09",
            "months": 24,
            "warmup_days": 30,
            "tuning_months": 12,
            "holdout_months": 2,
        },
        fees={"maker": 0.0002, "taker": 0.0005},
        display_tz="UTC",
        paths={"data_root": tmp_path / "data", "artifact_root": tmp_path / "artifacts"},
        seed=42,
    )


def test_window_bounds_from_base_config(tmp_path) -> None:
    wb = window_bounds(_base_cfg(tmp_path))
    # end_month=2026-09、months=24 → M1=2024-10；预热 30 天 → start=2024-09-01
    assert wb.start_ms == _ms(2024, 10, 1) - 30 * 86_400_000
    # end_ms = M24 之后一个月起点 = 2026-10-01
    assert wb.end_ms == _ms(2026, 10, 1)
    # holdout_start_ms = M23 起点 = 2026-08-01
    assert wb.holdout_start_ms == _ms(2026, 8, 1)


def test_request_holdout_without_final_raises(tmp_path) -> None:
    bars = make_bars(100, seed=1, base_ms=_ms(2026, 8, 1))
    data_root = tmp_path / "data"
    bars_dir = data_root / "bars" / "C01"
    bars_dir.mkdir(parents=True)
    bars.to_parquet(bars_dir / "2026-08.parquet", index=False)

    wb = window_bounds(_base_cfg(tmp_path))
    # 不带 --final（allow_holdout=False）请求覆盖 M23~M24 的完整窗口必须报错
    with pytest.raises(ValueError, match="封存集"):
        load_bars(
            data_root,
            "C01",
            wb.start_ms,
            wb.end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
    # --final（allow_holdout=True）放行
    loaded = load_bars(
        data_root,
        "C01",
        wb.holdout_start_ms,
        wb.end_ms,
        holdout_start_ms=wb.holdout_start_ms,
        allow_holdout=True,
    )
    assert not loaded.empty
