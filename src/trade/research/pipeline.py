"""研究流水线：网格评估、试验登记与轮次报告（docs/13 §9）。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import yaml

from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.config import ResearchConfig
from trade.research.data import load_market_data
from trade.research.engine import Engine, RunResult, ScenarioOptions, Trade
from trade.research.metrics import annualized_sharpe, summarize
from trade.research.rolling import (
    Fold,
    FoldChoice,
    ParamRun,
    concatenated_returns,
    fold_windows,
    selected_trades,
)
from trade.strategies.registry import build_strategy

_CTX: dict[str, Any] = {}


def params_key(params: Mapping[str, object]) -> str:
    """参数组的稳定键（JSON 排序序列化）。"""
    return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_grid(path: Path) -> dict[str, Any]:
    """读取网格 YAML：`{round, family, params: [...]}`。"""
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or "family" not in values or "params" not in values:
        raise ValueError(f"网格配置缺少 family / params：{path}")
    return values


def build_market(
    config: ResearchConfig, *, start_ms: int, end_ms: int, start_month: str, end_month: str
):
    """按研究配置加载数据（工作进程与主进程共用）。"""
    root = Path.cwd()
    return load_market_data(
        klines_1h=root / config.paths.klines_1h,
        klines_1d=root / config.paths.klines_1d,
        funding=root / config.paths.funding,
        universe_hist=root / config.paths.universe_hist,
        rules_path=root / config.paths.artifacts / "exchange_info" / "rules.json",
        start_ms=start_ms,
        end_ms=end_ms,
        start_month=start_month,
        end_month=end_month,
    )


def run_param(
    market,
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    family: str,
    params: Mapping[str, object],
    start_ms: int,
    end_ms: int,
    options: ScenarioOptions | None = None,
) -> ParamRun:
    """跑一组参数（全研究期一次），返回日收益、交易与统计。"""
    strategy = build_strategy(family, params, stop_max=config.execution.stop_max)
    engine = Engine(
        market=market,
        strategy=strategy,
        sizing_cfg=sizing,
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        options=options or ScenarioOptions(),
        seed=config.bootstrap.seed,
    )
    return _to_param_run(params, engine.run(start_ms, end_ms))


def _to_param_run(params: Mapping[str, object], result: RunResult) -> ParamRun:
    return ParamRun(
        key=params_key(params),
        params=dict(params),
        daily=result.daily_returns(),
        trades=result.trades,
        turnover=result.turnover,
        dropped=result.dropped,
        forced=result.forced_liquidations,
        cost_total=result.cost_total,
        funding_total=result.funding_total,
        long_gross=result.long_gross,
        short_gross=result.short_gross,
        risk_events=len(result.risk_events),
        net_exposure_p95=float(
            np.quantile(np.abs(np.asarray(result.net_exposure, dtype="float64")), 0.95)
        )
        if result.net_exposure
        else 0.0,
        params_meta={"daily_sharpe": result.daily_sharpe()},
    )


def _worker_init(payload: dict[str, Any]) -> None:
    """工作进程初始化：各自加载数据，避免跨进程序列化几百 MB 数组。"""
    config = ResearchConfig.model_validate(payload["config"])
    _CTX["config"] = config
    _CTX["sizing"] = SizingConfig.model_validate(payload["sizing"])
    _CTX["fee_taker"] = float(payload["fee_taker"])
    _CTX["risk"] = RiskLimits(**payload["risk"])
    _CTX["start_ms"] = int(payload["start_ms"])
    _CTX["end_ms"] = int(payload["end_ms"])
    _CTX["market"] = build_market(
        config,
        start_ms=int(payload["start_ms"]),
        end_ms=int(payload["end_ms"]),
        start_month=str(payload["start_month"]),
        end_month=str(payload["end_month"]),
    )


def _worker_task(task: dict[str, Any]) -> ParamRun:
    return run_param(
        _CTX["market"],
        config=_CTX["config"],
        sizing=_CTX["sizing"],
        fee_taker=_CTX["fee_taker"],
        risk_limits=_CTX["risk"],
        family=str(task["family"]),
        params=task["params"],
        start_ms=_CTX["start_ms"],
        end_ms=_CTX["end_ms"],
        options=ScenarioOptions(**task["options"]),
    )


SCENARIO_OPTIONS: dict[str, ScenarioOptions] = {
    "base": ScenarioOptions(),
    "cost2x": ScenarioOptions(name="cost2x", cost_mult=2.0),
    "nohalt": ScenarioOptions(name="nohalt", no_halt=True),
    "delay1": ScenarioOptions(name="delay1", delay_rebalances=1),
    "shuffle": ScenarioOptions(name="shuffle", shuffle=True),
    "long_only": ScenarioOptions(name="long_only", long_only=True),
    "short_only": ScenarioOptions(name="short_only", short_only=True),
}


def _run_tasks(
    tasks: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    workers: int,
    market,
) -> list[ParamRun]:
    """并行（或串行）执行参数任务。"""
    if not tasks:
        return []
    if workers <= 1:
        return [
            run_param(
                market,
                config=ResearchConfig.model_validate(payload["config"]),
                sizing=SizingConfig.model_validate(payload["sizing"]),
                fee_taker=float(payload["fee_taker"]),
                risk_limits=RiskLimits(**payload["risk"]),
                family=str(task["family"]),
                params=task["params"],
                start_ms=int(payload["start_ms"]),
                end_ms=int(payload["end_ms"]),
                options=ScenarioOptions(**task["options"]),
            )
            for task in tasks
        ]
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_worker_init, initargs=(payload,)
    ) as pool:
        return list(pool.map(_worker_task, tasks))


def _slice(daily: pd.Series, start_ms: int, end_ms: int) -> pd.Series:
    if daily.empty:
        return daily
    mask = (daily.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")) & (
        daily.index < pd.Timestamp(end_ms, unit="ms", tz="UTC")
    )
    return daily[mask]


def own_concat(run: ParamRun, folds: list[Fold]) -> pd.Series:
    """该参数自身在各折上的样本外收益拼接（用于登记试验夏普）。"""
    pieces = [
        piece
        for fold in folds
        if not (piece := _slice(run.daily, fold.start_ms, fold.end_ms)).empty
    ]
    return pd.concat(pieces).sort_index() if pieces else pd.Series(dtype="float64")


def _synthetic_result(
    daily: pd.Series,
    trades: list[Trade],
    *,
    initial_equity: float,
    net_exposure_p95: float,
) -> RunResult:
    equity = float(initial_equity) * (1.0 + daily).cumprod()
    equity_ts = np.asarray([int(ts.timestamp() * 1000) for ts in daily.index], dtype="int64")
    return RunResult(
        trades=trades,
        equity_ts=equity_ts,
        equity=equity.to_numpy(dtype="float64"),
        turnover=sum(trade.notional for trade in trades) * 2.0,
        dropped=0,
        forced_liquidations=sum(1 for trade in trades if trade.reason == "delist"),
        cost_total=sum(trade.fees for trade in trades),
        funding_total=sum(trade.funding for trade in trades),
        long_gross=sum(trade.gross_pnl for trade in trades if trade.side > 0),
        short_gross=sum(trade.gross_pnl for trade in trades if trade.side < 0),
        net_exposure=[net_exposure_p95],
        risk_events=[],
    )


def read_trial_sharpes(log_path: Path) -> list[float]:
    """从 EXPERIMENTS.log 读取全部 kind=tuning 记录的日频夏普。"""
    if not log_path.is_file():
        return []
    values: list[float] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("kind") == "tuning" and "daily_sharpe" in entry:
            values.append(float(entry["daily_sharpe"]))
    return values


def append_experiments(log_path: Path, entries: list[dict[str, Any]]) -> None:
    """按行追加试验登记（kind=tuning，docs/13 §9）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def _scenario_concat(
    runs: Mapping[tuple[str, str], ParamRun],
    folds: list[Fold],
    choices: list[FoldChoice],
    scenario: str,
) -> tuple[pd.Series, list[Trade]]:
    """某情景按各折所选参数拼接收益与折内交易。"""
    pieces: list[pd.Series] = []
    trades: list[Trade] = []
    for fold, choice in zip(folds, choices, strict=True):
        run = runs[(scenario, choice.params_key)]
        piece = _slice(run.daily, fold.start_ms, fold.end_ms)
        if not piece.empty:
            pieces.append(piece)
        trades.extend(
            trade for trade in run.trades if fold.start_ms <= trade.entry_ts < fold.end_ms
        )
    daily = pd.concat(pieces).sort_index() if pieces else pd.Series(dtype="float64")
    return daily, trades


def evaluate_grid(
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    grid: Mapping[str, Any],
    round_id: str,
    start_ms: int,
    end_ms: int,
    start_month: str,
    end_month: str,
    workers: int,
    out_dir: Path,
    log_path: Path,
    scenarios: Sequence[str] = ("cost2x", "nohalt", "delay1", "shuffle", "long_only", "short_only"),
) -> dict[str, Any]:
    """评估一轮的完整网格，写出报告并登记试验（docs/13 §9）。"""
    family = str(grid["family"])
    params_list = [dict(item) for item in grid["params"]]
    payload = {
        "config": config.model_dump(mode="json"),
        "sizing": sizing.model_dump(mode="json"),
        "fee_taker": fee_taker,
        "risk": {
            "daily_loss_stop": risk_limits.daily_loss_stop,
            "daily_loss_halt": risk_limits.daily_loss_halt,
            "max_drawdown_halt": risk_limits.max_drawdown_halt,
            "max_consecutive_losses": risk_limits.max_consecutive_losses,
            "consecutive_loss_pause_min": risk_limits.consecutive_loss_pause_min,
            "halt_mode": risk_limits.halt_mode,
            "halt_reset_days": risk_limits.halt_reset_days,
        },
        "start_ms": start_ms,
        "end_ms": end_ms,
        "start_month": start_month,
        "end_month": end_month,
    }
    market = None
    if workers <= 1:
        market = build_market(
            config, start_ms=start_ms, end_ms=end_ms, start_month=start_month, end_month=end_month
        )
    base_tasks = [
        {"family": family, "params": params, "options": {"name": "base"}} for params in params_list
    ]
    base_runs = _run_tasks(base_tasks, payload=payload, workers=workers, market=market)
    runs = {run.key: run for run in base_runs}
    if len(runs) != len(params_list):
        raise ValueError("参数键重复：检查网格参数")
    folds = fold_windows(
        start_ms,
        end_ms,
        train_months=config.rolling.train_months,
        folds=config.rolling.folds,
    )
    own_sharpes = {run.key: own_concat(run, folds) for run in base_runs}
    try:
        concat_daily, choices = concatenated_returns(runs, folds, start_ms)
    except ValueError as error:
        return _no_selection_report(
            round_id=round_id,
            family=family,
            exposure=str(grid.get("exposure", "neutral")),
            folds=folds,
            runs=runs,
            own_sharpes=own_sharpes,
            failure=str(error),
            out_dir=out_dir,
            log_path=log_path,
        )
    selected = selected_trades(runs, folds, choices)
    prior = read_trial_sharpes(log_path)
    trial_sharpes = prior + [
        float(series.mean() / series.std(ddof=1))
        for series in own_sharpes.values()
        if series.size > 2 and series.std(ddof=1) > 0.0
    ]
    net_p95 = max((runs[choice.params_key].net_exposure_p95 for choice in choices), default=0.0)
    result = _synthetic_result(
        concat_daily, selected, initial_equity=sizing.initial_equity, net_exposure_p95=net_p95
    )
    metrics = summarize(result, bootstrap=config.bootstrap, trial_sharpes=trial_sharpes)
    scenario_runs: dict[tuple[str, str], ParamRun] = {}
    unique_keys = sorted({choice.params_key for choice in choices})
    if scenarios:
        scenario_tasks = [
            {
                "family": family,
                "params": runs[key].params,
                "options": {
                    "name": name,
                    "cost_mult": SCENARIO_OPTIONS[name].cost_mult,
                    "no_halt": SCENARIO_OPTIONS[name].no_halt,
                    "delay_rebalances": SCENARIO_OPTIONS[name].delay_rebalances,
                    "shuffle": SCENARIO_OPTIONS[name].shuffle,
                    "long_only": SCENARIO_OPTIONS[name].long_only,
                    "short_only": SCENARIO_OPTIONS[name].short_only,
                },
            }
            for name in scenarios
            for key in unique_keys
        ]
        scenario_results = _run_tasks(
            scenario_tasks, payload=payload, workers=workers, market=market
        )
        index = 0
        for name in scenarios:
            for key in unique_keys:
                scenario_runs[(name, key)] = scenario_results[index]
                index += 1
    return _build_report(
        round_id=round_id,
        family=family,
        exposure=str(grid.get("exposure", "neutral")),
        config=config,
        folds=folds,
        runs=runs,
        own_sharpes=own_sharpes,
        choices=choices,
        concat_daily=concat_daily,
        metrics=metrics,
        scenario_runs=scenario_runs,
        scenarios=scenarios,
        out_dir=out_dir,
        log_path=log_path,
    )


def _fold_stats(
    folds: list[Fold],
    choices: list[FoldChoice],
    concat_daily: pd.Series,
    runs: Mapping[str, ParamRun],
) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for fold, choice in zip(folds, choices, strict=True):
        piece = _slice(concat_daily, fold.start_ms, fold.end_ms)
        fold_trades = [
            trade
            for trade in runs[choice.params_key].trades
            if fold.start_ms <= trade.entry_ts < fold.end_ms
        ]
        net = float((1.0 + piece).prod() - 1.0) if piece.size else 0.0
        stats.append(
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
                "params": runs[choice.params_key].params,
                "train_sharpe": choice.train_sharpe,
                "train_trades": choice.train_trades,
                "net_return": net,
                "trades": len(fold_trades),
                "long_gross": sum(t.gross_pnl for t in fold_trades if t.side > 0),
                "short_gross": sum(t.gross_pnl for t in fold_trades if t.side < 0),
            }
        )
    return stats


def _scenario_metrics(
    scenario_runs: Mapping[tuple[str, str], ParamRun],
    folds: list[Fold],
    choices: list[FoldChoice],
    scenarios: Sequence[str],
) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    for name in scenarios:
        daily, trades = _scenario_concat(scenario_runs, folds, choices, name)
        equity = (1.0 + daily).cumprod()
        std = float(daily.std(ddof=1)) if daily.size > 1 else 0.0
        drawdown = (
            float(((equity.cummax() - equity) / equity.cummax()).max()) if equity.size else 0.0
        )
        metrics[name] = {
            "annualized_sharpe": annualized_sharpe(daily),
            "daily_sharpe": float(daily.mean() / std) if std > 0.0 else 0.0,
            "net_return": float(equity.iloc[-1] - 1.0) if equity.size else 0.0,
            "max_drawdown": drawdown,
            "trades": float(len(trades)),
            "net_pnl": float(sum(trade.net_pnl for trade in trades)),
        }
    return metrics


def _no_selection_report(
    *,
    round_id: str,
    family: str,
    exposure: str,
    folds: list[Fold],
    runs: Mapping[str, ParamRun],
    own_sharpes: Mapping[str, pd.Series],
    failure: str,
    out_dir: Path,
    log_path: Path,
) -> dict[str, Any]:
    """没有参数满足折内选参条件时的报告（仍登记全部试验）。"""
    now = datetime.now(UTC).isoformat()
    param_rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for run in runs.values():
        series = own_sharpes[run.key]
        std = float(series.std(ddof=1)) if series.size > 1 else 0.0
        daily_sharpe = float(series.mean() / std) if std > 0.0 else 0.0
        param_rows.append(
            {
                "key": run.key,
                "params": run.params,
                "own_concat_daily_sharpe": daily_sharpe,
                "own_concat_annualized_sharpe": annualized_sharpe(series),
                "full_period_daily_sharpe": float(run.params_meta.get("daily_sharpe", 0.0)),
                "full_trades": len(run.trades),
                "selected_in_folds": 0,
                "turnover": run.turnover,
                "dropped": run.dropped,
                "forced": run.forced,
                "cost_total": run.cost_total,
                "funding_total": run.funding_total,
                "risk_events": run.risk_events,
            }
        )
        entries.append(
            {
                "ts": now,
                "kind": "tuning",
                "round": round_id,
                "family": family,
                "params": run.params,
                "daily_sharpe": daily_sharpe,
                "annualized_sharpe": annualized_sharpe(series),
                "trades": len(run.trades),
                "selected_in_folds": 0,
            }
        )
    report: dict[str, Any] = {
        "round": round_id,
        "family": family,
        "exposure": exposure,
        "generated_at": now,
        "folds": [
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
            }
            for fold in folds
        ],
        "params": param_rows,
        "fold_stats": [],
        "selected_params": {},
        "selected_metrics": {},
        "scenarios": {},
        "checks": {},
        "passed": False,
        "failure": failure,
    }
    target_dir = out_dir / round_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "grid.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    append_experiments(log_path, entries)
    return report


def _build_report(
    *,
    round_id: str,
    family: str,
    exposure: str,
    config: ResearchConfig,
    folds: list[Fold],
    runs: Mapping[str, ParamRun],
    own_sharpes: Mapping[str, pd.Series],
    choices: list[FoldChoice],
    concat_daily: pd.Series,
    metrics: dict[str, object],
    scenario_runs: Mapping[tuple[str, str], ParamRun],
    scenarios: Sequence[str],
    out_dir: Path,
    log_path: Path,
) -> dict[str, Any]:
    """汇总折、情景与达标项，写报告并登记试验（docs/13 §9）。"""
    fold_stats = _fold_stats(folds, choices, concat_daily, runs)
    scenario_stats = (
        _scenario_metrics(scenario_runs, folds, choices, scenarios) if scenarios else {}
    )
    base_sharpe = float(metrics["annualized_sharpe"])
    checks: dict[str, bool] = {
        "sharpe_ge_1": base_sharpe >= 1.0,
        "folds_positive_ge_4": sum(1 for item in fold_stats if item["net_return"] > 0.0) >= 4,
        "bootstrap_lower_gt_0": float(metrics["bootstrap"]["lower_bp"]) > 0.0,
        "dsr_ge_080": float(metrics["deflated_sharpe"]) >= 0.80,
        "cost2x_positive": scenario_stats.get("cost2x", {}).get("net_return", -1.0) > 0.0,
        "mdd_le_020": float(metrics["max_drawdown"]) <= 0.20,
        "shuffle_le_03": scenario_stats.get("shuffle", {}).get("annualized_sharpe", 1.0) <= 0.3,
        "delay1_lt_base": scenario_stats.get("delay1", {}).get("annualized_sharpe", 1.0)
        < base_sharpe,
    }
    if exposure == "neutral":
        checks["side_condition"] = (
            sum(1 for item in fold_stats if item["long_gross"] - item["short_gross"] > 0.0) >= 4
        )
    else:
        checks["side_condition"] = (
            scenario_stats.get("long_only", {}).get("net_pnl", 0.0) > 0.0
            or scenario_stats.get("short_only", {}).get("net_pnl", 0.0) > 0.0
        )
    passed = all(checks.values())
    selected_keys = {choice.params_key for choice in choices}
    param_rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for run in runs.values():
        series = own_sharpes[run.key]
        daily_sharpe = (
            float(series.mean() / series.std(ddof=1))
            if series.size > 2 and series.std(ddof=1) > 0.0
            else 0.0
        )
        row = {
            "key": run.key,
            "params": run.params,
            "own_concat_daily_sharpe": daily_sharpe,
            "own_concat_annualized_sharpe": annualized_sharpe(series),
            "full_period_daily_sharpe": float(run.params_meta.get("daily_sharpe", 0.0)),
            "full_trades": len(run.trades),
            "selected_in_folds": sum(1 for choice in choices if choice.params_key == run.key),
            "turnover": run.turnover,
            "dropped": run.dropped,
            "forced": run.forced,
            "cost_total": run.cost_total,
            "funding_total": run.funding_total,
            "risk_events": run.risk_events,
        }
        param_rows.append(row)
        entries.append(
            {
                "ts": now,
                "kind": "tuning",
                "round": round_id,
                "family": family,
                "params": run.params,
                "daily_sharpe": daily_sharpe,
                "annualized_sharpe": row["own_concat_annualized_sharpe"],
                "trades": len(run.trades),
                "selected_in_folds": row["selected_in_folds"],
            }
        )
    report: dict[str, Any] = {
        "round": round_id,
        "family": family,
        "exposure": exposure,
        "generated_at": now,
        "folds": [
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
            }
            for fold in folds
        ],
        "params": param_rows,
        "fold_stats": fold_stats,
        "selected_params": {
            str(fold.index + 1): choice.params_key
            for fold, choice in zip(folds, choices, strict=True)
        },
        "selected_metrics": metrics,
        "scenarios": scenario_stats,
        "checks": checks,
        "passed": passed,
        "selected_keys": sorted(selected_keys),
    }
    target_dir = out_dir / round_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "grid.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    concat_daily.rename("return").to_frame().to_csv(target_dir / "selected_daily_returns.csv")
    append_experiments(log_path, entries)
    return report
