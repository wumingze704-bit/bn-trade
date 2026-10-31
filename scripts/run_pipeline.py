"""T1-9 分阶段下载+聚合编排（服务器运行）。

规格：docs/03-数据与选币.md §2.1、§2.3
按 调参期 → 测试段 → 封存集 顺序处理 (symbol, month)；断点续传（bars 已存在则跳过）。
下载与聚合按月交织，aggTrades 聚合后即删除（流式，不长期保存，03 §3）。
"""

from __future__ import annotations

import argparse
import calendar
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

from trade.common.config import BaseConfig, load_config
from trade.data.downloader import build_vision_url, checksum_url, download_with_checksum
from trade.data.loader import load_universe_snapshot, universe_months

_DAILY_TYPES = frozenset({"aggtrades", "metrics"})
_MONTH_TYPES = ("klines", "fundingrate")


def _month_periods(start: str, end: str) -> list[str]:
    """返回 [start, end] 闭区间 YYYY-MM 列表。"""
    start_dt = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    periods: list[str] = []
    year, month = start_dt.year, start_dt.month
    while (year, month) <= (end_dt.year, end_dt.month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return periods


def _day_periods(month: str) -> list[str]:
    year, month_num = (int(part) for part in month.split("-"))
    days = calendar.monthrange(year, month_num)[1]
    return [f"{year:04d}-{month_num:02d}-{day:02d}" for day in range(1, days + 1)]


def build_jobs(
    data_root: Path,
    months: list[str],
    tuning_months: int,
    holdout_months: int,
    *,
    warmup_months: int = 1,
) -> list[tuple[str, str, str, str]]:
    """从月度快照生成 (symbol, alias, month, phase) 任务清单（03 §2.1）。

    每个币下载范围 = 每段入选期前 `warmup_months` 个月预热 + 入选月（按整月近似 30 天预热）。
    阶段：前 `tuning_months` 个月为 `tuning`，末尾 `holdout_months` 个月为 `holdout`，
    其余为 `test`。
    阶段内按入选月数降序（入选月数多的币优先，03 §2.1）。

    Args:
        data_root: 数据根目录。
        months: M1..M{months} 月列表（升序）。
        tuning_months: 调参期月数。
        holdout_months: 封存集月数（取窗口末尾）。
        warmup_months: 预热月数（默认 1，近似 30 天）。

    Returns:
        (symbol, alias, month, phase) 列表，已按阶段与优先级排序。
    """
    symbol_months: dict[str, set[str]] = defaultdict(set)
    alias_by_symbol: dict[str, str] = {}
    for month in months:
        snapshot = load_universe_snapshot(data_root, month)
        for coin in snapshot.coins:
            symbol_months[coin.symbol].add(month)
            alias_by_symbol[coin.symbol] = coin.alias

    tuning = set(months[:tuning_months])
    holdout = set(months[-holdout_months:])

    def phase_of(month: str) -> str:
        # M1 之前的预热月归入调参期（03 §2.1：调参期及其预热）
        if month in tuning or month < months[0]:
            return "tuning"
        if month in holdout:
            return "holdout"
        return "test"

    jobs: list[tuple[str, str, str, str]] = []
    for symbol, selected in symbol_months.items():
        ordered = sorted(selected)
        first, last = ordered[0], ordered[-1]
        year, month = int(first[:4]), int(first[5:7])
        month -= warmup_months
        while month <= 0:
            year, month = year - 1, month + 12
        start = f"{year:04d}-{month:02d}"
        for m in _month_periods(start, last):
            jobs.append((symbol, alias_by_symbol[symbol], m, phase_of(m)))

    order = {"tuning": 0, "test": 1, "holdout": 2}
    jobs.sort(key=lambda job: (order[job[3]], -len(symbol_months[job[0]]), job[0], job[2]))
    return jobs


def _download_month(
    client: httpx.Client, symbol: str, alias: str, month: str, raw_root: Path
) -> None:
    """下载某币某月的 raw（klines/funding 按月，aggTrades/metrics 按日），404 跳过。"""
    tasks: list[tuple[str, Path]] = []
    for data_type in _MONTH_TYPES:
        url = build_vision_url(symbol, data_type, month)
        tasks.append((url, raw_root / data_type / alias / Path(url).name))
    for data_type in _DAILY_TYPES:
        for day in _day_periods(month):
            url = build_vision_url(symbol, data_type, day)
            tasks.append((url, raw_root / data_type / alias / Path(url).name))

    for url, dest in tasks:
        probe = client.get(checksum_url(url), follow_redirects=True)
        if probe.status_code == 404:
            continue
        probe.raise_for_status()
        download_with_checksum(client, url, dest, checksum_url(url))


def _aggregate_month(
    symbol: str,
    month: str,
    repo_root: Path,
    python: str,
    bars_root: Path,
    raw_root: Path,
) -> None:
    """调用 scripts/aggregate.py 聚合某币某月（复用同一份聚合代码）。"""
    cmd = [
        python,
        str(repo_root / "scripts" / "aggregate.py"),
        "--symbol",
        symbol,
        "--month",
        month,
        "--aliases",
        str(repo_root / "data" / "universe" / "aliases.json"),
        "--raw-dir",
        str(raw_root),
        "--bars-dir",
        str(bars_root),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"聚合失败 {symbol} {month}: {result.stderr.strip()[-800:]}")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _process_one(
    client: httpx.Client,
    symbol: str,
    alias: str,
    month: str,
    raw_root: Path,
    bars_root: Path,
    repo_root: Path,
    python: str,
) -> None:
    _download_month(client, symbol, alias, month, raw_root)
    _aggregate_month(symbol, month, repo_root, python, bars_root, raw_root)
    # 聚合后删除该月 aggTrades raw（流式，03 §2.2/§3），
    # 但保留月末最后一天供下月首日 p99（03 §2.1「每段首日多下载 1 天只算 p99」）。
    month_zips = sorted((raw_root / "aggtrades" / alias).glob(f"{symbol}-aggTrades-{month}-*.zip"))
    for day_zip in month_zips[:-1]:
        day_zip.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="分阶段下载+聚合（T1-9）")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--phase", choices=["tuning", "test", "holdout"], default="tuning")
    parser.add_argument("--out", default="data/bars")
    parser.add_argument("--raw", default="data/raw")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个待处理任务（冒烟用）")
    args = parser.parse_args(argv)

    base = load_config(BaseConfig, _resolve(repo_root, args.base))
    months = universe_months(base)
    bars_root = _resolve(repo_root, args.out)
    raw_root = _resolve(repo_root, args.raw)
    python = sys.executable

    jobs = [
        job
        for job in build_jobs(
            base.paths.data_root,
            months,
            base.data_window.tuning_months,
            base.data_window.holdout_months,
        )
        if job[3] == args.phase
    ]
    pending = []
    for symbol, alias, month, _phase in jobs:
        bars_path = bars_root / alias / f"{month.replace('-', '')}.parquet"
        if not bars_path.is_file():
            pending.append((symbol, alias, month, _phase))
    if args.limit is not None:
        pending = pending[: args.limit]
    print(f"阶段 {args.phase}：共 {len(jobs)} 个任务，待处理 {len(pending)} 个")

    failed = 0
    with (
        httpx.Client(timeout=120.0, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool,
    ):
        futures = {
            pool.submit(
                _process_one,
                client,
                symbol,
                alias,
                month,
                raw_root,
                bars_root,
                repo_root,
                python,
            ): (symbol, month)
            for symbol, alias, month, _phase in pending
        }
        for future in as_completed(futures):
            symbol, month = futures[future]
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - 编排入口需汇总失败
                failed += 1
                print(f"失败：{symbol} {month}（{error}）", file=sys.stderr)
    print(f"阶段 {args.phase} 完成，失败 {failed} 个")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
