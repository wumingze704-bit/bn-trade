"""运行 R0 信号回测（06 §3、§8）。

用法（在东京服务器，有 data/bars/ 与 exchangeInfo 快照时）：
    python scripts/backtest.py --signals artifacts/signals/r0.parquet \
        --rules artifacts/exchange_info/rules.json --run-id r0_20261029
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule, run_backtest
from trade.backtest.event_sim import event_sim
from trade.backtest.metrics import compute_metrics, daily_block_bootstrap_ci
from trade.backtest.r0 import TrainConfig
from trade.backtest.report import (
    append_experiments_log,
    compute_experiment_hash,
    experiments_log_path,
    write_report,
)
from trade.backtest.sanity import (
    compute_random_entry_candidates,
    random_entry_baseline,
    run_sanity_checks,
    s4_threshold_perturb,
    s5_time_stability,
    s6_concentration,
    s9_compare,
)
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import load_bars, window_bounds
from trade.data.universe import load_coins, load_universe
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
    parser.add_argument("--train", type=Path, default=Path("configs/train.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--coins", type=Path, default=Path("configs/coins.yaml"))
    parser.add_argument("--final", action="store_true", help="包含封存集 M23~M24（仅 G3，06 §8）")
    return parser


def _run_scenario(
    bars_by_symbol: dict[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    *,
    scen: str,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    features: FeaturesConfig,
    initial_equity: float,
):
    """运行单个成本情景的回测（06 §3.4）。"""
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
        initial_equity=initial_equity,
    )
    return result


def _monthly_fold_net(result) -> tuple[list[float], list[float]]:
    """按测试月与 5 折汇总净收益（06 §7 S5、03 §6）。"""
    monthly: dict[str, float] = {}
    for t in result.trades:
        month = _utc_month(t.signal_open_time)
        monthly[month] = monthly.get(month, 0.0) + t.net_pnl
    months = sorted(monthly)
    monthly_net = [monthly[m] for m in months]
    folds: dict[int, float] = {}
    for i, m in enumerate(months):
        fold = i // 2 + 1  # 相邻两月成折（03 §6：F1=M13~14 … F5=M21~22）
        folds[fold] = folds.get(fold, 0.0) + monthly[m]
    fold_net = [folds[f] for f in sorted(folds)]
    return monthly_net, fold_net


def _utc_month(ts_ms: int) -> str:
    """UTC 月份（06 §7 S5）。"""
    month = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{month.year:04d}-{month.month:02d}"


def main() -> None:
    args = build_parser().parse_args()
    base = load_config(BaseConfig, args.base)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    features = load_config(FeaturesConfig, args.features)
    train_cfg = load_config(TrainConfig, args.train)
    universe_cfg = load_universe(args.universe)
    coins = load_coins(args.coins)

    wb = window_bounds(base)
    run_end_ms = wb.end_ms if args.final else wb.holdout_start_ms
    signals = pd.read_parquet(args.signals)

    rules_raw = json.loads(args.rules.read_text(encoding="utf-8"))
    rules: dict[str, TradeRule] = {alias: TradeRule(**rules_raw[alias]) for alias in rules_raw}
    groups = {e.alias: "train" for e in coins.train}
    groups.update({e.alias: "holdout" for e in coins.holdout})
    groups.update({e.alias: "blind" for e in coins.blind})

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for entry in [*coins.train, *coins.holdout, *coins.blind]:
        bars_by_symbol[entry.alias] = load_bars(
            base.paths.data_root,
            entry.alias,
            wb.start_ms,
            run_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=args.final,
        )

    fees = FeeSchedule(base.fees.maker, base.fees.taker)
    # reference 运行权益/口径来自配置（06 §5.1），user 运行按 sizing.yaml（06 §5.4）。
    reference_sizing = sizing_cfg.model_copy(
        update={
            "initial_equity": backtest_cfg.reference.initial_equity,
            "mode": backtest_cfg.reference.mode,
            "risk_per_trade": backtest_cfg.reference.risk_per_trade,
            "base": backtest_cfg.reference.base,
        }
    )
    runs = {
        "reference": (reference_sizing, backtest_cfg.reference.initial_equity),
        "user": (sizing_cfg, sizing_cfg.initial_equity),
    }
    run_results: dict[str, dict[str, object]] = {}
    for run_name, (sizing, equity) in runs.items():
        run_results[run_name] = {
            scen: _run_scenario(
                bars_by_symbol,
                signals,
                rules,
                groups,
                scen=scen,
                fees=fees,
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing,
                features=features,
                initial_equity=equity,
            )
            for scen in backtest_cfg.scenarios
        }

    reference_result = run_results["reference"]["base"]
    reference_metrics = compute_metrics(reference_result, backtest_cfg.reference.initial_equity)

    # S1 随机入场基准（06 §7 S1，固定种子可复现）。
    candidates = compute_random_entry_candidates(
        bars_by_symbol,
        signals,
        rules,
        fees,
        backtest_cfg,
        exit_cfg,
        reference_sizing,
        features.label.k_sl,
        features.label.sl_min,
        features.label.sl_max,
        features.label.H_label,
    )
    s1 = random_entry_baseline(
        reference_result,
        candidates,
        n_iters=backtest_cfg.sanity.random_iters,
        seed=backtest_cfg.sanity.random_seed,
    )

    # S4 阈值扰动（06 §7 S4）。
    pfs: dict[str, float] = {}
    for direction, delta in (
        ("below", -backtest_cfg.sanity.threshold_perturb),
        ("above", backtest_cfg.sanity.threshold_perturb),
    ):
        perturbed = backtest_cfg.model_copy(update={"threshold": backtest_cfg.threshold + delta})
        presult = _run_scenario(
            bars_by_symbol,
            signals,
            rules,
            groups,
            scen="base",
            fees=fees,
            backtest_cfg=perturbed,
            exit_cfg=exit_cfg,
            sizing_cfg=reference_sizing,
            features=features,
            initial_equity=backtest_cfg.reference.initial_equity,
        )
        pfs[direction] = compute_metrics(presult, backtest_cfg.reference.initial_equity)[
            "profit_factor"
        ]
    s4 = s4_threshold_perturb(pfs["below"], pfs["above"])

    # S5 时间稳定性（06 §7 S5）。
    monthly_net, fold_net = _monthly_fold_net(reference_result)
    s5 = s5_time_stability(
        monthly_net,
        fold_net,
        min_months_positive=backtest_cfg.sanity.min_months_positive,
        min_folds_positive=backtest_cfg.sanity.min_folds_positive,
    )

    # S6 集中度（06 §7 S6）。
    s6 = s6_concentration(reference_result, backtest_cfg.reference.initial_equity)

    # S9 事件驱动对拍（P5 之前仅覆盖组合层，06 §3.4）。
    event = event_sim(
        bars_by_symbol,
        signals,
        rules,
        groups,
        scen_name="base",
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=reference_sizing,
        k_sl=features.label.k_sl,
        sl_min=features.label.sl_min,
        sl_max=features.label.sl_max,
        H_label=features.label.H_label,
        initial_equity=backtest_cfg.reference.initial_equity,
    )
    s9 = s9_compare(reference_result, event)

    # G2 统计显著性：单笔净期望的 UTC 日分块 bootstrap（08 §3 G2、06 §6）。
    g2_bootstrap: dict[str, dict[str, float]] = {}
    for label, group in (("all", None), ("train", "train"), ("holdout", "holdout")):
        group_trades = [t for t in reference_result.trades if group is None or t.group == group]
        g2_bootstrap[label] = daily_block_bootstrap_ci(
            group_trades,
            n_iters=backtest_cfg.sanity.bootstrap_iters,
            seed=backtest_cfg.sanity.bootstrap_seed,
        )

    report_dir = base.paths.artifact_root / "reports" / args.run_id
    sanity = run_sanity_checks(
        s1="passed" if s1["passed"] else "failed",
        s4=s4,
        s5=s5,
        s6=s6,
        s9=s9,
        s8="not_run_待真实数据",
        s10="not_run_盲测币未抽取",
        s11="not_run",
    )
    write_report(
        report_dir,
        args.run_id,
        source="real",
        run_results=run_results,
        reference_metrics=reference_metrics,
        sanity_results=sanity,
        initial_equity=backtest_cfg.reference.initial_equity,
        g2_bootstrap=g2_bootstrap,
    )
    config_hash = compute_experiment_hash(
        {
            "base": base.model_dump(mode="json"),
            "backtest": backtest_cfg.model_dump(mode="json"),
            "exits": exit_cfg.model_dump(mode="json"),
            "sizing": sizing_cfg.model_dump(mode="json"),
            "features": features.model_dump(mode="json"),
            "train": train_cfg.model_dump(mode="json"),
            "universe": universe_cfg.model_dump(mode="json"),
            "run_window": {
                "final": args.final,
                "start_ms": wb.start_ms,
                "end_ms": run_end_ms,
            },
        },
        {
            "coins_snapshot": args.coins,
            "exchange_rules_snapshot": args.rules,
            "signals": args.signals,
        },
    )
    append_experiments_log(
        experiments_log_path(base.paths.artifact_root),
        args.run_id,
        config_hash,
        reference_metrics,
    )
    if args.final:
        final_log = base.paths.artifact_root / "reports" / "FINAL_RUNS.log"
        final_log.parent.mkdir(parents=True, exist_ok=True)
        with final_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"ts": datetime.now(UTC).isoformat(), "run_id": args.run_id},
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"报告目录：{report_dir}")


if __name__ == "__main__":
    main()
