"""T4-3 第 2 步：模型超参搜索（只在调参期 M1~M12，最多 12 组）。

规格：docs/05-模型与训练.md §6.1；试验登记 docs/06-回测引擎.md §8

用法（本地，有 data/features 与 data/bars 时）：
    python scripts/tuning_models.py --dry-run
    python scripts/tuning_models.py --features-dir data/features/ds_20261102 \
        --labels-dir data/labels/ds_20261102 --rules artifacts/exchange_info/rules.json

约束：固定第 1 步冻结的出场与止损参数；不碰测试段 M13~M22 与封存集 M23~M24。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule, run_backtest
from trade.backtest.metrics import compute_metrics
from trade.backtest.r0 import TrainConfig
from trade.backtest.report import (
    append_experiments_log,
    compute_experiment_hash,
    experiments_log_path,
    read_experiments_log,
)
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
from trade.data.universe import load_universe_config, role_for_symbol
from trade.features import FeaturesConfig
from trade.models.calibration import calibration_metrics, select_threshold, threshold_grid
from trade.models.config import ModelConfig
from trade.models.dataset import WindowDataset
from trade.models.train import train_ensemble

TRIAL_BUDGET = 30


@dataclass(frozen=True)
class Trial:
    """一组模型超参（05 §6.1 第 2 步）。"""

    window_L: int  # noqa: N815 - 与 05 §6.1 文档名一致
    hidden: int
    loss: str

    @property
    def run_id(self) -> str:
        return f"t4_3b_L{self.window_L}_h{self.hidden}_{self.loss}"


@dataclass
class TrialOutcome:
    """一组搜索结果（含失败组）。"""

    trial: Trial
    pf: float | None
    net_return: float | None
    n_trades: int | None
    theta: float | None
    failed: bool
    error: str | None = None


def build_trials(cfg: ModelConfig) -> list[Trial]:
    """按既定网格生成 12 组：L × hidden × loss（dropout 与 time decay 固定）。"""
    if cfg.dropout != 0.2:
        raise ValueError("第 2 步固定 dropout=0.2（05 §6.1）")
    if cfg.time_decay_halflife_months is not None:
        raise ValueError("第 2 步固定 time_decay_halflife_months=关闭（使用者决定）")
    return [
        Trial(window_L=window, hidden=hidden, loss=loss)
        for window in (30, 60, 120)
        for hidden in (32, 64)
        for loss in ("bce", "focal")
    ]


def count_logged_trials(log_path: Path) -> int:
    """统计 EXPERIMENTS.log 中已登记的组数（按 run_id 去重，含失败组）。"""
    if not log_path.exists():
        return 0
    return len({str(row.get("run_id")) for row in read_experiments_log(log_path)})


def remaining_budget(log_path: Path, *, cap: int = TRIAL_BUDGET) -> int:
    """返回剩余预算；已登记组数不得因本轮放宽。"""
    return max(0, cap - count_logged_trials(log_path))


def select_best(outcomes: list[TrialOutcome]) -> tuple[TrialOutcome, bool]:
    """按 05 §6.1 自动选择：验证段 base PF 最高且净收益 > 0。

    Returns:
        (选中结果, 是否所有候选都满足净收益 > 0)。若没有净收益 > 0 的组，
        返回 PF 最高者并标记为降级选择（调用方必须在报告中说明）。
    """
    valid = [
        item
        for item in outcomes
        if not item.failed and item.pf is not None and item.net_return is not None
    ]
    if not valid:
        raise ValueError("没有任何可用搜索结果（全部失败）")
    positive = [item for item in valid if float(item.net_return) > 0.0]
    pool = positive or valid
    best = max(
        pool,
        key=lambda item: (
            float(item.pf or 0.0),
            float(item.net_return or 0.0),
            int(item.n_trades or 0),
            -item.trial.window_L,
            -item.trial.hidden,
        ),
    )
    return best, bool(positive)


def update_train_yaml(path: Path, trial: Trial, *, draft: bool) -> None:
    """把选定超参写回 `train.yaml` 的 model 段；`draft` 时不动文件。

    只修改 window_L、hidden、loss、dropout、time_decay_halflife_months 五项，
    其余超参与 R0 阈值保持不变。
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = raw.setdefault("model", {})
    model["window_L"] = trial.window_L
    model["hidden"] = trial.hidden
    model["loss"] = trial.loss
    model["dropout"] = 0.2
    model["time_decay_halflife_months"] = None
    if not draft:
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _load_rules(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读取 exchangeInfo 规则快照（alias → TradeRule）。"""
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    out: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = rules.get(symbol)
        if rule is None:
            continue
        out[alias] = TradeRule(
            tick_size=float(rule.get("tick_size", 0.01)),
            step_size=float(rule.get("step_size", 0.001)),
            min_notional=float(rule.get("min_notional", 5.0)),
        )
    return out


def manifest_features(features_dir: Path) -> list[str]:
    """读取数据集 manifest 的特征顺序（05 §5）。"""
    manifest = json.loads((features_dir / "manifest.json").read_text(encoding="utf-8"))
    return list(manifest["features"])


def _val_signals(
    dataset: WindowDataset, result: object, temperature: float, months: list[str]
) -> pd.DataFrame:
    from trade.models.walkforward import predict_signals

    return predict_signals(
        dataset,
        state_dicts=result.state_dicts,
        arch=result.arch,
        arch_params=result.arch_params,
        temperature=temperature,
        months=months,
    )


def _threshold_metrics(
    signals: pd.DataFrame,
    n_days: float,
    *,
    bars_by_symbol: dict[str, pd.DataFrame],
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    features_cfg: FeaturesConfig,
    trade_masks: dict[str, np.ndarray] | None,
) -> dict[float, dict[str, float]]:
    """在验证段逐 θ 跑 base 情景，返回 PF / 净收益 / 笔数（05 §7.2）。"""
    metrics: dict[float, dict[str, float]] = {}
    for theta in threshold_grid():
        cfg = backtest_cfg.model_copy(update={"threshold": theta})
        result, _ = run_backtest(
            bars_by_symbol,
            signals,
            rules,
            groups,
            scen_name="base",
            fees=fees,
            backtest_cfg=cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=features_cfg.label.k_sl,
            sl_min=features_cfg.label.sl_min,
            sl_max=features_cfg.label.sl_max,
            H_label=features_cfg.label.H_label,
            initial_equity=backtest_cfg.reference.initial_equity,
            trade_masks=trade_masks,
        )
        row = compute_metrics(result, backtest_cfg.reference.initial_equity)
        metrics[theta] = {
            "net_return": float(row["net_return_usdt"]),
            "profit_factor": float(row["profit_factor"]),
            "n_trades": float(row["n_trades"]),
        }
    return metrics


def run_grid(args: argparse.Namespace) -> int:
    """执行 12 组（或剩余预算内）搜索并逐组登记 EXPERIMENTS.log。"""
    base_cfg = load_config(BaseConfig, args.base)
    train_cfg = load_config(TrainConfig, args.train)
    features_cfg = load_config(FeaturesConfig, args.features)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    universe_cfg = load_universe_config(args.universe)
    log_path = experiments_log_path(base_cfg.paths.artifact_root)
    budget = remaining_budget(log_path)
    trials = build_trials(train_cfg.model)
    if budget != len(trials):
        print(f"剩余预算 {budget} 组，与设定网格 {len(trials)} 组不一致；停止。", flush=True)
        return 2
    if args.max_groups is not None:
        trials = trials[: args.max_groups]

    months = universe_months(base_cfg)
    tuning = months[: base_cfg.data_window.tuning_months]
    train_months, val_months = tuning[:-3], tuning[-3:]
    wb = window_bounds(base_cfg)
    membership = load_universe_membership(base_cfg.paths.data_root, months)
    entries = collect_universe_coins(base_cfg.paths.data_root, months)
    groups = {entry.alias: role_for_symbol(entry.symbol, universe_cfg.roles) for entry in entries}
    train_symbols = [alias for alias, role in groups.items() if role == "train"]
    rules = _load_rules(args.rules, base_cfg.paths.data_root / "universe" / "aliases.json")
    feature_names = manifest_features(args.features_dir)

    val_start = int(pd.Timestamp(f"{val_months[0]}-01", tz="UTC").timestamp() * 1000)
    val_end = int(pd.Timestamp(f"{val_months[-1]}-01", tz="UTC").timestamp() * 1000)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    trade_masks: dict[str, np.ndarray] = {}
    for entry in entries:
        bars = load_bars(
            base_cfg.paths.data_root,
            entry.alias,
            val_start,
            val_end,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        bars_by_symbol[entry.alias] = bars
        trade_masks[entry.alias] = universe_trade_mask(
            bars["open_time"].to_numpy(dtype="int64"),
            entry.symbol,
            membership,
            universe_cfg.rule.new_entry_delay_days,
        )
    n_days = float((val_end - val_start) / 86_400_000)
    outcomes: list[TrialOutcome] = []

    for trial in trials:
        cfg = train_cfg.model.model_copy(
            update={
                "window_L": trial.window_L,
                "hidden": trial.hidden,
                "loss": trial.loss,
                "dropout": 0.2,
                "time_decay_halflife_months": None,
            }
        )
        try:
            train_ds = WindowDataset(
                args.features_dir,
                args.labels_dir,
                train_symbols,
                feature_names,
                window_L=cfg.window_L,
                months=train_months,
                data_placement=cfg.data_placement,
            )
            val_ds = WindowDataset(
                args.features_dir,
                args.labels_dir,
                train_symbols,
                feature_names,
                window_L=cfg.window_L,
                months=val_months,
                data_placement=cfg.data_placement,
            )
            result = train_ensemble(train_ds, val_ds, cfg)
            calibration = calibration_metrics(
                np.concatenate([np.asarray(logit) for logit in result.val_logits]),
                np.asarray(result.val_y),
            )
            val_signals = _val_signals(
                val_ds, result, float(calibration["temperature"]), val_months
            )
            metrics = _threshold_metrics(
                val_signals,
                n_days,
                bars_by_symbol=bars_by_symbol,
                rules=rules,
                groups=groups,
                fees=FeeSchedule(maker=base_cfg.fees.maker, taker=base_cfg.fees.taker),
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing_cfg,
                features_cfg=features_cfg,
                trade_masks=trade_masks,
            )
            choice = select_threshold(metrics, n_days=n_days)
            outcome = TrialOutcome(
                trial=trial,
                pf=choice.profit_factor,
                net_return=choice.net_return,
                n_trades=choice.n_trades,
                theta=choice.theta,
                failed=False,
            )
        except Exception as error:  # noqa: BLE001 - 失败组也必须登记（05 §6.1）
            outcome = TrialOutcome(trial, None, None, None, None, True, str(error))
        outcomes.append(outcome)
        if not args.no_log:
            append_experiments_log(
                log_path,
                trial.run_id,
                compute_experiment_hash({"trial": trial.__dict__, "model": cfg.model_dump()}, {}),
                {
                    "profit_factor": outcome.pf,
                    "net_return_usdt": outcome.net_return,
                    "n_trades": outcome.n_trades,
                    "threshold": outcome.theta,
                    "failed": 1.0 if outcome.failed else 0.0,
                },
            )
        print(
            f"{trial.run_id}: PF={outcome.pf} net={outcome.net_return} "
            f"n={outcome.n_trades} θ={outcome.theta}"
            + (f" 失败={outcome.error}" if outcome.failed else ""),
            flush=True,
        )

    best, all_positive = select_best(outcomes)
    print(
        f"选定：{best.trial.run_id}（PF={best.pf}，净收益={best.net_return}，"
        f"θ={best.theta}，存在正收益组={all_positive}）"
    )
    update_train_yaml(args.train, best.trial, draft=args.draft_yaml)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T4-3 第 2 步模型超参搜索")
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
    parser.add_argument("--max-groups", type=int, default=None)
    parser.add_argument("--no-log", action="store_true", help="冒烟时不写 EXPERIMENTS.log")
    parser.add_argument("--draft-yaml", action="store_true", help="只校验，不写回 train.yaml")
    parser.add_argument("--dry-run", action="store_true", help="只打印网格与预算")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    train_cfg = load_config(TrainConfig, args.train)
    base_cfg = load_config(BaseConfig, args.base)
    budget = remaining_budget(experiments_log_path(base_cfg.paths.artifact_root))
    trials = build_trials(train_cfg.model)
    if args.dry_run:
        print(f"剩余预算 {budget} 组，网格 {len(trials)} 组：")
        for trial in trials:
            print(f"  {trial.run_id}")
        return 0
    return run_grid(args)


if __name__ == "__main__":
    raise SystemExit(main())
