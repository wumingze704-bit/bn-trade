"""aggTrades 流式聚合与缺失分钟处理测试。

规格：docs/03-数据与选币.md §2.2
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade.data.aggregate import (
    MINUTE_ATOMIC_COLUMNS,
    aggregate_aggtrades,
    aggregate_aggtrades_day,
    aggregate_aggtrades_python,
    assemble_bars,
    coerce_aggtrades,
    daily_whale_thresholds,
    fill_missing_minutes,
    has_header,
    normalize_timestamp_ms,
    read_aggtrades_csv,
    read_aggtrades_zip,
    write_monthly_bars,
)
from trade.data.loader import load_bars


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


def test_numba_matches_python_reference_across_minute_split_chunks() -> None:
    trades = _trades()
    chunks = [trades.iloc[:2], trades.iloc[2:4], trades.iloc[4:]]
    expected = aggregate_aggtrades_python(chunks, whale_thr=150.0, prev_close=99.0)
    actual = aggregate_aggtrades(chunks, whale_thr=150.0, prev_close=99.0)
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=0.0, atol=1e-9)


def test_daily_aggregation_consumes_chunks_once_and_returns_exact_p99() -> None:
    trades = _trades()
    iterator_calls = 0

    def one_pass_chunks():
        nonlocal iterator_calls
        iterator_calls += 1
        if iterator_calls > 1:
            raise AssertionError("aggTrades chunks were iterated more than once")
        yield trades.iloc[:2]
        yield trades.iloc[2:]

    result = aggregate_aggtrades_day(one_pass_chunks(), whale_thr=150.0, prev_close=99.0)
    expected_p99 = float(daily_whale_thresholds(trades)["p99_quote"].iloc[0])
    reference = aggregate_aggtrades_python([trades], whale_thr=150.0, prev_close=99.0)
    pd.testing.assert_frame_equal(result.minutes, reference, check_exact=False, rtol=0.0, atol=1e-9)
    assert result.p99_quote == pytest.approx(expected_p99, abs=1e-9)
    assert result.n_trades == len(trades)
    assert iterator_calls == 1


def test_daily_aggregation_without_previous_p99_does_not_use_current_day() -> None:
    result = aggregate_aggtrades_day([_trades()], whale_thr=float("nan"))

    assert result.p99_quote == pytest.approx(
        float(daily_whale_thresholds(_trades())["p99_quote"].iloc[0]), abs=1e-9
    )
    assert result.minutes["whale_thr"].isna().all()
    assert result.minutes["whale_buy_quote"].sum() == 0.0
    assert result.minutes["whale_sell_quote"].sum() == 0.0


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


def test_assemble_bars_preserves_missing_whale_threshold() -> None:
    kline = pd.DataFrame(
        {
            "open_time": [0],
            "open": [100.0],
            "high": [100.0],
            "low": [100.0],
            "close": [100.0],
            "volume": [1.0],
            "close_time": [59_999],
            "quote_volume": [100.0],
            "count": [1],
            "taker_buy_volume": [0.5],
            "taker_buy_quote_volume": [50.0],
        }
    )
    atomic_row = dict.fromkeys(MINUTE_ATOMIC_COLUMNS, 0)
    atomic_row.update(
        {
            "open_time": 0,
            "agg_n": 1,
            "buy_quote": 50.0,
            "sell_quote": 50.0,
            "whale_thr": float("nan"),
            "p_1s": 100.0,
            "p_3s": 100.0,
            "hi_post_1s": 100.0,
            "lo_post_1s": 100.0,
            "hi_post_3s": 100.0,
            "lo_post_3s": 100.0,
        }
    )

    bars = assemble_bars(
        kline,
        pd.DataFrame([atomic_row], columns=MINUTE_ATOMIC_COLUMNS),
        start_ms=0,
        end_ms=60_000,
        aggtrades_missing_days=[0],
        whale_threshold_missing_days=[0],
    )

    assert pd.isna(bars.loc[0, "whale_thr"])
    assert bars.loc[0, "aggtrades_source_missing"] == 1
    assert bars.loc[0, "whale_threshold_missing"] == 1


def test_assemble_bars_aligns_sources_and_loads_month_file(tmp_path: Path) -> None:
    minute_ms = 60_000
    open_times = [0, minute_ms, *range(3 * minute_ms, 11 * minute_ms, minute_ms)]
    closes = [100.0 + index for index in range(len(open_times))]
    klines = pd.DataFrame(
        {
            "open_time": open_times,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(open_times),
            "close_time": [time + minute_ms - 1 for time in open_times],
            "quote_volume": [100.0] * len(open_times),
            "count": [1] * len(open_times),
            "taker_buy_volume": [0.5] * len(open_times),
            "taker_buy_quote_volume": [50.0] * len(open_times),
            "ignore": [0] * len(open_times),
        }
    )
    atomic_rows = []
    for time, close in zip(open_times[1:], closes[1:], strict=True):
        if time == minute_ms:
            continue
        row = dict.fromkeys(MINUTE_ATOMIC_COLUMNS, 0)
        row.update(
            {
                "open_time": time,
                "agg_n": 1,
                "buy_quote": 50.0,
                "sell_quote": 50.0,
                "whale_thr": 100.0,
                "p_1s": close,
                "p_3s": close,
                "hi_post_1s": close,
                "lo_post_1s": close,
                "hi_post_3s": close,
                "lo_post_3s": close,
            }
        )
        atomic_rows.append(row)
    atomic = pd.DataFrame(atomic_rows, columns=MINUTE_ATOMIC_COLUMNS)
    metrics = pd.DataFrame(
        {
            "create_time": ["1970-01-01 00:00:00", "1970-01-01 00:05:00"],
            "sum_open_interest": [10.0, 20.0],
        }
    )
    funding = pd.DataFrame({"calc_time": [3 * minute_ms], "last_funding_rate": [0.001]})

    bars = assemble_bars(
        klines,
        atomic,
        start_ms=0,
        end_ms=11 * minute_ms,
        metrics=metrics,
        funding=funding,
        previous_close=99.0,
    )

    assert bars.loc[1, "aggtrades_missing"] == 1
    assert bars.loc[1, "is_filled"] == 1
    assert bars.loc[1, "p_1s"] == closes[0]
    assert bars.loc[1, "buy_quote"] == 0.0
    assert bars.loc[2, "kline_missing"] == 1
    assert bars.loc[2, "is_filled"] == 1
    assert bars.loc[2, "close"] == closes[1]
    assert bars.loc[2, "volume"] == 0.0
    assert pd.isna(bars.loc[4, "oi"])
    assert bars.loc[5, "oi"] == 10.0
    assert bars.loc[9, "oi"] == 10.0
    assert bars.loc[10, "oi"] == 20.0
    assert pd.isna(bars.loc[2, "funding_rate"])
    assert bars.loc[3, "funding_rate"] == 0.001
    assert bars.loc[3, "funding_time"] == 3 * minute_ms

    output_path = tmp_path / "bars" / "C01" / "202601.parquet"
    write_monthly_bars(bars, output_path)
    loaded = load_bars(
        tmp_path,
        "C01",
        0,
        11 * minute_ms,
        holdout_start_ms=11 * minute_ms + 1,
    )
    assert len(loaded) == 11
    assert "ignore" not in loaded.columns


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
    assert list(frame.columns) == ["price", "quantity", "transact_time", "is_buyer_maker"]
    assert frame["transact_time"].tolist() == [500, 1500]


def test_read_aggtrades_zip_streams_header_and_normalizes_microseconds(tmp_path: Path) -> None:
    path = tmp_path / "trades.zip"
    payload = (
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
        "1,100,1,1,1,1720000000000000,True\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("trades.csv", payload)

    chunks = list(read_aggtrades_zip(path))

    assert len(chunks) == 1
    assert list(chunks[0].columns) == ["price", "quantity", "transact_time", "is_buyer_maker"]
    assert chunks[0]["transact_time"].tolist() == [1_720_000_000_000]
    assert bool(chunks[0]["is_buyer_maker"].iloc[0])


def test_aggregate_cli_builds_one_coin_three_day_month_file(tmp_path: Path) -> None:
    from scripts.aggregate import main

    raw_root = tmp_path / "raw"
    bars_root = tmp_path / "bars"
    alias = "C01"
    symbol = "SOLUSDT"
    month_start_ms = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    minute_count = 3 * 24 * 60
    open_times = month_start_ms + np.arange(minute_count, dtype="int64") * 60_000
    klines = pd.DataFrame(
        {
            "open_time": open_times,
            "open": np.full(minute_count, 100.0),
            "high": np.full(minute_count, 101.0),
            "low": np.full(minute_count, 99.0),
            "close": np.full(minute_count, 100.0),
            "volume": np.ones(minute_count),
            "close_time": open_times + 59_999,
            "quote_volume": np.full(minute_count, 100.0),
            "count": np.ones(minute_count, dtype="int64"),
            "taker_buy_volume": np.full(minute_count, 0.5),
            "taker_buy_quote_volume": np.full(minute_count, 50.0),
            "ignore": np.zeros(minute_count, dtype="int64"),
        }
    )

    def write_csv_zip(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{path.stem}.csv", frame.to_csv(index=False))

    write_csv_zip(
        raw_root / "klines" / alias / f"{symbol}-1m-2026-01.zip",
        klines,
    )
    write_csv_zip(
        raw_root / "fundingrate" / alias / f"{symbol}-fundingRate-2026-01.zip",
        pd.DataFrame(
            {
                "calc_time": [month_start_ms],
                "funding_interval_hours": [8],
                "last_funding_rate": [0.0001],
            }
        ),
    )
    for day_offset, quote in [(-1, 100.0), (0, 200.0), (1, 300.0), (2, 400.0)]:
        day_start_ms = month_start_ms + day_offset * 86_400_000
        day_text = datetime.fromtimestamp(day_start_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        trade_time_ms = day_start_ms + 500
        trades = pd.DataFrame(
            {
                "agg_trade_id": [1],
                "price": [quote],
                "quantity": [1.0],
                "first_trade_id": [1],
                "last_trade_id": [1],
                "transact_time": [trade_time_ms],
                "is_buyer_maker": [False],
            }
        )
        write_csv_zip(
            raw_root / "aggtrades" / alias / f"{symbol}-aggTrades-{day_text}.zip",
            trades,
        )

    aliases_path = tmp_path / "aliases.json"
    aliases_path.write_text(json.dumps({"SOLUSDT": "C01"}), encoding="utf-8")

    exit_code = main(
        [
            "--aliases",
            str(aliases_path),
            "--symbol",
            symbol,
            "--month",
            "2026-01",
            "--raw-dir",
            str(raw_root),
            "--bars-dir",
            str(bars_root),
            "--keep-raw",
        ]
    )

    output_path = bars_root / alias / "202601.parquet"
    loaded = load_bars(
        bars_root.parent,
        alias,
        month_start_ms,
        month_start_ms + 31 * 86_400_000,
        holdout_start_ms=month_start_ms + 31 * 86_400_000 + 1,
    )
    threshold_cache = pd.read_parquet(bars_root / alias / "whale_thr.parquet")
    assert exit_code == 0
    assert output_path.is_file()
    assert len(loaded) == 31 * 24 * 60
    assert loaded.loc[0, "whale_thr"] == 100.0
    assert loaded.loc[0, "funding_rate"] == 0.0001
    assert threshold_cache.loc[0, "p99_source_date"].isoformat() == "2025-12-31"
    assert threshold_cache["missing_day"].sum() == 28
