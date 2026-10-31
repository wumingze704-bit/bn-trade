"""分钟数据聚合编排入口（服务器运行）。

规格：docs/03-数据与选币.md §2.2（aggTrades → 分钟原子量，两遍处理）
"""

from __future__ import annotations

import argparse
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from trade.data.aggregate import (
    MINUTE_ATOMIC_COLUMNS,
    aggregate_aggtrades_day,
    assemble_bars,
    has_header,
    read_aggtrades_zip,
    write_monthly_bars,
)
from trade.data.universe import load_aliases

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
METRICS_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _alias_for(aliases: dict[str, str], symbol: str) -> str:
    if symbol not in aliases:
        raise ValueError(f"币种不在别名表 aliases.json 中：{symbol}")
    return aliases[symbol]


def _read_zip_frame(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    """读取 Binance ZIP CSV，兼容有无表头的数据文件（03 §1.1）。"""
    with zipfile.ZipFile(path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"ZIP 内没有 CSV：{path}")
        with archive.open(csv_names[0]) as handle:
            first_line = handle.readline().decode("utf-8").strip()
    header = 0 if has_header(first_line) else None
    frame = pd.read_csv(path, compression="zip", header=header)
    if header is None:
        if len(frame.columns) != len(columns):
            raise ValueError(
                f"CSV 列数不符：{path}，期望 {len(columns)}，实际 {len(frame.columns)}"
            )
        frame.columns = columns
    return frame.rename(columns=lambda column: str(column).strip())


def _month_bounds(month: str) -> tuple[int, int]:
    """返回 UTC 自然月的 [start_ms, end_ms) 边界。"""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return int(start.timestamp() * 1000), int(next_month.timestamp() * 1000)


def _previous_month_close(kline_dir: Path, symbol: str, month: str) -> float | None:
    """读取目标月之前最近月文件的末收盘价，用于月初缺失分钟回退。"""
    candidates = []
    for path in kline_dir.glob(f"{symbol}-1m-*.zip"):
        file_month = path.name.removeprefix(f"{symbol}-1m-").removesuffix(".zip")
        if file_month < month:
            candidates.append((file_month, path))
    if not candidates:
        return None
    previous_path = max(candidates)[1]
    frame = _read_zip_frame(previous_path, KLINE_COLUMNS)
    return float(frame.sort_values("open_time")["close"].iloc[-1])


def main(argv: list[str] | None = None) -> int:
    """aggregate.py 入口：把某币某月的日度 aggTrades 聚合为分钟原子量。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="聚合 aggTrades 为分钟原子量")
    parser.add_argument("--aliases", default="data/universe/aliases.json")
    parser.add_argument("--symbol", required=True, help="单个合约 symbol（冒烟用）")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bars-dir", default="data/bars")
    parser.add_argument(
        "--keep-raw", action="store_true", help="聚合后保留原始 zip（默认删除，03 §2.2）"
    )
    args = parser.parse_args(argv)

    aliases = load_aliases(_resolve(root, args.aliases))
    alias = _alias_for(aliases, args.symbol)
    raw_root = _resolve(root, args.raw_dir)
    kline_dir = raw_root / "klines" / alias
    metrics_dir = raw_root / "metrics" / alias
    funding_dir = raw_root / "fundingrate" / alias
    aggtrades_dir = raw_root / "aggtrades" / alias
    kline_path = kline_dir / f"{args.symbol}-1m-{args.month}.zip"
    funding_path = funding_dir / f"{args.symbol}-fundingRate-{args.month}.zip"
    if not kline_path.is_file():
        raise FileNotFoundError(f"未找到月度 1m K 线：{kline_path}")
    if not funding_path.is_file():
        raise FileNotFoundError(f"未找到月度 fundingRate：{funding_path}")

    start_ms, end_ms = _month_bounds(args.month)
    klines = _read_zip_frame(kline_path, KLINE_COLUMNS)
    previous_close = _previous_month_close(kline_dir, args.symbol, args.month)
    metrics_paths = sorted(metrics_dir.glob(f"{args.symbol}-metrics-{args.month}-*.zip"))
    metrics = (
        pd.concat([_read_zip_frame(path, METRICS_COLUMNS) for path in metrics_paths])
        if metrics_paths
        else None
    )
    funding = _read_zip_frame(funding_path, FUNDING_COLUMNS)

    available_zips: dict[date, Path] = {}
    for zip_path in aggtrades_dir.glob(f"{args.symbol}-aggTrades-*.zip"):
        date_text = zip_path.stem.removeprefix(f"{args.symbol}-aggTrades-")
        try:
            available_zips[datetime.strptime(date_text, "%Y-%m-%d").date()] = zip_path
        except ValueError:
            continue
    month_start_date = datetime.fromtimestamp(start_ms / 1000, tz=UTC).date()
    month_end_date = datetime.fromtimestamp(end_ms / 1000, tz=UTC).date()
    previous_dates = [day for day in available_zips if day < month_start_date]
    previous_p99: float | None = None
    previous_p99_date: date | None = None
    if previous_dates:
        previous_day = max(previous_dates)
        previous_day_result = aggregate_aggtrades_day(
            read_aggtrades_zip(available_zips[previous_day]), whale_thr=float("nan")
        )
        if previous_day_result.n_trades:
            previous_p99 = previous_day_result.p99_quote
            previous_p99_date = previous_day

    minute_frames: list[pd.DataFrame] = []
    threshold_rows: list[dict[str, object]] = []
    missing_source_day_starts: list[int] = []
    missing_threshold_day_starts: list[int] = []
    current_day = month_start_date
    while current_day < month_end_date:
        day_start = datetime.combine(current_day, datetime.min.time(), UTC)
        day_start_ms = int(day_start.timestamp() * 1000)
        zip_path = available_zips.get(current_day)
        threshold_used = previous_p99 if previous_p99 is not None else float("nan")
        if zip_path is None:
            missing_source_day_starts.append(day_start_ms)
            if previous_p99 is None:
                missing_threshold_day_starts.append(day_start_ms)
            threshold_rows.append(
                {
                    "date": current_day,
                    "p99_quote": float("nan"),
                    "n_trades": 0,
                    "whale_thr": threshold_used,
                    "p99_source_date": previous_p99_date,
                    "missing_day": 1,
                }
            )
            current_day += timedelta(days=1)
            continue
        if previous_p99 is None:
            missing_threshold_day_starts.append(day_start_ms)
        prior_klines = klines.loc[klines["open_time"] < day_start_ms, "close"]
        day_previous_close = (
            float(prior_klines.iloc[-1]) if not prior_klines.empty else previous_close
        )
        day_result = aggregate_aggtrades_day(
            read_aggtrades_zip(zip_path),
            whale_thr=threshold_used,
            prev_close=day_previous_close,
        )
        minute_frames.append(day_result.minutes)
        threshold_rows.append(
            {
                "date": current_day,
                "p99_quote": day_result.p99_quote,
                "n_trades": day_result.n_trades,
                "whale_thr": threshold_used,
                "p99_source_date": previous_p99_date,
                "missing_day": 0,
            }
        )
        if day_result.n_trades:
            previous_p99 = day_result.p99_quote
            previous_p99_date = current_day
        current_day += timedelta(days=1)

    atomic = (
        pd.concat(minute_frames, ignore_index=True)
        if minute_frames
        else pd.DataFrame(columns=MINUTE_ATOMIC_COLUMNS)
    )
    bars = assemble_bars(
        klines,
        atomic,
        start_ms=start_ms,
        end_ms=end_ms,
        metrics=metrics,
        funding=funding,
        previous_close=previous_close,
        aggtrades_missing_days=missing_source_day_starts,
        whale_threshold_missing_days=missing_threshold_day_starts,
    )
    out_path = _resolve(root, args.bars_dir) / alias / f"{args.month.replace('-', '')}.parquet"
    write_monthly_bars(bars, out_path)
    threshold_path = out_path.parent / "whale_thr.parquet"
    new_thresholds = pd.DataFrame(threshold_rows)
    if threshold_path.is_file():
        old_thresholds = pd.read_parquet(threshold_path)
        new_thresholds = pd.concat([old_thresholds, new_thresholds], ignore_index=True)
    new_thresholds = new_thresholds.drop_duplicates("date", keep="last").sort_values("date")
    temporary_threshold_path = threshold_path.with_name(f"{threshold_path.name}.tmp")
    new_thresholds.to_parquet(temporary_threshold_path, index=False, compression="zstd")
    temporary_threshold_path.replace(threshold_path)
    if not args.keep_raw:
        for day, zip_path in available_zips.items():
            if month_start_date <= day < month_end_date:
                zip_path.unlink(missing_ok=True)
    print(f"bars 组装完成：{out_path}，共 {len(bars)} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
