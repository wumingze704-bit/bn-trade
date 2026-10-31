"""数据质量报告测试。

规格：docs/03-数据与选币.md §2.4
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.data.quality import (
    compute_quality,
    compute_quality_windows,
    load_quality_cache,
    write_quality_cache,
)


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [0, 60000, 120000, 180000],
            "close": [100.0, 101.0, 102.0, 130.0],  # 最后一根 +27.5% 价格跳变
            "quote_volume": [1000.0, 1000.0, 1000.0, 1000.0],
            "buy_quote": [500.0, 500.0, 500.0, 500.0],
            "sell_quote": [500.0, 500.0, 500.0, 500.0],
            "is_filled": [0, 1, 0, 0],
        }
    )


def test_compute_quality_basic_metrics() -> None:
    report = compute_quality("X", _bars())
    assert report.total_minutes == 4
    assert report.missing_count == 1
    assert report.missing_ratio == 0.25
    assert report.max_contiguous_missing == 1
    # 一致性：buy+sell == quote_volume，误差中位数为 0
    assert report.consistency_error_median == 0.0
    assert report.max_consistency_error == 0.0
    assert report.aggtrades_missing_count == 0
    # |1m 收益| > 20% 的一根（100→101、101→102、102→130 = 27.5%）
    assert report.price_jumps == 1


def test_compute_quality_detects_missing_aggtrades() -> None:
    # 某分钟 klines 有成交，但 aggTrades 数据缺失（buy_quote/sell_quote 为 NaN）
    bars = pd.DataFrame(
        {
            "open_time": [0, 60000, 120000],
            "close": [100.0, 101.0, 102.0],
            "quote_volume": [1000.0, 1000.0, 1000.0],
            "buy_quote": [500.0, float("nan"), 500.0],
            "sell_quote": [500.0, float("nan"), 500.0],
            "is_filled": [0, 0, 0],
        }
    )
    report = compute_quality("X", bars)
    assert report.aggtrades_missing_count == 1
    assert report.consistency_error_median == 0.0
    assert report.max_consistency_error == 0.0
    assert not report.qualified  # 有真实成交分钟缺 aggTrades → 不合格


def test_compute_quality_marks_unqualified_on_missing() -> None:
    report = compute_quality("X", _bars(), missing_ratio_max=0.01)
    assert not report.qualified


def test_compute_quality_marks_unqualified_on_consistency() -> None:
    bars = _bars()
    bars["buy_quote"] = [0.0, 0.0, 0.0, 0.0]
    bars["sell_quote"] = [0.0, 0.0, 0.0, 0.0]  # 与 quote_volume 完全不匹配
    report = compute_quality("X", bars)
    assert report.consistency_error_median == 1.0
    assert not report.qualified


def test_compute_quality_longest_contiguous_missing() -> None:
    bars = _bars()
    bars["is_filled"] = [1, 1, 0, 1]
    report = compute_quality("X", bars)
    assert report.max_contiguous_missing == 2


def test_quality_window_uses_30_day_denominator_and_round_trips_cache(tmp_path) -> None:
    minute_count = 30 * 24 * 60
    open_time = np.arange(minute_count, dtype="int64") * 60_000
    kline_missing = np.zeros(minute_count, dtype="int8")
    kline_missing[:27] = 1
    quote_volume = np.full(minute_count, 100.0)
    quote_volume[:27] = 0.0
    source_missing = np.zeros(minute_count, dtype="int8")
    source_missing[: 24 * 60] = 1
    threshold_missing = np.zeros(minute_count, dtype="int8")
    threshold_missing[:3] = 1
    bars = pd.DataFrame(
        {
            "open_time": open_time,
            "close": np.full(minute_count, 100.0),
            "quote_volume": quote_volume,
            "buy_quote": quote_volume / 2,
            "sell_quote": quote_volume / 2,
            "kline_missing": kline_missing,
            "aggtrades_missing": np.zeros(minute_count, dtype="int8"),
            "aggtrades_source_missing": source_missing,
            "whale_threshold_missing": threshold_missing,
            "whale_thr": np.ones(minute_count),
            "agg_n": np.ones(minute_count, dtype="int64"),
            "is_filled": kline_missing,
        }
    )

    daily = compute_quality("SOLUSDT", bars.iloc[: 24 * 60])
    monthly = compute_quality_windows("SOLUSDT", bars, [minute_count * 60_000])

    assert not daily.qualified
    assert daily.missing_ratio == 27 / (24 * 60)
    assert len(monthly) == 1
    assert monthly.loc[0, "missing_count"] == 27
    assert monthly.loc[0, "missing_ratio"] == 27 / minute_count
    assert monthly.loc[0, "aggtrades_missing_ratio"] == 0.0
    assert monthly.loc[0, "aggtrades_source_missing_days"] == 1
    assert monthly.loc[0, "whale_threshold_missing_count"] == 3
    assert bool(monthly.loc[0, "qualified"])

    cache_path = tmp_path / "quality" / "windows.parquet"
    write_quality_cache(monthly, cache_path)
    loaded = load_quality_cache(cache_path)
    assert loaded.loc[0, "symbol"] == "SOLUSDT"
    assert loaded.loc[0, "window_end_ms"] == minute_count * 60_000
    assert bool(loaded.loc[0, "qualified"])


def test_aggtrades_missing_ratio_allows_one_percent_but_rejects_more() -> None:
    minute_count = 200
    quote_volume = np.full(minute_count, 100.0)
    missing = np.zeros(minute_count, dtype="int8")
    missing[:2] = 1
    buy_quote = quote_volume / 2
    sell_quote = quote_volume / 2
    buy_quote[:2] = 0.0
    sell_quote[:2] = 0.0
    bars = pd.DataFrame(
        {
            "open_time": np.arange(minute_count, dtype="int64") * 60_000,
            "close": np.full(minute_count, 100.0),
            "quote_volume": quote_volume,
            "buy_quote": buy_quote,
            "sell_quote": sell_quote,
            "kline_missing": np.zeros(minute_count, dtype="int8"),
            "aggtrades_missing": missing,
            "is_filled": missing,
        }
    )
    at_limit = compute_quality("X", bars)
    bars.loc[2, "aggtrades_missing"] = 1
    bars.loc[2, "is_filled"] = 1
    above_limit = compute_quality("X", bars)

    assert at_limit.aggtrades_missing_ratio == 0.01
    assert at_limit.qualified
    assert above_limit.aggtrades_missing_ratio == 0.015
    assert not above_limit.qualified


def test_consistency_error_at_two_percent_remains_qualified() -> None:
    minute_count = 100
    bars = pd.DataFrame(
        {
            "open_time": np.arange(minute_count, dtype="int64") * 60_000,
            "close": np.full(minute_count, 100.0),
            "quote_volume": np.ones(minute_count),
            "buy_quote": np.full(minute_count, 0.49),
            "sell_quote": np.full(minute_count, 0.49),
            "kline_missing": np.zeros(minute_count, dtype="int8"),
            "aggtrades_missing": np.zeros(minute_count, dtype="int8"),
        }
    )

    report = compute_quality("X", bars)

    assert report.consistency_error_median == pytest.approx(0.02, abs=1e-15)
    assert report.qualified
