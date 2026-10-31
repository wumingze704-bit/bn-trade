"""全市场日线下载器脚本测试。

规格：docs/03-数据与选币.md §2.1、§4.2 第一阶段（T1-3a）
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import pandas as pd

from scripts.download_daily import (
    DAILY_COLUMNS,
    _download_one,
    convert_daily,
    month_periods,
    read_symbols,
)
from trade.data.aggregate import KLINE_CSV_COLUMNS


def test_month_periods_single_month() -> None:
    assert month_periods("2024-10", "2024-10") == ["2024-10"]


def test_month_periods_crosses_year_boundary() -> None:
    assert month_periods("2024-11", "2025-02") == [
        "2024-11",
        "2024-12",
        "2025-01",
        "2025-02",
    ]


def test_read_symbols_ignores_blank_and_comment() -> None:
    path = Path("symbols.txt")
    try:
        path.write_text("# 全市场 USDT 永续\nBTCUSDT\n\nETHUSDT\n", encoding="utf-8")
        assert read_symbols(path) == ["BTCUSDT", "ETHUSDT"]
    finally:
        path.unlink(missing_ok=True)


def test_convert_daily_writes_selected_columns(tmp_path: Path) -> None:
    payload = ",".join(KLINE_CSV_COLUMNS) + "\n"
    payload += "1720000000000,100,102,99,101,10,1720086399999,1010,100,5,505,0\n"
    zip_path = tmp_path / "BTCUSDT-1d-2024-01.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("BTCUSDT-1d-2024-01.csv", payload)

    output = tmp_path / "daily" / "BTCUSDT" / "2024-01.parquet"
    convert_daily(zip_path, output)

    frame = pd.read_parquet(output)
    assert list(frame.columns) == list(DAILY_COLUMNS)
    assert frame["open_time"].tolist() == [1_720_000_000_000]
    assert frame["close"].tolist() == [101.0]
    assert frame["quote_volume"].tolist() == [1010.0]


def test_download_one_skips_missing_month_404(tmp_path: Path) -> None:
    """该 symbol/月无数据（.CHECKSUM 返回 404）时跳过，不生成 parquet。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"x")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    _download_one(client, "SOLUSDT", "2024-01", tmp_path, tmp_path)

    assert calls["n"] == 1  # 只探测了 .CHECKSUM，未下载
    assert list(tmp_path.rglob("*.parquet")) == []
    assert list(tmp_path.rglob("*.zip")) == []
