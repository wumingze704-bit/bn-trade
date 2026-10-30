"""生成 R0 规则策略信号（05 §2）→ signals.parquet。

用法（在东京服务器，有 data/bars/ 时）：
    python scripts/r0_signals.py --out artifacts/signals/r0.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trade.backtest.r0 import TrainConfig, r0_signals_for_symbol
from trade.common.config import BaseConfig, load_config
from trade.data.loader import load_bars, window_bounds
from trade.data.universe import load_coins
from trade.features.registry import FeaturesConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 R0 规则策略信号")
    parser.add_argument("--out", type=Path, default=Path("artifacts/signals/r0.parquet"))
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--coins", type=Path, default=Path("configs/coins.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--train", type=Path, default=Path("configs/train.yaml"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    base = load_config(BaseConfig, args.base)
    coins = load_coins(args.coins)
    features = load_config(FeaturesConfig, args.features)
    train = load_config(TrainConfig, args.train)

    wb = window_bounds(base)
    # R0 信号只覆盖调参期 + 测试段，不碰封存集 M23~M24（03 §6、AGENTS.md §6 红线 2）
    end_ms = wb.holdout_start_ms

    entries = [*coins.train, *coins.holdout, *coins.blind]
    frames = []
    for entry in entries:
        bars = load_bars(
            base.paths.data_root,
            entry.alias,
            wb.start_ms,
            end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        frame = r0_signals_for_symbol(
            bars,
            train.r0,
            vol_z_60_thr=features.prefilter.vol_z_60,
            r1_sigma=features.prefilter.r1_sigma,
            brk_60_thr=features.prefilter.brk_60,
            warmup_bars=features.warmup_bars,
            symbol=entry.symbol,
        )
        frames.append(frame)

    signals = pd.concat(frames, ignore_index=True).sort_values(["open_time", "symbol"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    signals.to_parquet(args.out)
    n_signal = int((signals["p"] == 1).sum())
    print(f"写入 {args.out}，共 {len(signals)} 行（其中 p=1 信号 {n_signal} 条）")


if __name__ == "__main__":
    main()
