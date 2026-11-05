"""诊断突破信号的毛收益：入场后价格走势（不含成本），定位 alpha 来源。

规格：docs/01 §1 风险（模型需显著优于 R0）；04 §6 预过滤。
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
    universe_months,
    window_bounds,
)
from trade.data.universe import load_universe_config

# 未来窗口（分钟）
_HORIZONS = [5, 15, 30, 60]


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="诊断突破信号入场后毛收益")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--limit", type=int, default=30, help="采样前 N 个币")
    args = parser.parse_args(argv)

    base_cfg = load_config(
        BaseConfig, Path(args.base) if Path(args.base).is_absolute() else root / args.base
    )
    # 仅做校验：universe.yaml 必须在场且合法（后续取币走月度快照）
    load_universe_config(
        Path(args.universe) if Path(args.universe).is_absolute() else root / args.universe
    )
    data_root = base_cfg.paths.data_root
    months = universe_months(base_cfg)
    tuning_months_list = months[: base_cfg.data_window.tuning_months]
    coins = collect_universe_coins(data_root, tuning_months_list)[: args.limit]
    wb = window_bounds(base_cfg)
    # 直接用 M12 结束（tuning_months 个完整月后）
    from trade.data.loader import rebalance_ms_of

    tuning_end = rebalance_ms_of(months[base_cfg.data_window.tuning_months])

    rows: list[dict] = []
    for coin in coins:
        bars = load_bars(
            data_root,
            coin.alias,
            wb.start_ms,
            tuning_end,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        n = len(bars)
        if n == 0:
            continue
        close = bars["close"].to_numpy(dtype="float64")
        p1s = bars["p_1s"].to_numpy(dtype="float64")
        # 入场价 p_1s(t+1)：样本 t 的入场在 t+1 根
        # 简化：用 p_1s 序列前移，入场后 h 分钟的收益 = close[t+1+h] / p_1s[t+1] - 1
        for h in _HORIZONS:
            # 入场在 i（用 p_1s[i]），未来 h 分钟收盘 close[i+h]
            if n <= h + 2:
                continue
            entry = p1s[: -h - 1]  # 入场价序列（去掉尾部，保证 i+h 有效）
            future_close = close[h + 1 :]  # 对应未来 h 分钟收盘
            valid = (entry > 0) & np.isfinite(entry) & np.isfinite(future_close)
            ret = (future_close[valid] / entry[valid] - 1.0) * 10000  # bp
            rows.append(
                {
                    "alias": coin.alias,
                    "horizon": h,
                    "mean_bp": float(np.mean(ret)),
                    "win": float((ret > 0).mean()),
                }
            )

    df = pd.DataFrame(rows)
    summary = df.groupby("horizon").agg(mean_bp=("mean_bp", "mean"), win=("win", "mean"))
    print("=== 入场后毛收益（不含成本，bp）===")
    print(summary.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
