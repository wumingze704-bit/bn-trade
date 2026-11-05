"""5 折 Walk-Forward 与 F_final 训练脚本（03 §6、05 §6、§7）。

用法（本地，有 data/features、data/labels、data/bars 时）：
    python scripts/train_walkforward.py --run-id wf_20261105 --dry-run
    python scripts/train_walkforward.py --run-id wf_20261105 \
        --features-dir data/features/ds_20261102 --labels-dir data/labels/ds_20261102 \
        --rules artifacts/exchange_info/rules.json
    python scripts/train_walkforward.py --run-id final_20261105 --final

约束：每折使用完全相同、已冻结的超参与出场参数；阈值只在该折验证月选择；
测试段只读取 M13~M22；封存集 M23~M24 不在本脚本范围内。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
from tuning_models import _load_rules, _threshold_metrics, manifest_features

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule
from trade.backtest.r0 import TrainConfig
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import load_universe_config, role_for_symbol, rule_hash
from trade.features import FeaturesConfig
from trade.models.dataset import month_end_ms, month_start_ms
from trade.models.walkforward import ThresholdEvaluator, build_folds, run_final, run_fold


def make_evaluator(
    *,
    base_cfg: BaseConfig,
    universe_cfg: object,
    membership: dict[str, frozenset[str]],
    entries: list[object],
    rules_path: Path,
    backtest_cfg: BacktestConfig,
    sizing_cfg: SizingConfig,
    exit_cfg: ExitConfig,
    features_cfg: FeaturesConfig,
) -> Callable[[str], ThresholdEvaluator]:
    """返回按验证月构造阈值评估器的工厂（只读该月 bars）。"""

    def factory(month: str) -> ThresholdEvaluator:
        start = month_start_ms(month)
        end = month_end_ms(month)
        holdout_start = window_bounds(base_cfg).holdout_start_ms
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        groups: dict[str, str] = {}
        masks: dict[str, np.ndarray] = {}
        rules: dict[str, TradeRule] = _load_rules(
            rules_path, base_cfg.paths.data_root / "universe" / "aliases.json"
        )
        for entry in entries:
            bars = load_bars(
                base_cfg.paths.data_root,
                entry.alias,
                start,
                end,
                holdout_start_ms=holdout_start,
                allow_holdout=False,
            )
            bars_by_symbol[entry.alias] = bars
            groups[entry.alias] = role_for_symbol(entry.symbol, universe_cfg.roles)
            masks[entry.alias] = universe_trade_mask(
                bars["open_time"].to_numpy(dtype="int64"),
                entry.symbol,
                membership,
                universe_cfg.rule.new_entry_delay_days,
            )

        def evaluate(signals: pd.DataFrame, n_days: float) -> dict[float, dict[str, float]]:
            return _threshold_metrics(
                signals,
                n_days,
                bars_by_symbol=bars_by_symbol,
                rules=rules,
                groups=groups,
                fees=FeeSchedule(maker=base_cfg.fees.maker, taker=base_cfg.fees.taker),
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing_cfg,
                features_cfg=features_cfg,
                trade_masks=masks,
            )

        return evaluate

    return factory


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="5 折 Walk-Forward 与 F_final 训练")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--train", type=Path, default=Path("configs/train.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--features-dir", type=Path, default=Path("data/features/ds_20261102"))
    parser.add_argument("--labels-dir", type=Path, default=Path("data/labels/ds_20261102"))
    parser.add_argument("--rules", type=Path, default=Path("artifacts/exchange_info/rules.json"))
    parser.add_argument("--models-dir", type=Path, default=Path("artifacts/models"))
    parser.add_argument("--signals-dir", type=Path, default=Path("artifacts/signals/walkforward"))
    parser.add_argument("--final", action="store_true", help="训练 F_final（M1~M21 / M22）")
    parser.add_argument("--dry-run", action="store_true", help="只打印折划分")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    base_cfg = load_config(BaseConfig, args.base)
    train_cfg = load_config(TrainConfig, args.train)
    features_cfg = load_config(FeaturesConfig, args.features)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    universe_cfg = load_universe_config(args.universe)
    tuning_months = base_cfg.data_window.tuning_months
    months = universe_months(base_cfg)
    folds = build_folds(
        months,
        first_test_month=months[tuning_months],
        n_folds=train_cfg.walk_forward.max_folds,
        train_start=months[0],
    )
    if args.dry_run:
        for fold in folds:
            print(
                f"{fold.fold_id}: train {fold.train_months[0]}~{fold.train_months[-1]}，"
                f"val {fold.val_month}，test {fold.test_months}"
            )
        print(
            f"F_final: train {months[0]}~{months[tuning_months + 8]}，"
            f"val {months[tuning_months + 9]}"
        )
        return 0
    if len(folds) != train_cfg.walk_forward.max_folds:
        print(f"折数不足：得到 {len(folds)}，期望 {train_cfg.walk_forward.max_folds}")
        return 2

    entries = collect_universe_coins(base_cfg.paths.data_root, months)
    membership = load_universe_membership(base_cfg.paths.data_root, months)
    groups = {entry.alias: role_for_symbol(entry.symbol, universe_cfg.roles) for entry in entries}
    train_symbols = [alias for alias, role in groups.items() if role == "train"]
    feature_names = manifest_features(args.features_dir)
    factory = make_evaluator(
        base_cfg=base_cfg,
        universe_cfg=universe_cfg,
        membership=membership,
        entries=entries,
        rules_path=args.rules,
        backtest_cfg=backtest_cfg,
        sizing_cfg=sizing_cfg,
        exit_cfg=exit_cfg,
        features_cfg=features_cfg,
    )
    common = {
        "features_dir": args.features_dir,
        "labels_dir": args.labels_dir,
        "feature_names": feature_names,
        "train_symbols": train_symbols,
        "cfg": train_cfg.model,
        "walk_cfg": train_cfg.walk_forward,
        "features_cfg": features_cfg,
        "exit_cfg": exit_cfg,
        "universe_rule_hash": rule_hash(universe_cfg),
        "market": base_cfg.market,
        "models_root": args.models_dir,
    }
    if args.final:
        summary = run_final(
            run_id=args.run_id,
            train_months=months[: tuning_months + 9],
            val_month=months[tuning_months + 9],
            threshold_evaluator=factory(months[tuning_months + 9]),
            manifest_path=args.features_dir / "manifest.json",
            **common,  # type: ignore[arg-type]
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    results = []
    for fold in folds:
        result = run_fold(
            fold,
            run_id=args.run_id,
            all_symbols=list(groups),
            threshold_evaluator=factory(fold.val_month),
            signals_root=args.signals_dir,
            manifest_path=args.features_dir / "manifest.json",
            **common,  # type: ignore[arg-type]
        )
        results.append(result)
        print(
            f"{fold.fold_id}: 模型 {result.model_dir.name}，θ={result.threshold:.2f}，"
            f"信号 {result.n_test_signals} 行 → {result.signals_path}"
        )

    args.signals_dir.mkdir(parents=True, exist_ok=True)
    (args.signals_dir / "walk_forward_summary.json").write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "folds": [
                    {
                        "fold_id": item.fold.fold_id,
                        "val_month": item.fold.val_month,
                        "test_months": item.fold.test_months,
                        "model_dir": str(item.model_dir),
                        "signals": str(item.signals_path),
                        "threshold": item.threshold,
                        "val_pr_auc": item.val_pr_auc,
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"汇总：{args.signals_dir / 'walk_forward_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
