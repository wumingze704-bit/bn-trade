"""一次性运维脚本：修复历史 bars 月文件「首日大单阈值缺失」（T1-9 遗留缺陷）。

规格：docs/03-数据与选币.md §2.2（`whale_thr(D) = p99(D-1)`）、§2.3（月文件原子替换）。

缺陷：修复前的流水线在聚合某月时取不到上一自然月最后一天的 aggTrades，导致该月首日的
`whale_thr` 为空、`whale_buy_quote` / `whale_sell_quote` 为 0（特征 #23~#25、#27 失真）。

做法（**复用同一份实现，不另写聚合逻辑**）：只下载「首日前一日」与「首日」两个 aggTrades
归档，用 `aggregate_aggtrades_day()` 重算首日分钟原子量（阈值 = p99(D-1)），把首日以外的
原子量从现有月文件原样取回，再交给 `assemble_bars()` 重组整月并原子替换。脚本会先比较新旧
月文件，首日以外的行有任何变化就拒绝写盘。只处理 M1~M{months-holdout_months}（封存集月份
固定排除，不读不写，AGENTS.md §6 红线 2）。
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from scripts.aggregate import (
    FUNDING_COLUMNS,
    KLINE_COLUMNS,
    METRICS_COLUMNS,
    _exclusive_lock,
    _month_bounds,
    _previous_month_close,
    _read_zip_frame,
)
from trade.common.config import BaseConfig, load_config
from trade.data.aggregate import (
    MINUTE_ATOMIC_COLUMNS,
    aggregate_aggtrades_day,
    assemble_bars,
    read_aggtrades_zip,
    write_monthly_bars,
)
from trade.data.downloader import build_vision_url, checksum_url, download_with_checksum
from trade.data.loader import universe_months
from trade.data.universe import load_aliases

_DAY_MS = 86_400_000


@dataclass(frozen=True)
class RepairTarget:
    """一个待修复的「币·月」。"""

    symbol: str
    alias: str
    month: str
    bars_path: Path


def _needs_repair(bars: pd.DataFrame) -> bool:
    """判断首日是否存在「有成交但大单阈值为空」的分钟。"""
    day_start = int(bars["open_time"].min()) // _DAY_MS * _DAY_MS
    day = bars["open_time"] < day_start + _DAY_MS
    missing_threshold = bars["whale_thr"].isna()
    has_trades = ~bars["aggtrades_missing"].fillna(0).astype(bool) & (
        bars["quote_volume"].fillna(0.0) > 0
    )
    return bool((day & missing_threshold & has_trades).any())


def _find_targets(
    bars_root: Path, aliases: dict[str, str], alias_filter: set[str], month_filter: set[str]
) -> list[RepairTarget]:
    """扫描月文件，返回需要修复的目标列表。"""
    symbol_by_alias = {alias: symbol for symbol, alias in aliases.items()}
    targets: list[RepairTarget] = []
    for path in sorted(bars_root.glob("*/[0-9][0-9][0-9][0-9][0-9][0-9].parquet")):
        alias = path.parent.name
        month = f"{path.name[:4]}-{path.name[4:6]}"
        if alias_filter and alias not in alias_filter:
            continue
        if month_filter and month not in month_filter:
            continue
        if alias not in symbol_by_alias:
            # BTCUSDT 等市场环境目录以 symbol 本身命名、无逐笔归档（03 §4.5），跳过
            continue
        bars = pd.read_parquet(
            path, columns=["open_time", "whale_thr", "aggtrades_missing", "quote_volume"]
        )
        if _needs_repair(bars):
            targets.append(RepairTarget(symbol_by_alias[alias], alias, month, path))
    return targets


def _ensure_previous_month_klines(
    client: httpx.Client, symbol: str, alias: str, month: str, raw_root: Path
) -> None:
    """上一自然月 1m K 线缺失时下载（月首价格回退用，03 §2.1）。"""
    year, month_num = (int(part) for part in month.split("-"))
    month_num -= 1
    if month_num == 0:
        year, month_num = year - 1, 12
    previous = f"{year:04d}-{month_num:02d}"
    url = build_vision_url(symbol, "klines", previous)
    dest = raw_root / "klines" / alias / Path(url).name
    if dest.is_file():
        return
    response = client.get(checksum_url(url), follow_redirects=True)
    if response.status_code == 404:
        return
    response.raise_for_status()
    download_with_checksum(client, url, dest, checksum_url(url))


def _download_day(client: httpx.Client, symbol: str, day: str, workspace: Path) -> Path | None:
    """下载某日 aggTrades 到工作区；归档不存在时返回 None。"""
    url = build_vision_url(symbol, "aggtrades", day)
    dest = workspace / Path(url).name
    response = client.get(checksum_url(url), follow_redirects=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    download_with_checksum(client, url, dest, checksum_url(url))
    return dest


def _atomic_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """从现有月文件取回原子量；原先标记为逐笔缺失的分钟恢复为 NaN。

    只有在确有缺失分钟时才写 NaN，避免把整列 int 无谓地升级为 float（会改变输出字节）。
    """
    atomic = bars[list(MINUTE_ATOMIC_COLUMNS)].copy()
    missing = bars["aggtrades_missing"].fillna(0).astype(bool).to_numpy()
    if missing.any():
        value_columns = [column for column in MINUTE_ATOMIC_COLUMNS if column != "open_time"]
        atomic.loc[missing, value_columns] = np.nan
    return atomic


def _day_starts_flagged(bars: pd.DataFrame, flag_column: str) -> list[int]:
    """取某整日标志列对应的 UTC 日起点毫秒列表。"""
    flagged = bars.loc[bars[flag_column].fillna(0).astype(bool), "open_time"].to_numpy()
    return [int(value) for value in np.unique(flagged // _DAY_MS * _DAY_MS)]


def _update_threshold_audit(
    bars_root: Path,
    alias: str,
    day: date,
    *,
    p99_quote: float,
    n_trades: int,
    whale_thr: float,
    source_date: date,
    lock_path: Path,
) -> None:
    """更新 `whale_thr.parquet` 中某日审计行（与聚合脚本同一把锁）。"""
    threshold_path = bars_root / alias / "whale_thr.parquet"
    if not threshold_path.is_file():
        return
    with _exclusive_lock(lock_path):
        frame = pd.read_parquet(threshold_path)
        mask = pd.to_datetime(frame["date"]).dt.date == day
        if not bool(mask.any()):
            return
        frame.loc[mask, "p99_quote"] = p99_quote
        frame.loc[mask, "n_trades"] = n_trades
        frame.loc[mask, "whale_thr"] = whale_thr
        frame.loc[mask, "p99_source_date"] = source_date
        frame.loc[mask, "missing_day"] = 0
        frame = frame.sort_values("date")
        temporary = threshold_path.with_name(threshold_path.name + ".tmp")
        frame.to_parquet(temporary, index=False, compression="zstd")
        temporary.replace(threshold_path)


def _later_column_differences(
    repaired: pd.DataFrame, existing: pd.DataFrame, day1_mask: pd.Series
) -> list[str]:
    """列出首日以外发生变化的列（含类型变化），用于写入前的安全检查。"""
    later = repaired.loc[~day1_mask].reset_index(drop=True)
    old = existing.loc[~day1_mask].reset_index(drop=True)
    differences: list[str] = []
    for column in sorted(set(later.columns) | set(old.columns)):
        if column not in later or column not in old:
            differences.append(f"{column}:缺失列")
            continue
        if later[column].dtype != old[column].dtype:
            differences.append(f"{column}:类型{old[column].dtype}->{later[column].dtype}")
            continue
        if not later[column].equals(old[column]):
            left = later[column]
            right = old[column]
            differing = np.flatnonzero(~((left.isna() & right.isna()) | (left == right)))
            samples = [
                f"i={int(idx)} new={left.iloc[int(idx)]!r} old={right.iloc[int(idx)]!r}"
                for idx in differing[:2]
            ]
            differences.append(f"{column}:值不同({len(differing)}行 {samples})")
    return differences


def repair_one(
    client: httpx.Client,
    target: RepairTarget,
    bars_root: Path,
    raw_root: Path,
    *,
    dry_run: bool,
) -> dict[str, object]:
    """修复一个「币·月」。

    Args:
        client: httpx 客户端。
        target: 待修复目标。
        bars_root: `data/bars` 根目录。
        raw_root: `data/raw` 根目录（本脚本用其下的 klines/metrics/fundingrate 与工作区）。
        dry_run: 为 True 时只计算并比较，不写盘。

    Returns:
        含 `symbol`、`month`、`p99_prev`、`n_trades_day1`、`written` 的统计字典。

    Raises:
        RuntimeError: 缺少必需归档，或首日以外的行发生变化（复现逻辑不成立）。
    """
    alias, month = target.alias, target.month
    month_start_ms, month_end_ms = _month_bounds(month)
    day = datetime.fromtimestamp(month_start_ms / 1000, tz=UTC).date()
    day1, previous_day = day.isoformat(), (day - timedelta(days=1)).isoformat()

    _ensure_previous_month_klines(client, target.symbol, alias, month, raw_root)
    workspace = raw_root / "work" / f"repair-{alias}-{month}"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        previous_zip = _download_day(client, target.symbol, previous_day, workspace)
        day1_zip = _download_day(client, target.symbol, day1, workspace)
        if previous_zip is None or day1_zip is None:
            raise RuntimeError(f"缺少归档：{target.symbol} {previous_day}/{day1}")

        previous_result = aggregate_aggtrades_day(
            read_aggtrades_zip(previous_zip), whale_thr=float("nan")
        )
        if not previous_result.n_trades:
            raise RuntimeError(f"前一日无成交，取不到 p99：{target.symbol} {previous_day}")

        existing = pd.read_parquet(target.bars_path)
        kline_dir = raw_root / "klines" / alias
        kline_path = kline_dir / f"{target.symbol}-1m-{month}.zip"
        if not kline_path.is_file():
            raise RuntimeError(f"缺少月度 K 线：{kline_path}")
        klines = _read_zip_frame(kline_path, KLINE_COLUMNS)
        previous_close = _previous_month_close(kline_dir, target.symbol, month)
        metrics_dir = raw_root / "metrics" / alias
        metrics_paths = sorted(metrics_dir.glob(f"{target.symbol}-metrics-{month}-*.zip"))
        metrics = (
            pd.concat([_read_zip_frame(path, METRICS_COLUMNS) for path in metrics_paths])
            if metrics_paths
            else None
        )
        funding_path = raw_root / "fundingrate" / alias / f"{target.symbol}-fundingRate-{month}.zip"
        funding = _read_zip_frame(funding_path, FUNDING_COLUMNS) if funding_path.is_file() else None

        day1_result = aggregate_aggtrades_day(
            read_aggtrades_zip(day1_zip),
            whale_thr=float(previous_result.p99_quote),
            prev_close=previous_close,
        )
        atomic = _atomic_from_bars(existing)
        # 首日以外的分钟原样保留，首日由本次重新聚合替换
        atomic = atomic.loc[atomic["open_time"] >= month_start_ms + _DAY_MS]
        atomic = pd.concat([atomic, day1_result.minutes], ignore_index=True)

        threshold_missing_days = [
            value
            for value in _day_starts_flagged(existing, "whale_threshold_missing")
            if value != month_start_ms
        ]
        repaired = assemble_bars(
            klines,
            atomic,
            start_ms=month_start_ms,
            end_ms=month_end_ms,
            metrics=metrics,
            funding=funding,
            previous_close=previous_close,
            aggtrades_missing_days=_day_starts_flagged(existing, "aggtrades_source_missing"),
            whale_threshold_missing_days=threshold_missing_days,
        )
        if len(repaired) != len(existing):
            raise RuntimeError(f"行数变化：{target.symbol} {month} {len(existing)}→{len(repaired)}")

        day1_mask = repaired["open_time"] < month_start_ms + _DAY_MS
        differences = _later_column_differences(repaired, existing, day1_mask)
        if differences:
            raise RuntimeError(f"首日以外的数据发生变化：{target.symbol} {month} {differences[:6]}")

        if not dry_run:
            write_monthly_bars(repaired, target.bars_path)
            _update_threshold_audit(
                bars_root,
                alias,
                day,
                p99_quote=float(day1_result.p99_quote),
                n_trades=int(day1_result.n_trades),
                whale_thr=float(previous_result.p99_quote),
                source_date=day - timedelta(days=1),
                lock_path=raw_root / "locks" / f"{alias}.lock",
            )
    finally:
        for path in workspace.glob("*"):
            path.unlink(missing_ok=True)
        workspace.rmdir()

    return {
        "symbol": target.symbol,
        "alias": alias,
        "month": month,
        "p99_prev": float(previous_result.p99_quote),
        "n_trades_day1": int(day1_result.n_trades),
        "written": not dry_run,
    }


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    """脚本入口：扫描并修复受影响月文件。"""
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="修复历史 bars 首日大单阈值缺失（T1-9 遗留）")
    parser.add_argument("--aliases", default="data/universe/aliases.json")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--bars-dir", default="data/bars")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--alias", action="append", default=[], help="只处理指定别名（可重复）")
    parser.add_argument("--months", default="", help="只处理指定月份，逗号分隔（YYYY-MM）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    aliases = load_aliases(_resolve(repo_root, args.aliases))
    bars_root = _resolve(repo_root, args.bars_dir)
    raw_root = _resolve(repo_root, args.raw_dir)
    # 封存集月份一律不读不写（AGENTS.md §6 红线 2）：只允许 M1~M{months-holdout_months}
    base = load_config(BaseConfig, _resolve(repo_root, args.base))
    months = universe_months(base)
    allowed_months = set(months[: len(months) - base.data_window.holdout_months])
    requested = {item.strip() for item in args.months.split(",") if item.strip()}
    month_filter = allowed_months & requested if requested else allowed_months
    targets = _find_targets(bars_root, aliases, set(args.alias), month_filter)
    if args.limit is not None:
        targets = targets[: args.limit]
    print(f"待修复「币·月」{len(targets)} 个（dry_run={args.dry_run}）")

    failures: list[tuple[RepairTarget, str]] = []
    repaired = 0
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:

        def process(target: RepairTarget) -> dict[str, object]:
            return repair_one(client, target, bars_root, raw_root, dry_run=args.dry_run)

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(process, target): target for target in targets}
            for done, future in enumerate(as_completed(futures), start=1):
                target = futures[future]
                try:
                    info = future.result()
                except Exception as error:  # noqa: BLE001 - 运维脚本需汇总失败后继续
                    failures.append((target, str(error)))
                    print(f"[{done}/{len(targets)}] 失败 {target.symbol} {target.month}: {error}")
                    continue
                repaired += 1
                print(
                    f"[{done}/{len(targets)}] {info['symbol']} {info['month']} "
                    f"p99_prev={info['p99_prev']:.4f} n_day1={info['n_trades_day1']}"
                )

    print(f"完成 {repaired} 个，失败 {len(failures)} 个")
    for target, message in failures[:20]:
        print(f"  失败 {target.symbol} {target.month}: {message}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
