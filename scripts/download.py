"""历史数据下载编排入口（服务器运行）。

规格：docs/03-数据与选币.md §2.1；docs/09-币安数据与API.md §3.4
"""

from __future__ import annotations

import argparse
import calendar
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

from trade.data.downloader import build_vision_url, checksum_url, download_with_checksum
from trade.data.universe import load_coins

DATA_TYPES = ("klines", "aggtrades", "fundingrate", "metrics")
# 日度数据类型：按 YYYY-MM-DD 逐日下载；其余按月
_DAILY_TYPES = frozenset({"aggtrades", "metrics"})


def _month_periods(start_month: str, end_month: str) -> list[str]:
    """返回 [start_month, end_month] 闭区间的 YYYY-MM 列表。"""
    start = datetime.strptime(start_month, "%Y-%m")
    end = datetime.strptime(end_month, "%Y-%m")
    periods: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return periods


def _day_periods(month: str) -> list[str]:
    """返回某月全部 YYYY-MM-DD。"""
    year, month = (int(part) for part in month.split("-"))
    days = calendar.monthrange(year, month)[1]
    return [f"{year:04d}-{month:02d}-{day:02d}" for day in range(1, days + 1)]


def _iter_tasks(
    coins: object, types: list[str], start: str, end: str
) -> list[tuple[str, str, Path]]:
    """生成 (url, checksum_url, dest) 下载任务列表。"""
    entries = [*coins.train, *coins.holdout, *coins.blind]
    tasks: list[tuple[str, str, Path]] = []
    for entry in entries:
        for data_type in types:
            if data_type in _DAILY_TYPES:
                periods = [
                    day for month in _month_periods(start, end) for day in _day_periods(month)
                ]
            else:
                periods = _month_periods(start, end)
            for period in periods:
                url = build_vision_url(entry.symbol, data_type, period)
                tasks.append(
                    (url, checksum_url(url), Path(data_type) / entry.alias / Path(url).name)
                )
    return tasks


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    """download.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="下载 data.binance.vision 历史数据")
    parser.add_argument("--coins", default="configs/coins.yaml")
    parser.add_argument("--types", nargs="+", choices=DATA_TYPES, default=["klines"])
    parser.add_argument("--start", required=True, help="起始月 YYYY-MM")
    parser.add_argument("--end", required=True, help="结束月 YYYY-MM")
    parser.add_argument("--out", default="data/raw")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    coins = load_coins(_resolve(root, args.coins))
    out_root = _resolve(root, args.out)
    tasks = _iter_tasks(coins, list(args.types), args.start, args.end)
    print(f"预计下载 {len(tasks)} 个文件")

    failed = 0
    with (
        httpx.Client(timeout=60.0, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool,
    ):
        futures = {
            pool.submit(download_with_checksum, client, url, out_root / dest, cksum): dest
            for url, cksum, dest in tasks
        }
        for future in as_completed(futures):
            dest = futures[future]
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - 编排入口需汇总所有失败
                failed += 1
                print(f"下载失败：{dest}（{error}）", file=sys.stderr)
    print(f"下载完成，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
