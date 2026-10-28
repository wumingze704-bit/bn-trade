"""数据质量报告测试。

规格：docs/03-数据与选币.md §2.4
"""

from __future__ import annotations

import pandas as pd

from trade.data.quality import compute_quality


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
