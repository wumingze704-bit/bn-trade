"""下载并组装 BTCUSDT 1m K 线为市场环境 bars（G1：BTCUSDT 数据齐全）。

规格：docs/03-数据与选币.md §1 第 15 行（BTCUSDT 1m K 线，市场环境特征）。
BTC 只做市场环境（04 §4.3），无 aggTrades/仓位，原子量列置 NaN、无 metrics/funding。
下载与组装在东京服务器执行（02 §5.2）。
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd

from trade.common.config import BaseConfig, load_config
from trade.data.aggregate import (
    MINUTE_ATOMIC_COLUMNS,
    assemble_bars,
    read_klines_zip,
    write_monthly_bars,
)
from trade.data.downloader import build_vision_url, checksum_url, download_with_checksum
from trade.data.loader import window_bounds


def _month_start_ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _month_bounds_ms(year: int, month: int) -> tuple[int, int]:
    """返回某 UTC 月的 [start_ms, end_ms) 边界。"""
    start = _month_start_ms(year, month)
    ny, nm = _next_month(year, month)
    return start, _month_start_ms(ny, nm)


def _empty_atomic() -> pd.DataFrame:
    """构造空原子量表：BTC 无 aggTrades，各列 dtype 与 assemble_bars 期望一致。"""
    return pd.DataFrame(
        {
            "open_time": pd.Series(dtype="int64"),
            **{
                col: pd.Series(dtype="float64")
                for col in MINUTE_ATOMIC_COLUMNS
                if col != "open_time"
            },
        }
    )


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="下载并组装 BTCUSDT 市场环境 bars")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bars-dir", default="data/bars")
    args = parser.parse_args(argv)

    base = load_config(BaseConfig, _resolve(root, args.base))
    wb = window_bounds(base)
    bars_root = _resolve(root, args.bars_dir)
    raw_root = _resolve(root, args.raw_dir)
    kline_dir = raw_root / "klines" / args.symbol
    out_dir = bars_root / args.symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    # 覆盖预热 + M1~M24：从 start_ms 所在月到 end_ms 前一月
    start_dt = datetime.fromtimestamp(wb.start_ms // 1000, tz=UTC)
    end_dt = datetime.fromtimestamp((wb.end_ms - 1) // 1000, tz=UTC)
    year, month = start_dt.year, start_dt.month
    end_year, end_month = end_dt.year, end_dt.month

    previous_close: float | None = None
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        while (year, month) <= (end_year, end_month):
            period = f"{year:04d}-{month:02d}"
            url = build_vision_url(args.symbol, "klines", period)
            dest = kline_dir / f"{args.symbol}-1m-{period}.zip"
            if not dest.is_file():
                probe = client.get(checksum_url(url), follow_redirects=True)
                if probe.status_code != 404:
                    probe.raise_for_status()
                    download_with_checksum(client, url, dest, checksum_url(url))

            klines = read_klines_zip(dest)
            start_ms, end_ms = _month_bounds_ms(year, month)
            bars = assemble_bars(
                klines,
                _empty_atomic(),
                start_ms=start_ms,
                end_ms=end_ms,
                metrics=None,
                funding=None,
                previous_close=previous_close,
            )
            write_monthly_bars(bars, out_dir / f"{period.replace('-', '')}.parquet")
            previous_close = float(klines.sort_values("open_time")["close"].iloc[-1])
            print(f"BTCUSDT {period} 完成，{len(bars)} 分钟")
            year, month = _next_month(year, month)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
