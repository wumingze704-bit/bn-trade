"""aggTrades 流式聚合与缺失分钟处理测试。

规格：docs/03-数据与选币.md §2.2
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from trade.data.aggregate import (
    AGGTRADES_COLUMNS,
    MINUTE_ATOMIC_COLUMNS,
    aggregate_aggtrades,
    coerce_aggtrades,
    daily_whale_thresholds,
    fill_missing_minutes,
    has_header,
    normalize_timestamp_ms,
    read_aggtrades_csv,
)


def _trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "agg_trade_id": [1, 2, 3, 4, 5],
            "price": [100.0, 101.0, 102.0, 103.0, 104.0],
            "quantity": [1.0, 2.0, 1.0, 1.0, 1.0],
            "first_trade_id": [1, 2, 3, 4, 5],
            "last_trade_id": [1, 2, 3, 4, 5],
            "transact_time": [500, 1500, 30000, 60500, 64000],
            "is_buyer_maker": [False, True, False, False, True],
        }
    )


def test_coerce_aggtrades_converts_string_bool() -> None:
    df = _trades().copy()
    df["is_buyer_maker"] = ["false", "true", "false", "false", "true"]
    out = coerce_aggtrades(df)
    assert out["is_buyer_maker"].dtype == bool
    assert not bool(out["is_buyer_maker"].iloc[0])
    assert bool(out["is_buyer_maker"].iloc[1])


def test_aggregate_aggtrades_hand_calculated() -> None:
    result = aggregate_aggtrades([_trades()], whale_thr=500.0)
    assert list(result.columns) == list(MINUTE_ATOMIC_COLUMNS)
    assert result["open_time"].tolist() == [0, 60000]

    minute0 = result.iloc[0]
    assert minute0["agg_n"] == 3
    assert minute0["buy_quote"] == 202.0  # 主动买：is_buyer_maker == False
    assert minute0["sell_quote"] == 202.0
    assert minute0["max_trade_quote"] == 202.0
    assert minute0["max_buy_1s_quote"] == 102.0
    assert minute0["whale_buy_quote"] == 0.0
    assert minute0["whale_sell_quote"] == 0.0
    assert minute0["p_1s"] == 100.0
    assert minute0["p_1s_filled"] == 0
    assert minute0["p_3s"] == 101.0
    assert minute0["hi_post_1s"] == 102.0
    assert minute0["lo_post_1s"] == 101.0
    assert minute0["hi_post_3s"] == 102.0
    assert minute0["lo_post_3s"] == 102.0

    minute1 = result.iloc[1]
    assert minute1["agg_n"] == 2
    assert minute1["buy_quote"] == 103.0
    assert minute1["sell_quote"] == 104.0
    assert minute1["p_1s"] == 103.0
    assert minute1["hi_post_1s"] == 104.0


def test_aggregate_cross_chunk_matches_one_shot() -> None:
    full = _trades()
    chunk1 = full.iloc[:2]
    chunk2 = full.iloc[2:]
    one_shot = aggregate_aggtrades([full], whale_thr=500.0)
    chunked = aggregate_aggtrades([chunk1, chunk2], whale_thr=500.0)
    pd.testing.assert_frame_equal(one_shot, chunked)


def test_aggregate_is_buyer_maker_direction() -> None:
    # 03 §1.3：is_buyer_maker=true 是主动卖出，false 是主动买入
    df = pd.DataFrame(
        {
            "agg_trade_id": [1, 2],
            "price": [10.0, 10.0],
            "quantity": [1.0, 2.0],
            "first_trade_id": [1, 2],
            "last_trade_id": [1, 2],
            "transact_time": [1000, 2000],
            "is_buyer_maker": [False, True],
        }
    )
    result = aggregate_aggtrades([df], whale_thr=1000.0)
    row = result.iloc[0]
    assert row["buy_quote"] == 10.0
    assert row["sell_quote"] == 20.0


def test_fill_missing_minutes() -> None:
    bars = pd.DataFrame(
        {
            "open_time": [0, 120000],
            "open": [10.0, 12.0],
            "high": [11.0, 13.0],
            "low": [9.0, 11.0],
            "close": [10.5, 12.5],
            "volume": [100.0, 200.0],
            "is_filled": [0, 0],
        }
    )
    filled = fill_missing_minutes(bars, start_ms=0, end_ms=180000, prev_close=9.5)
    assert filled["open_time"].tolist() == [0, 60000, 120000]
    missing = filled.iloc[1]
    assert missing["is_filled"] == 1
    assert missing["close"] == 10.5  # 上一分钟收盘价
    assert missing["volume"] == 0.0


def test_daily_whale_thresholds() -> None:
    trades = _trades()
    result = daily_whale_thresholds(trades)
    assert result["n_trades"].iloc[0] == 5
    assert result["p99_quote"].iloc[0] == pytest.approx(198.08)


def test_normalize_timestamp_ms_handles_microseconds() -> None:
    series = pd.Series([1_720_000_000_000_000])  # 微秒，> 1e14
    result = normalize_timestamp_ms(series)
    assert result.iloc[0] == 1_720_000_000_000


def test_normalize_timestamp_ms_keeps_milliseconds() -> None:
    series = pd.Series([1_720_000_000_000])  # 毫秒，< 1e14
    assert normalize_timestamp_ms(series).iloc[0] == 1_720_000_000_000


def test_has_header() -> None:
    assert has_header("agg_trade_id,price,quantity")
    assert not has_header("1,100,1,1,1,500,false")


def test_read_aggtrades_csv_reads_header_fixture() -> None:
    path = Path(__file__).resolve().parent.parent / "fixtures" / "aggtrades_sample.csv"
    chunks = list(read_aggtrades_csv(path))
    assert len(chunks) == 1
    frame = chunks[0]
    assert frame["transact_time"].tolist() == [500, 1500, 30000, 60500, 64000]
    assert not bool(frame["is_buyer_maker"].iloc[0])
    assert bool(frame["is_buyer_maker"].iloc[1])


def test_read_aggtrades_csv_no_header(tmp_path: Path) -> None:
    path = tmp_path / "noheader.csv"
    path.write_text("1,100,1,1,1,500,false\n2,101,2,2,2,1500,true\n", encoding="utf-8")
    chunks = list(read_aggtrades_csv(path))
    frame = chunks[0]
    assert list(frame.columns) == list(AGGTRADES_COLUMNS)
    assert frame["transact_time"].tolist() == [500, 1500]
