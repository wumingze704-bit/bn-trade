"""R2/R3 标签级筛选：给定出场/止损参数，直接在调参期重建 `exit_sim` 标签并统计。

规格：docs/04-特征与标签.md §7.3；docs/05-模型与训练.md §6.1；docs/06-回测引擎.md §4

不训练模型：出场参数只改变标签分布，标签层面的单笔净收益（bp）与净 R 结构足以
判断一组参数是否值得进入模型级验证。全部只读调参期 M1~M12。

用法：
    python scripts/r2_screen.py --trial-id r2_base --limit-coins 60
    python scripts/r2_screen.py --trial-id r2_slmin15 --sl-min 0.015 --limit-coins 60
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.engine import TradeRule
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
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
from trade.labels.exit_sim import exit_sim
from trade.labels.prefilter import is_sample_mask


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


def _pf(values: np.ndarray) -> float:
    """按净收益（bp 或 R）计算的 PF；无样本或全为非有限值时返回 0。"""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    wins = float(finite[finite > 0].sum())
    losses = float(-finite[finite < 0].sum())
    return wins / losses if losses > 0 else float("inf")


def screen(args: argparse.Namespace) -> dict[str, object]:
    """重建一组参数下的标签并返回统计（调参期，只读）。"""
    root = Path(__file__).resolve().parents[1]

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    base_cfg = load_config(BaseConfig, resolve(args.base))
    features_cfg = load_config(FeaturesConfig, resolve(args.features))
    exit_cfg = load_config(ExitConfig, resolve(args.exits))
    overrides: dict[str, object] = {}
    for name in ("H_max", "stall_bars", "be_trigger_r", "trail_trigger_r", "k_trail", "tp_cap_r"):
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = value
    if args.exit_type is not None:
        overrides["type"] = args.exit_type
    if args.rr is not None:
        overrides["rr"] = args.rr
    if overrides:
        exit_cfg = exit_cfg.model_copy(update=overrides)
    vol_z = float(args.vol_z) if args.vol_z is not None else float(features_cfg.prefilter.vol_z_60)
    r1_sigma = (
        float(args.r1_sigma)
        if args.r1_sigma is not None
        else float(features_cfg.prefilter.r1_sigma)
    )
    brk_60 = float(args.brk_60) if args.brk_60 is not None else float(features_cfg.prefilter.brk_60)

    months = universe_months(base_cfg)
    tuning = months[: base_cfg.data_window.tuning_months]
    wb = window_bounds(base_cfg)
    end_ms = int(
        pd.Timestamp(f"{months[base_cfg.data_window.tuning_months]}-01", tz="UTC").timestamp()
        * 1000
    )
    membership = load_universe_membership(base_cfg.paths.data_root, tuning)
    coins = collect_universe_coins(base_cfg.paths.data_root, tuning)
    if args.stride > 1:
        coins = coins[:: args.stride]
    if args.limit_coins is not None:
        coins = coins[: args.limit_coins]
    rules = _rules(
        base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
        base_cfg.paths.data_root / "universe" / "aliases.json",
    )
    missing = sorted(coin.alias for coin in coins if coin.alias not in rules)
    if missing:
        raise ValueError(f"交易规则缺少 {missing[:5]}（03 §4.6、红线 13）")

    sl_min = float(args.sl_min) if args.sl_min is not None else features_cfg.label.sl_min
    k_sl = float(args.k_sl) if args.k_sl is not None else features_cfg.label.k_sl
    sl_max = float(args.sl_max) if args.sl_max is not None else features_cfg.label.sl_max
    notional_ref = 100.0 * float(args.position_frac) * float(args.leverage)
    max_chase = 0.002 if args.max_chase is None else float(args.max_chase)
    entry_mode = "taker" if args.entry_mode is None else str(args.entry_mode)
    entry_offset_bp = 30.0 if args.entry_offset_bp is None else float(args.entry_offset_bp)
    entry_expiry = 5 if args.entry_expiry_bars is None else int(args.entry_expiry_bars)
    cadence = (
        int(features_cfg.prefilter.cadence_min)
        if args.cadence_min is None
        else int(args.cadence_min)
    )
    atr_tf = (
        int(features_cfg.label.atr_timeframe_min)
        if args.atr_timeframe_min is None
        else int(args.atr_timeframe_min)
    )

    net_bp_parts: list[np.ndarray] = []
    net_r_parts: list[np.ndarray] = []
    exit_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "bp": 0.0, "r": 0.0})
    n_samples = 0
    dropped = 0
    for coin in coins:
        bars = load_bars(
            base_cfg.paths.data_root,
            coin.alias,
            wb.start_ms,
            end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        times = bars["open_time"].to_numpy(dtype="int64")
        segments = segment_ids(times)
        sample = is_sample_mask(
            bars,
            vol_z,
            r1_sigma,
            brk_60,
            features_cfg.warmup_bars,
            segments,
            direction=features_cfg.prefilter.direction,
            cadence_min=cadence,
        )
        sample &= universe_trade_mask(times, coin.symbol, membership, 0)
        positions = np.nonzero(sample)[0]
        n_samples += int(positions.size)
        if positions.size == 0:
            del bars
            continue
        labels, _ = exit_sim(
            bars,
            positions,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=features_cfg.label.H_label,
            entry_latency_ms=features_cfg.label.entry_latency_ms,
            y_min_r=features_cfg.label.y_min_r,
            exit_cfg=exit_cfg,
            fees_taker=base_cfg.fees.taker,
            fees_maker=base_cfg.fees.maker,
            half_spread_min=0.0001,
            slip_min=0.0001,
            Y=1.0,
            notional_ref=notional_ref,
            max_chase=max_chase,
            tick_size=rules[coin.alias].tick_size,
            segments=segments,
            r_mult_clip=features_cfg.label.exit_sim_r_mult_clip,
            entry_mode=entry_mode,
            entry_limit_offset_bp=entry_offset_bp,
            entry_limit_expiry_bars=entry_expiry,
            atr_timeframe_min=atr_tf,
        )
        del bars
        if labels.empty:
            continue
        sl = labels["sl"].to_numpy(dtype="float64")
        ret_net = labels["ret_net"].to_numpy(dtype="float64")
        r_net = ret_net / sl
        # 非有限值（缺价/深缺口）不参与统计，但计数写明（红线 13：不静默）
        finite = np.isfinite(ret_net) & np.isfinite(r_net) & np.isfinite(sl)
        if not finite.all():
            dropped += int((~finite).sum())
        ret_net = ret_net[finite]
        r_net = r_net[finite]
        exit_types = labels["exit_type"].to_numpy()[finite]
        if ret_net.size == 0:
            continue
        net_bp_parts.append(ret_net * 1e4)
        net_r_parts.append(r_net)
        for exit_type, bp, r in zip(exit_types, ret_net * 1e4, r_net, strict=True):
            entry = exit_stats[str(exit_type)]
            entry["n"] += 1.0
            entry["bp"] += float(bp)
            entry["r"] += float(r)

    net_bp = np.concatenate(net_bp_parts) if net_bp_parts else np.empty(0)
    net_r = np.concatenate(net_r_parts) if net_r_parts else np.empty(0)
    return {
        "trial_id": args.trial_id,
        "created_at": datetime.now(UTC).isoformat(),
        "months": [tuning[0], tuning[-1]],
        "coins": len(coins),
        "prefilter_samples": n_samples,
        "label_samples": int(net_bp.size),
        "dropped_nonfinite": dropped,
        "params": {
            "k_sl": k_sl,
            "sl_min": sl_min,
            "sl_max": sl_max,
            "exit_policy": exit_cfg.model_dump(),
            "max_chase": max_chase,
            "entry_mode": entry_mode,
            "entry_limit_offset_bp": entry_offset_bp,
            "entry_limit_expiry_bars": entry_expiry,
            "cadence_min": cadence,
            "atr_timeframe_min": atr_tf,
            "position_frac": float(args.position_frac),
            "leverage": float(args.leverage),
        },
        "mean_net_bp": float(net_bp.mean()) if net_bp.size else float("nan"),
        "median_net_bp": float(np.median(net_bp)) if net_bp.size else float("nan"),
        "pf_bp": _pf(net_bp),
        "mean_net_r": float(net_r.mean()) if net_r.size else float("nan"),
        "pf_r": _pf(net_r),
        "positive_rate": float((net_bp > 0).mean()) if net_bp.size else float("nan"),
        "exit_stats": {
            key: {
                "n": value["n"],
                "share": value["n"] / max(1.0, float(net_bp.size)),
                "mean_bp": value["bp"] / max(1.0, value["n"]),
                "mean_r": value["r"] / max(1.0, value["n"]),
            }
            for key, value in exit_stats.items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    """r2_screen.py 入口：写 artifacts/reports/r3/{trial_id}.json。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="R2/R3 标签级筛选（只读调参期）")
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--exits", default="configs/exits.yaml")
    parser.add_argument("--stride", type=int, default=1, help="按 alias 每 k 个取 1（抽样屏筛）")
    parser.add_argument("--limit-coins", type=int, default=None)
    parser.add_argument("--k-sl", type=float, default=None)
    parser.add_argument("--sl-min", type=float, default=None)
    parser.add_argument("--sl-max", type=float, default=None)
    parser.add_argument("--H-max", type=int, default=None, dest="H_max")
    parser.add_argument("--stall-bars", type=int, default=None, dest="stall_bars")
    parser.add_argument("--be-trigger-r", type=float, default=None, dest="be_trigger_r")
    parser.add_argument("--trail-trigger-r", type=float, default=None, dest="trail_trigger_r")
    parser.add_argument("--k-trail", type=float, default=None, dest="k_trail")
    parser.add_argument("--tp-cap-r", type=float, default=None, dest="tp_cap_r")
    parser.add_argument("--max-chase", type=float, default=None)
    parser.add_argument("--entry-mode", default=None, choices=("taker", "maker", "limit_pullback"))
    parser.add_argument("--entry-offset-bp", type=float, default=None)
    parser.add_argument("--entry-expiry-bars", type=int, default=None)
    parser.add_argument("--cadence-min", type=int, default=None)
    parser.add_argument("--atr-timeframe-min", type=int, default=None)
    parser.add_argument("--exit-type", default=None, choices=("trail", "fixed"))
    parser.add_argument("--rr", type=float, default=None)
    parser.add_argument("--vol-z", type=float, default=None)
    parser.add_argument("--r1-sigma", type=float, default=None)
    parser.add_argument("--brk-60", type=float, default=None)
    parser.add_argument("--position-frac", type=float, default=0.18)
    parser.add_argument("--leverage", type=float, default=5.0)
    parser.add_argument("--out-dir", default="artifacts/reports/r3")
    args = parser.parse_args(argv)
    result = screen(args)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.trial_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
