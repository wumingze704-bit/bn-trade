"""分钟数据聚合编排入口（服务器运行）。

规格：docs/03-数据与选币.md §2.2（aggTrades → 分钟原子量，两遍处理）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from trade.data.aggregate import aggregate_aggtrades, daily_whale_thresholds, read_aggtrades_zip
from trade.data.universe import load_coins


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _alias_for(coins: object, symbol: str) -> str:
    for entry in [*coins.train, *coins.holdout, *coins.blind]:
        if entry.symbol == symbol:
            return entry.alias
    raise ValueError(f"币种不在名单中：{symbol}")


def main(argv: list[str] | None = None) -> int:
    """aggregate.py 入口：把某币某月的日度 aggTrades 聚合为分钟原子量。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="聚合 aggTrades 为分钟原子量")
    parser.add_argument("--coins", default="configs/coins.yaml")
    parser.add_argument("--symbol", required=True, help="单个合约 symbol（冒烟用）")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bars-dir", default="data/bars")
    parser.add_argument(
        "--keep-raw", action="store_true", help="聚合后保留原始 zip（默认删除，03 §2.2）"
    )
    args = parser.parse_args(argv)

    coins = load_coins(_resolve(root, args.coins))
    alias = _alias_for(coins, args.symbol)
    raw_dir = _resolve(root, args.raw_dir) / "aggtrades" / alias
    bars_dir = _resolve(root, args.bars_dir) / alias
    bars_dir.mkdir(parents=True, exist_ok=True)

    zips = sorted(raw_dir.glob(f"{args.symbol}-aggTrades-{args.month}-*.zip"))
    if not zips:
        print(f"未找到 {args.month} 的 aggTrades zip：{raw_dir}", file=sys.stderr)
        return 1

    # 第一遍：逐日单笔成交额 p99（03 §2.2）
    daily_p99: dict[str, float] = {}
    for zip_path in zips:
        chunks = list(read_aggtrades_zip(zip_path))
        if not chunks:
            daily_p99[zip_path.stem] = 0.0
            continue
        day_df = pd.concat(chunks, ignore_index=True)
        daily_p99[zip_path.stem] = float(daily_whale_thresholds(day_df)["p99_quote"].iloc[0])

    # 第二遍：逐日聚合，whale_thr(D) = p99(D-1)；首日回退到当日 p99（冒烟无前日）
    minute_frames: list[pd.DataFrame] = []
    prev_p99: float | None = None
    for zip_path in zips:
        whale_thr = prev_p99 if prev_p99 is not None else daily_p99[zip_path.stem]
        atomic = aggregate_aggtrades(read_aggtrades_zip(zip_path), whale_thr=whale_thr)
        minute_frames.append(atomic)
        prev_p99 = daily_p99[zip_path.stem]
        if not args.keep_raw:
            zip_path.unlink()

    if minute_frames:
        output = pd.concat(minute_frames, ignore_index=True)
        out_path = bars_dir / f"{args.month}.parquet"
        output.to_parquet(out_path, index=False, compression="zstd")
        print(f"聚合完成：{out_path}，共 {len(output)} 分钟")
    else:
        print("无有效分钟数据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
