"""T1-9 分阶段下载+聚合编排（服务器运行）。

规格：docs/03-数据与选币.md §2.1、§2.3
按 调参期 → 测试段 → 封存集 顺序处理 (symbol, month)；断点续传（bars 已存在则跳过）。
每个「币·月」任务使用独立的 raw 工作区（含前一月 1m K 线与首日前一日 aggTrades），
聚合后整体删除，任务输出只依赖自身输入（T1-9 可重复性）。
单任务失败不中断整批，批末对失败清单重试一轮（03 §2.1）。
"""

from __future__ import annotations

import argparse
import calendar
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
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


def _previous_month(month: str) -> str:
    """返回上一个自然月（`YYYY-MM`）。"""
    year, month_num = (int(part) for part in month.split("-"))
    month_num -= 1
    if month_num == 0:
        year, month_num = year - 1, 12
    return f"{year:04d}-{month_num:02d}"


def _previous_day(month: str) -> str:
    """返回本月首日的前一天（上一个自然月最后一日，`YYYY-MM-DD`）。"""
    first_day = datetime.strptime(month, "%Y-%m").date()
    return (first_day - timedelta(days=1)).isoformat()


def _download_tasks(symbol: str, alias: str, month: str, workspace: Path) -> list[tuple[str, Path]]:
    """列出某币某月需要下载的文件（03 §2.1）。

    除本月 klines/fundingRate/aggTrades/metrics 外，还显式包含两个前序文件：
    上一个自然月的 1m K 线（月首缺失分钟与首分钟 `p_1s` 的上一收盘价）与本月首日
    前一天的 aggTrades（`whale_thr(D) = p99(D-1)`）。显式下载使单个「币·月」任务的
    输出只依赖自身输入，不受并发任务是否已下载、是否已删除其他月份归档的影响。

    Args:
        symbol: 合约 symbol。
        alias: 币种别名，用于工作区内的 raw 目录。
        month: 目标月 `YYYY-MM`。
        workspace: 本任务的 raw 工作区（任务结束后整体删除）。

    Returns:
        `(url, 目标路径)` 列表。
    """
    tasks: list[tuple[str, Path]] = []
    previous_month_url = build_vision_url(symbol, "klines", _previous_month(month))
    tasks.append(
        (
            previous_month_url,
            workspace / "klines" / alias / Path(previous_month_url).name,
        )
    )
    for data_type in _MONTH_TYPES:
        url = build_vision_url(symbol, data_type, month)
        tasks.append((url, workspace / data_type / alias / Path(url).name))
    previous_day_url = build_vision_url(symbol, "aggtrades", _previous_day(month))
    tasks.append(
        (
            previous_day_url,
            workspace / "aggtrades" / alias / Path(previous_day_url).name,
        )
    )
    for data_type in _DAILY_TYPES:
        for day in _day_periods(month):
            url = build_vision_url(symbol, data_type, day)
            tasks.append((url, workspace / data_type / alias / Path(url).name))
    return tasks


def _download_month(
    client: httpx.Client, symbol: str, alias: str, month: str, workspace: Path
) -> None:
    """下载某币某月到任务工作区（按月/按日归档），404 跳过。"""
    for url, dest in _download_tasks(symbol, alias, month, workspace):
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
    workspace: Path,
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
        str(workspace),
        "--bars-dir",
        str(bars_root),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"聚合失败 {symbol} {month}: {result.stderr.strip()[-800:]}")


def _run_batch(
    jobs: list[tuple[str, str, str, str]],
    process: Callable[[str, str, str], None],
    workers: int,
) -> list[tuple[str, str, str, str]]:
    """并发执行任务，返回失败清单（单个失败不中断整批，03 §2.1）。

    Args:
        jobs: `(symbol, alias, month, phase)` 任务列表。
        process: 单任务处理函数，接收 `(symbol, alias, month)`。
        workers: 并发线程数，至少 1。

    Returns:
        失败任务列表，供批末重试与报告；全部成功时为空列表。
    """
    if not jobs:
        return []
    failures: list[tuple[str, str, str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(process, job[0], job[1], job[2]): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                future.result()
            except Exception as error:  # noqa: BLE001 - 编排入口需汇总失败后继续
                failures.append(job)
                print(f"失败：{job[0]} {job[2]}（{error}）", file=sys.stderr)
    return failures


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
    # 每个「币·月」任务使用独立的 raw 工作区：只由本任务下载的输入参与聚合，
    # 不受同币其他月份任务的下载/删除时序影响（T1-9 可重复性）。任务结束后删除。
    workspace = raw_root / "work" / f"{alias}-{month}"
    try:
        _download_month(client, symbol, alias, month, workspace)
        _aggregate_month(symbol, month, repo_root, python, bars_root, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


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

    failed_jobs: list[tuple[str, str, str, str]] = []
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:

        def process(symbol: str, alias: str, month: str) -> None:
            _process_one(client, symbol, alias, month, raw_root, bars_root, repo_root, python)

        failed_jobs = _run_batch(pending, process, args.workers)
        if failed_jobs:
            # 03 §2.1：失败清单在批次结束时重试一轮，仍失败的写入报告（脚本输出）。
            print(f"批末重试：{len(failed_jobs)} 个失败任务", file=sys.stderr)
            failed_jobs = _run_batch(failed_jobs, process, args.workers)

    succeeded = len(pending) - len(failed_jobs)
    print(f"阶段 {args.phase} 完成：成功 {succeeded} 个，失败 {len(failed_jobs)} 个")
    for symbol, _alias, month, _phase in failed_jobs:
        print(f"仍失败：{symbol} {month}", file=sys.stderr)
    return 1 if failed_jobs else 0


if __name__ == "__main__":
    raise SystemExit(main())
