"""全市场日线（1d）K 线下载器（服务器运行，T1-3a）。

规格：docs/03-数据与选币.md §2.1、§4.2 第一阶段
用途：下载全市场（含已下架）USDⓈ-M USDT 永续的日线 K 线，转为
`data/daily/{symbol}/{YYYY-MM}.parquet`，供 `scripts/select_coins.py --build`
做规则化月度选币排名。仅在东京服务器执行真实下载；本机只跑 fixture/本地数据。
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

from trade.data.aggregate import read_klines_zip
from trade.data.downloader import build_vision_url, checksum_url, download_with_checksum

# 选币排名只用这三列（03 §4.6、§4.2 第 3/4/5 步）
DAILY_COLUMNS = ("open_time", "close", "quote_volume")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def month_periods(start_month: str, end_month: str) -> list[str]:
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


def read_symbols(path: Path) -> list[str]:
    """从文本文件读取 symbol 清单（每行一个，忽略空行与 `#` 注释）。"""
    symbols: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line)
    return symbols


def convert_daily(zip_path: Path, output_path: Path) -> None:
    """把某月 1d K 线 zip 原子写入 `data/daily/{symbol}/{YYYY-MM}.parquet`。"""
    frame = read_klines_zip(zip_path)[list(DAILY_COLUMNS)].copy()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        frame.to_parquet(temporary_path, index=False, compression="zstd")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _download_one(
    client: httpx.Client, symbol: str, month: str, tmp_root: Path, out_root: Path
) -> None:
    """下载并转换单个 (symbol, month) 日线任务；该月无数据（404）时跳过。"""
    url = build_vision_url(symbol, "daily", month)
    # 先探测 .CHECKSUM 是否存在：404 表示该 symbol/月无数据（未上线或已删除）
    probe = client.get(checksum_url(url), follow_redirects=True)
    if probe.status_code == 404:
        return
    probe.raise_for_status()
    zip_dest = tmp_root / symbol / f"{symbol}-1d-{month}.zip"
    download_with_checksum(client, url, zip_dest, checksum_url(url))
    try:
        convert_daily(zip_dest, out_root / symbol / f"{month}.parquet")
    finally:
        zip_dest.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """download_daily.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="下载全市场 1d 日线 K 线并转为 parquet")
    parser.add_argument("--symbols", required=True, help="symbol 清单文件（每行一个，含已下架）")
    parser.add_argument("--start", required=True, help="起始月 YYYY-MM")
    parser.add_argument("--end", required=True, help="结束月 YYYY-MM")
    parser.add_argument("--out", default="data/daily", help="日线 parquet 输出目录")
    parser.add_argument("--tmp", default="data/raw/daily", help="临时 zip 目录")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    symbols = read_symbols(_resolve(root, args.symbols))
    months = month_periods(args.start, args.end)
    out_root = _resolve(root, args.out)
    tmp_root = _resolve(root, args.tmp)

    jobs = [
        (symbol, month)
        for symbol in symbols
        for month in months
        if not (out_root / symbol / f"{month}.parquet").is_file()
    ]
    print(f"待下载 {len(jobs)} 个 (币, 月) 任务")

    failed = 0
    with (
        httpx.Client(timeout=60.0, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool,
    ):
        futures = {
            pool.submit(_download_one, client, symbol, month, tmp_root, out_root): (symbol, month)
            for symbol, month in jobs
        }
        for future in as_completed(futures):
            symbol, month = futures[future]
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - 编排入口需汇总所有失败
                failed += 1
                print(f"失败：{symbol} {month}（{error}）", file=sys.stderr)
    print(f"完成，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
