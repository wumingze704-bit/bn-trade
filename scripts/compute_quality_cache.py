"""全量真实 bars 质量缓存生成（T0.17 / T1-5）。

规格：docs/03-数据与选币.md §2.4；入口 `trade.data.quality.compute_quality_windows` /
`write_quality_cache`。遍历 M1~M{months-holdout_months} 入选币，按月再平衡时点 R 计算
「R 前 30 天」质量结论并写缓存，供 T0.13 选币第 7 步与训练/评估按月读取。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trade.common.config import BaseConfig, load_config
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    rebalance_ms_of,
    universe_months,
    window_bounds,
)
from trade.data.quality import compute_quality_windows, write_quality_cache


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="生成全量真实 bars 质量缓存（T0.17/T1-5）")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--out", default="artifacts/quality/quality_cache.parquet")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个币（冒烟用）")
    args = parser.parse_args(argv)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    base_cfg = load_config(BaseConfig, resolve(args.base))
    data_root = base_cfg.paths.data_root
    months = universe_months(base_cfg)
    holdout_months = base_cfg.data_window.holdout_months
    # 只用 M1~M{months-holdout_months}：封存集 M23~M24 不加载（红线 2）。
    build_months = months[: len(months) - holdout_months]
    coins = collect_universe_coins(data_root, build_months)
    wb = window_bounds(base_cfg)
    window_ends = [rebalance_ms_of(month) for month in build_months]

    if args.limit is not None:
        coins = coins[: args.limit]

    frames: list[pd.DataFrame] = []
    for idx, coin in enumerate(coins, start=1):
        symbol, alias = coin.symbol, coin.alias
        bars = load_bars(
            data_root,
            alias,
            wb.start_ms,
            wb.holdout_start_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        reports = compute_quality_windows(symbol, bars, window_ends)
        frames.append(reports)
        print(f"[{idx}/{len(coins)}] {symbol} ({alias}) rows={len(bars)}")

    all_reports = pd.concat(frames, ignore_index=True)
    write_quality_cache(all_reports, resolve(args.out))

    total = len(all_reports)
    qualified = int(all_reports["qualified"].sum())
    print(f"总窗口 {total}，合格 {qualified}，不合格 {total - qualified}")
    print(f"缓存已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
