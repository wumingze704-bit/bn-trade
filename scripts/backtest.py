"""运行 R0 信号回测（06 §3、§8）。

用法（在东京服务器，有 data/bars/ 与 exchangeInfo 快照时）：
    python scripts/backtest.py --signals artifacts/signals/r0.parquet \
        --rules artifacts/exchange_info/rules.json --run-id r0_20261029
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule, run_backtest
from trade.backtest.metrics import compute_metrics
from trade.backtest.report import append_experiments_log, write_report
from trade.backtest.sanity import run_sanity_checks
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import load_bars
from trade.data.universe import load_coins
from trade.features.registry import FeaturesConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R0 信号回测")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True, help="exchangeInfo 规则快照 JSON")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--coins", type=Path, default=Path("configs/coins.yaml"))
    parser.add_argument("--start", type=str, default="2024-10-01")
    parser.add_argument("--end", type=str, default="2026-09-30")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    base = load_config(BaseConfig, args.base)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    features = load_config(FeaturesConfig, args.features)
    coins = load_coins(args.coins)

    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end, tz="UTC").timestamp() * 1000)
    signals = pd.read_parquet(args.signals)

    rules_raw = json.loads(args.rules.read_text(encoding="utf-8"))
    rules: dict[str, TradeRule] = {alias: TradeRule(**rules_raw[alias]) for alias in rules_raw}
    groups = {e.alias: "train" for e in coins.train}
    groups.update({e.alias: "holdout" for e in coins.holdout})
    groups.update({e.alias: "blind" for e in coins.blind})

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for entry in [*coins.train, *coins.holdout, *coins.blind]:
        bars_by_symbol[entry.alias] = load_bars(base.paths.data_root, entry.alias, start_ms, end_ms)

    fees = FeeSchedule(base.fees.maker, base.fees.taker)
    scenario_results = {}
    for scen in backtest_cfg.scenarios:
        result, _ = run_backtest(
            bars_by_symbol,
            signals,
            rules,
            groups,
            scen_name=scen,
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=features.label.k_sl,
            sl_min=features.label.sl_min,
            sl_max=features.label.sl_max,
            H_label=features.label.H_label,
            initial_equity=10000.0,
        )
        scenario_results[scen] = result

    reference_metrics = compute_metrics(scenario_results["base"], 10000.0)
    report_dir = base.paths.artifact_root / "reports" / args.run_id
    sanity = run_sanity_checks(
        s8="not_run_待真实数据",
        s10="not_run_盲测币未抽取",
        s11="not_run",
    )
    write_report(
        report_dir,
        args.run_id,
        source="real",
        scenario_results=scenario_results,
        reference_metrics=reference_metrics,
        sanity_results=sanity,
        initial_equity=10000.0,
    )
    config_hash = hashlib.sha256(args.run_id.encode()).hexdigest()[:16]
    append_experiments_log(
        report_dir / "EXPERIMENTS.log", args.run_id, config_hash, reference_metrics
    )
    print(f"报告目录：{report_dir}")


if __name__ == "__main__":
    main()
