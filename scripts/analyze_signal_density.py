"""信号密度只读分析（月化收益可行性，只分析不改参数；05 §7.2、06 §9）。

规格：docs/04-特征与标签.md §6；docs/05-模型与训练.md §7.2

只用调参期 M1~M12：
- 每月入选币数（`data/universe/{YYYY-MM}.json`）；
- 每币每日预过滤样本数（`is_sample_mask`，与训练/回测同一份实现）；
- 给定 `--signals`（R0 或模型信号）时，θ = 0.30 / 0.50 / 0.70 下的日均信号数。

本脚本不修改任何配置、阈值或预过滤参数；结果不得用于调参（红线 3）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from trade.common.config import BaseConfig, load_config
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    segment_ids,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import load_universe_config
from trade.features.registry import FeaturesConfig
from trade.labels.prefilter import is_sample_mask

_THETAS = (0.30, 0.50, 0.70)


def build_parser() -> argparse.ArgumentParser:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="信号密度只读分析（调参期）")
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--signals", type=Path, default=None, help="R0/模型信号（可选）")
    parser.add_argument("--limit", type=int, default=None, help="只分析前 N 个币（冒烟用）")
    parser.add_argument("--out", type=Path, default=Path("artifacts/reports/signal_density.md"))
    return parser


def _month_of(open_time: np.ndarray) -> np.ndarray:
    """毫秒时间戳 → `YYYY-MM` 字符串数组（UTC）。"""
    return pd.to_datetime(open_time, unit="ms", utc=True).strftime("%Y-%m").to_numpy(dtype=object)


def prefilter_samples(
    bars: pd.DataFrame, features_cfg: FeaturesConfig, trade_mask: np.ndarray
) -> np.ndarray:
    """该币的预过滤样本掩码（`is_sample_mask` 与交易范围取交集）。"""
    segments = segment_ids(bars["open_time"].to_numpy(dtype="int64"))
    sample = is_sample_mask(
        bars,
        features_cfg.prefilter.vol_z_60,
        features_cfg.prefilter.r1_sigma,
        features_cfg.prefilter.brk_60,
        features_cfg.warmup_bars,
        segments,
    )
    return sample & trade_mask


def summarize(
    samples_by_month: dict[str, int],
    months: list[str],
    coins_per_month: dict[str, int],
    *,
    days: float = 30.0,
) -> pd.DataFrame:
    """按月汇总预过滤样本密度（样本/日、每币每日样本数）。"""
    rows: list[dict[str, object]] = []
    for month in months:
        total = samples_by_month.get(month, 0)
        n_coins = coins_per_month.get(month, 0)
        rows.append(
            {
                "month": month,
                "coins": n_coins,
                "prefilter_samples": total,
                "samples_per_day": total / days,
                "samples_per_coin_day": (total / (n_coins * days)) if n_coins else 0.0,
            }
        )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    """入口：统计调参期每月入选币数与预过滤样本密度。"""
    args = build_parser().parse_args(argv)
    base = load_config(BaseConfig, args.base)
    features_cfg = load_config(FeaturesConfig, args.features)
    universe_cfg = load_universe_config(args.universe)
    months = universe_months(base)
    tuning_months = months[: base.data_window.tuning_months]
    wb = window_bounds(base)
    membership = load_universe_membership(base.paths.data_root, tuning_months)
    coins = collect_universe_coins(base.paths.data_root, tuning_months)
    if args.limit is not None:
        coins = coins[: args.limit]

    start = int(np.datetime64(tuning_months[0], "M").astype("datetime64[ms]").astype("int64"))
    end = int(
        (np.datetime64(tuning_months[-1], "M") + np.timedelta64(1, "M"))
        .astype("datetime64[ms]")
        .astype("int64")
    )
    samples_by_month: dict[str, int] = {}
    for coin in coins:
        bars = load_bars(
            base.paths.data_root,
            coin.alias,
            start,
            end,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        times = bars["open_time"].to_numpy(dtype="int64")
        mask = universe_trade_mask(
            times, coin.symbol, membership, universe_cfg.rule.new_entry_delay_days
        )
        sample = prefilter_samples(bars, features_cfg, mask)
        unique, counts = np.unique(_month_of(times[sample]), return_counts=True)
        for month, count in zip(unique, counts, strict=True):
            samples_by_month[str(month)] = samples_by_month.get(str(month), 0) + int(count)

    coins_per_month = {month: len(membership[month]) for month in tuning_months}
    table = summarize(samples_by_month, tuning_months, coins_per_month)
    lines = [
        "# 信号密度（调参期 M1~M12，只读分析）",
        "",
        f"- 分析币数：{len(coins)}（未给 `--limit` 时为调参期全部入选币）",
        "- 本分析不修改任何阈值、预过滤参数或配置；结果不得用于调参。",
        "",
        "| 月份 | 入选币数 | 预过滤样本 | 样本/日 | 每币每日样本 |",
        "| :-- | --: | --: | --: | --: |",
    ]
    for row in table.itertuples(index=False):
        lines.append(
            f"| {row.month} | {row.coins} | {row.prefilter_samples} "
            f"| {row.samples_per_day:.1f} | {row.samples_per_coin_day:.2f} |"
        )
    if args.signals is not None:
        signals = pd.read_parquet(args.signals)
        signals = signals.assign(month=_month_of(signals["open_time"].to_numpy(dtype="int64")))
        signals = signals[signals["month"].isin(tuning_months)]
        days = 30.0 * len(tuning_months)
        lines += ["", "| θ | 日均信号数（调参期） |", "| :-- | --: |"]
        for theta in _THETAS:
            count = int((signals["p"] >= theta).sum())
            lines.append(f"| {theta:.2f} | {count / days:.2f} |")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
