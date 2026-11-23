"""`scripts/fetch_history.py` 的解析与索引测试（离线 fixture，不访问网络）。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from scripts.fetch_history import (
    build_file_index,
    month_periods,
    parse_funding_payload,
    parse_klines_payload,
    research_end_month,
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
