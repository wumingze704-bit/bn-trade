"""入场方式上限探测：回踩挂单的成交率与价格改善（只读调参期）。

规格：docs/06-回测引擎.md §3.2、§4

对每个预过滤样本，按「挂单价 = c_t × (1 − offset)，价格向下穿过挂单价 ≥ 1 tick
才算成交，最多等 N 根」的保守模型，统计成交率与相对 taker 入场价的价格改善。
这是「换入场方式」能带来的**上界**（不含逆向选择与出场路径变化）。

用法：
    python scripts/entry_probe.py --stride 4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.engine import TradeRule
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
from trade.features import FeaturesConfig
from trade.labels.prefilter import is_sample_mask

_OFFSETS_BP = (5.0, 10.0, 20.0, 30.0, 50.0)
_EXPIRY_BARS = (1, 3, 5)


def _rules(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读 exchangeInfo 快照（alias → TradeRule，03 §4.6）。"""
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    out: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = raw.get(symbol)
        if rule is None:
            continue
        out[alias] = TradeRule(
            tick_size=float(rule["tick_size"]),
            step_size=float(rule["step_size"]),
            min_notional=float(rule["min_notional"]),
        )
    return out


def main(argv: list[str] | None = None) -> int:
    """统计回踩挂单的成交率与价格改善上界。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="回踩挂单成交率与价格改善探测")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--limit-coins", type=int, default=None)
    parser.add_argument("--out", default="artifacts/reports/entry_probe.json")
    args = parser.parse_args(argv)

    base_cfg = load_config(BaseConfig, root / args.base)
    features_cfg = load_config(FeaturesConfig, root / args.features)
    months = universe_months(base_cfg)
    tuning = months[: base_cfg.data_window.tuning_months]
    wb = window_bounds(base_cfg)
    end_ms = int(
        pd.Timestamp(f"{months[base_cfg.data_window.tuning_months]}-01", tz="UTC").timestamp()
        * 1000
    )
    membership = load_universe_membership(base_cfg.paths.data_root, tuning)
    coins = collect_universe_coins(base_cfg.paths.data_root, tuning)[:: args.stride]
    if args.limit_coins is not None:
        coins = coins[: args.limit_coins]
    rules = _rules(
        base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
        base_cfg.paths.data_root / "universe" / "aliases.json",
    )

    stats = {
        f"off{offset:g}bp_N{expiry}": {"n": 0, "fill": 0, "improve_sum": 0.0, "improve_fill": 0}
        for offset in _OFFSETS_BP
        for expiry in _EXPIRY_BARS
    }
    n_samples = 0
    for coin in coins:
        if coin.alias not in rules:
            raise ValueError(f"交易规则缺少 {coin.alias}（03 §4.6、红线 13）")
        bars = load_bars(
            base_cfg.paths.data_root,
            coin.alias,
            wb.start_ms,
            end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        times = bars["open_time"].to_numpy(dtype="int64")
        close = bars["close"].to_numpy(dtype="float64")
        low = bars["low"].to_numpy(dtype="float64")
        p_entry = bars["p_1s"].to_numpy(dtype="float64")
        segments = segment_ids(times)
        sample = is_sample_mask(
            bars,
            features_cfg.prefilter.vol_z_60,
            features_cfg.prefilter.r1_sigma,
            features_cfg.prefilter.brk_60,
            features_cfg.warmup_bars,
            segments,
            direction=features_cfg.prefilter.direction,
        )
        sample &= universe_trade_mask(times, coin.symbol, membership, 0)
        positions = np.nonzero(sample)[0]
        n_samples += int(positions.size)
        del bars
        positions = positions[positions + 1 + max(_EXPIRY_BARS) < close.shape[0]]
        if positions.size == 0:
            continue
        c_t = close[positions]
        taker = p_entry[positions + 1]
        valid = np.isfinite(c_t) & np.isfinite(taker) & (c_t > 0) & (taker > 0)
        c_t = c_t[valid]
        taker = taker[valid]
        positions = positions[valid]
        tick = rules[coin.alias].tick_size
        for offset in _OFFSETS_BP:
            limit = np.floor(c_t * (1.0 - offset / 1e4) / tick + 1e-9) * tick
            for expiry in _EXPIRY_BARS:
                lows = np.stack([low[positions + j] for j in range(1, expiry + 1)], axis=1)
                filled = np.any(lows <= (limit - tick)[:, None], axis=1)
                key = f"off{offset:g}bp_N{expiry}"
                stats[key]["n"] += int(limit.size)
                stats[key]["fill"] += int(filled.sum())
                if filled.any():
                    improve = (taker[filled] - limit[filled]) / taker[filled] * 1e4
                    stats[key]["improve_sum"] += float(improve.sum())
                    stats[key]["improve_fill"] += int(improve.size)

    summary = {}
    for key, value in stats.items():
        n = max(1, value["n"])
        fill_rate = value["fill"] / n
        mean_improve = (
            value["improve_sum"] / value["improve_fill"] if value["improve_fill"] else 0.0
        )
        summary[key] = {
            "samples": value["n"],
            "fill_rate": fill_rate,
            "mean_improve_bp_when_filled": mean_improve,
            "expected_improve_bp": fill_rate * mean_improve,
        }
    payload = {"coins": len(coins), "samples": n_samples, "grid": summary}
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"coins={len(coins)} samples={n_samples}")
    for key, value in summary.items():
        print(
            f"{key:14s} fill {value['fill_rate']:.3f} "
            f"improve {value['mean_improve_bp_when_filled']:+.2f}bp "
            f"expected {value['expected_improve_bp']:+.2f}bp"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
