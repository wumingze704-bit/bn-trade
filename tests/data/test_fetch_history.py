"""`scripts/fetch_history.py` 的解析与索引测试（离线 fixture，不访问网络）。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from scripts.fetch_history import (
    build_file_index,
    download_url,
    month_periods,
    parse_flow_payload,
    parse_funding_payload,
    parse_klines_payload,
    parse_ohlc_payload,
    research_end_month,
    verify_flow_open_time,
)

_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore"
)


def _zip_bytes(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def test_有表头毫秒K线解析为研究列() -> None:
    """有表头、毫秒时间戳：保留 8 列，open_time 为 int64 毫秒。"""
    csv = "\n".join(
        [
            _HEADER,
            "1700000000000,100,110,90,105,1,1700003599999,1000,5,0.5,500,0",
            "1700003600000,105,115,95,110,2,1700007199999,2000,6,1.0,1000,0",
        ]
    )
    frame = parse_klines_payload(_zip_bytes("BTCUSDT-1h-2023-11.csv", csv), "fixture")
    assert list(frame.columns) == [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "count",
    ]
    assert frame["open_time"].tolist() == [1700000000000, 1700003600000]
    assert frame["open_time"].dtype == "int64"


def test_无表头微秒K线解析并归一毫秒() -> None:
    """无表头、微秒时间戳：按 12 列命名并换算为毫秒。"""
    csv = "\n".join(
        [
            "1700000000000000,100,110,90,105,1,1700003599999000,1000,5,0.5,500,0",
            "1700003600000000,105,115,95,110,2,1700007199999000,2000,6,1.0,1000,0",
        ]
    )
    frame = parse_klines_payload(_zip_bytes("ETHUSDT-1h-2023-11.csv", csv), "fixture")
    assert frame["open_time"].tolist() == [1700000000000, 1700003600000]


def test_资金费旧格式按结算时刻推断间隔() -> None:
    """旧文件没有 funding_interval_hours：用相邻结算差推断，并换算每小时费率。"""
    csv = "\n".join(
        [
            "1672531200000,0.0001",
            "1672560000000,0.0002",
            "1672588800000,-0.0001",
        ]
    )
    frame = parse_funding_payload(_zip_bytes("BTCUSDT-fundingRate-2023-01.csv", csv), "fixture")
    assert frame["interval_hours"].tolist() == pytest.approx([8.0, 8.0, 8.0])
    assert frame["hourly_rate"].tolist() == pytest.approx([0.0001 / 8, 0.0002 / 8, -0.0001 / 8])


def test_资金费带间隔列直接换算() -> None:
    """带 funding_interval_hours（1 小时结算）时不再推断。"""
    csv = "\n".join(
        [
            "calc_time,funding_interval_hours,last_funding_rate",
            "1700000000000,1,0.0003",
            "1700003600000,1,0.0005",
        ]
    )
    frame = parse_funding_payload(_zip_bytes("XUSDT-fundingRate-2023-11.csv", csv), "fixture")
    assert frame["interval_hours"].tolist() == pytest.approx([1.0, 1.0])
    assert frame["hourly_rate"].tolist() == pytest.approx([0.0003, 0.0005])


def test_月份区间与研究结束月() -> None:
    assert month_periods("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]
    ledger = Path(__file__).resolve().parents[2] / "docs" / "research" / "ledger.yaml"
    assert research_end_month(ledger) == "2026-07"
    with pytest.raises(ValueError):
        month_periods("2025-02", "2025-01")


def test_文件索引按类型与币种归集() -> None:
    keys = [
        "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip",
        "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-02.zip",
        "data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip",
        "data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip",
        "data/futures/um/monthly/klines/ETHUSDT/1h/ETHUSDT-1h-2024-01.zip",
    ]
    index = build_file_index(keys)
    assert index["1h"]["BTCUSDT"] == ["2024-01", "2024-02"]
    assert index["1d"]["BTCUSDT"] == ["2024-01"]
    assert index["funding"]["BTCUSDT"] == ["2024-01"]
    assert index["1h"]["ETHUSDT"] == ["2024-01"]


def test_K线列数不符时抛出带上下文异常() -> None:
    csv = "\n".join(["1,2,3", "4,5,6"])
    with pytest.raises(ValueError, match="列数不符"):
        parse_klines_payload(_zip_bytes("BAD-1h-2024-01.csv", csv), "BAD-1h-2024-01")


def test_数据框列顺序不影响结果() -> None:
    """有表头时按列名取值，不依赖列顺序。"""
    csv = "\n".join(
        [
            "close,open_time,open,high,low,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore",
            "105,1700000000000,100,110,90,1,1700003599999,1000,5,0.5,500,0",
        ]
    )
    frame = parse_klines_payload(_zip_bytes("BTCUSDT-1h-2023-11.csv", csv), "fixture")
    assert frame.loc[0, "close"] == pytest.approx(105.0)
    assert isinstance(frame, pd.DataFrame)


def test_文件索引识别三类补充数据类型() -> None:
    """K 线 1h 文件同时供 1h 与 flow 使用；溢价指数与标记价格各自独立。"""
    keys = [
        "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip",
        "data/futures/um/monthly/premiumIndexKlines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip",
        "data/futures/um/monthly/markPriceKlines/ETHUSDT/1h/ETHUSDT-1h-2024-02.zip",
    ]
    index = build_file_index(keys)
    assert index["1h"]["BTCUSDT"] == ["2024-01"]
    assert index["flow"]["BTCUSDT"] == ["2024-01"]
    assert index["premium"]["BTCUSDT"] == ["2024-01"]
    assert index["mark"]["ETHUSDT"] == ["2024-02"]
    assert "ETHUSDT" not in index["premium"]


def test_下载地址区分三类补充数据() -> None:
    """溢价指数与标记价格目录不同，文件名仍是 `{symbol}-1h-{month}.zip`。"""
    assert download_url("flow", "BTCUSDT", "2024-01").endswith(
        "klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    )
    assert download_url("premium", "BTCUSDT", "2024-01").endswith(
        "premiumIndexKlines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    )
    assert download_url("mark", "BTCUSDT", "2024-01").endswith(
        "markPriceKlines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    )


def test_主动买入成交只保留三列() -> None:
    """flow 只保留 open_time、taker_buy_volume、taker_buy_quote_volume。"""
    csv = "\n".join(
        [
            _HEADER,
            "1700000000000,100,110,90,105,1,1700003599999,1000,5,0.5,500,0",
        ]
    )
    frame = parse_flow_payload(_zip_bytes("BTCUSDT-1h-2023-11.csv", csv), "fixture")
    assert list(frame.columns) == ["open_time", "taker_buy_volume", "taker_buy_quote_volume"]
    assert frame.loc[0, "open_time"] == 1700000000000
    assert frame.loc[0, "taker_buy_volume"] == pytest.approx(0.5)
    assert frame.loc[0, "taker_buy_quote_volume"] == pytest.approx(500.0)


def test_溢价指数只保留OHLC() -> None:
    """premium / mark 只保留 open_time 与 OHLC。"""
    csv = "\n".join(
        [
            _HEADER,
            "1700000000000,0.001,0.002,0.0,0.0015,0,1700003599999,0,0,0,0,0",
        ]
    )
    frame = parse_ohlc_payload(_zip_bytes("BTCUSDT-1h-2023-11.csv", csv), "fixture")
    assert list(frame.columns) == ["open_time", "open", "high", "low", "close"]
    assert frame.loc[0, "close"] == pytest.approx(0.0015)


def test_主动买入成交open_time必须与1hK线逐行一致(tmp_path) -> None:
    """参照文件缺失或不一致时抛出带上下文的异常（docs/13 §1）。"""
    csv = "\n".join(
        [
            _HEADER,
            "1700000000000,100,110,90,105,1,1700003599999,1000,5,0.5,500,0",
            "1700003600000,105,115,95,110,2,1700007199999,2000,6,1.0,1000,0",
        ]
    )
    frame = parse_flow_payload(_zip_bytes("BTCUSDT-1h-2023-11.csv", csv), "fixture")
    reference = tmp_path / "BTCUSDT-1h-2023-11.parquet"
    pd.DataFrame({"open_time": [1700000000000, 1700003600000]}).to_parquet(reference, index=False)
    verify_flow_open_time(frame, reference, "fixture")
    with pytest.raises(ValueError, match="缺少 1h K 线参照文件"):
        verify_flow_open_time(frame, tmp_path / "missing.parquet", "fixture")
    pd.DataFrame({"open_time": [1700000000000]}).to_parquet(reference, index=False)
    with pytest.raises(ValueError, match="不一致"):
        verify_flow_open_time(frame, reference, "fixture")
