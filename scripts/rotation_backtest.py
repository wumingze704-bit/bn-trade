"""轮动策略回测脚本（research / confirm / final）。

规格：docs/11-轮动策略.md §7

用法（仓库根目录）：
    python scripts/rotation_backtest.py --stage research --run-id rot_r1_N50_L7_K5 \
        --params ma_days=50 --params lookback_days=7 --params top_k=5 \
        --scenarios base,cost2x --kind tuning
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from trade.backtest.engine import BacktestConfig
from trade.common.config import BaseConfig, load_config
from trade.common.sizing import SizingConfig
from trade.data.loader import load_bars
from trade.data.universe import load_aliases, load_universe_config
from trade.rotation.config import RotationConfig
from trade.rotation.data import (
    MS_HOUR,
    MS_MIN,
    build_hourly_panel,
    hour_grid,
    load_universe_calendar,
    month_keys,
    shift_month,
)
from trade.rotation.engine import (
    CostModel,
    SimContext,
    SymbolRules,
    build_risk_limits,
    run_scenarios,
    scenario_by_name,
)
from trade.rotation.metrics import equity_frame, evaluate, trades_frame
from trade.rotation.signals import build_schedule
from trade.rotation.stage import HOLDOUT_START_MS, StageWindow, check_window, stage_window

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "configs" / "base.yaml"
DEFAULT_BACKTEST = ROOT / "configs" / "backtest.yaml"
DEFAULT_SIZING = ROOT / "configs" / "sizing.yaml"
DEFAULT_UNIVERSE = ROOT / "configs" / "universe.yaml"
DEFAULT_ROTATION = ROOT / "configs" / "rotation.yaml"
DEFAULT_DOCS = ROOT / "docs"


def build_parser() -> argparse.ArgumentParser:
    """命令行参数（stage 上限不可由参数改写，11 §7）。"""
    parser = argparse.ArgumentParser(description="山寨币截面动量轮动回测")
    parser.add_argument("--stage", default="research", choices=["research", "confirm", "final"])
    parser.add_argument("--run-id", default=None, help="运行标识（默认按时间生成）")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--backtest", default=str(DEFAULT_BACKTEST))
    parser.add_argument("--sizing", default=str(DEFAULT_SIZING))
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    parser.add_argument("--rotation", default=str(DEFAULT_ROTATION))
    parser.add_argument("--docs", default=str(DEFAULT_DOCS))
    parser.add_argument(
        "--scenarios",
        default="base",
        help="逗号分隔：base,cost2x,nohalt,delay1,shuffle",
    )
    parser.add_argument("--params", action="append", default=[], help="轮动配置覆盖 key=value")
    parser.add_argument(
        "--sizing-params", action="append", default=[], help="仓位配置覆盖 key=value（第 3 轮用）"
    )
    parser.add_argument("--kind", default="tuning", help="EXPERIMENTS.log 的 kind 字段")
    parser.add_argument("--no-log", action="store_true", help="不写 EXPERIMENTS.log")
    parser.add_argument(
        "--neighbors-from",
        default=None,
        help="phase1.md 路径：按参数网格跑“只差一个参数”的相邻组（base 情景）",
    )
    return parser


def _load_rules(path: Path) -> dict[str, SymbolRules]:
    """读取 exchangeInfo 交易规则快照（09 §3）。"""
    if not path.is_file():
        raise ValueError(f"缺少交易规则快照：{path}（09 §3）")
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules: dict[str, SymbolRules] = {}
    for symbol, item in raw.items():
        if not isinstance(item, dict):
            continue
        rules[str(symbol)] = SymbolRules(
            tick_size=float(item["tick_size"]),
            step_size=float(item["step_size"]),
            min_notional=float(item["min_notional"]),
        )
    return rules


def _symbols_by_month(calendar: dict[str, Any], months: list[str]) -> dict[str, tuple[str, ...]]:
    """每月分钟块需要加载的币：本月与相邻月交易范围的并集。"""
    out: dict[str, tuple[str, ...]] = {}
    for i, month in enumerate(months):
        union: set[str] = set(calendar[month].symbols)
        for j in (i - 1, i + 1):
            if 0 <= j < len(months):
                union |= calendar[months[j]].symbols
        out[month] = tuple(sorted(union))
    return out


def _decision_times(start_ms: int, end_ms: int, step_ms: int) -> list[int]:
    """返回 [start_ms, end_ms) 内所有 step_ms 的整倍数时点。"""
    first = start_ms + (-start_ms) % step_ms
    return [int(ts) for ts in np.arange(first, end_ms, step_ms, dtype="int64")]


def _write_parquet(frame: Any, path: Path) -> None:
    """写 parquet（空表也写出 schema）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)


def _append_experiment(path: Path, record: dict[str, Any]) -> None:
    """追加一行 EXPERIMENTS.log（JSON Lines）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_id(args: argparse.Namespace) -> str:
    """运行标识（默认 rot_<stage>_<UTC 时间>）。"""
    if args.run_id:
        return str(args.run_id)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    return f"rot_{args.stage}_{stamp}"


def build_context(
    *,
    window: StageWindow,
    rotation: RotationConfig,
    sizing: SizingConfig,
    base_cfg: BaseConfig,
    backtest_cfg: Any,
    universe_cfg: Any,
    rules: dict[str, SymbolRules],
    aliases: dict[str, str],
    scenario_names: list[str],
) -> SimContext:
    """准备面板、交易范围与决策表，返回运行上下文（11 §3、§4）。"""
    data_root = Path(base_cfg.paths.data_root)
    months_all = month_keys(window.warmup_start_ms, window.eval_end_ms)
    eval_months = month_keys(window.eval_start_ms, window.eval_end_ms)
    # 交易范围快照从评估起点的上一个月开始读（新进入延迟需要上月成员，11 §4）
    first_year, first_month = (int(part) for part in eval_months[0].split("-"))
    prev_year, prev_month = shift_month(first_year, first_month, -1)
    calendar_months = [
        month for month in months_all if month >= f"{prev_year:04d}-{prev_month:02d}"
    ]
    calendar = load_universe_calendar(
        data_root=data_root,
        universe_cfg=universe_cfg,
        months=calendar_months,
    )
    chunk_symbols = _symbols_by_month(calendar, eval_months)
    symbols = tuple(sorted({symbol for month in eval_months for symbol in chunk_symbols[month]}))
    hours = hour_grid(window.warmup_start_ms, window.eval_end_ms)
    panel = build_hourly_panel(
        data_root=data_root,
        symbols=symbols,
        aliases=aliases,
        btc_symbol=rotation.btc_symbol,
        btc_alias="BTCUSDT",
        hours=hours,
        months=months_all,
        holdout_start_ms=HOLDOUT_START_MS,
        allow_holdout=window.allow_holdout,
        atr_period=rotation.stop_atr_period,
    )
    costs = CostModel(
        fee_taker=float(base_cfg.fees.taker),
        half_spread_min=float(backtest_cfg.costs.half_spread_min),
        slip_min=float(backtest_cfg.costs.slip_min),
        impact_y=float(backtest_cfg.costs.Y),
        notional_ref=float(sizing.initial_equity * sizing.position_frac * sizing.leverage),
    )
    rebalance_ms = rotation.rebalance_hours * MS_HOUR
    decision_times = _decision_times(window.eval_start_ms, window.eval_end_ms, rebalance_ms)
    schedules: dict[str, dict[str, Any]] = {}
    exit_schedules: dict[str, dict[str, Any]] = {}
    for name in scenario_names:
        scenario = scenario_by_name(name)
        lag = rotation.score_lag_hours + scenario.decision_lag_steps * rotation.rebalance_hours
        schedules[name] = build_schedule(
            panel,
            timestamps=decision_times,
            universe_calendar=calendar,
            cfg=rotation,
            lag_hours=lag,
            shuffle_seed=scenario.shuffle_seed,
        )
        if rotation.exit_check_hours:
            exit_times = [
                ts
                for ts in _decision_times(
                    window.eval_start_ms, window.eval_end_ms, rotation.exit_check_hours * MS_HOUR
                )
                if ts % rebalance_ms != 0
            ]
            exit_schedules[name] = build_schedule(
                panel,
                timestamps=exit_times,
                universe_calendar=calendar,
                cfg=rotation,
                lag_hours=lag,
                shuffle_seed=scenario.shuffle_seed,
            )
    return SimContext(
        panel=panel,
        calendar=calendar,
        cfg=rotation,
        sizing=sizing,
        limits=build_risk_limits(
            sizing=sizing,
            halt_mode=str(backtest_cfg.risk.halt_mode),
            halt_reset_days=int(backtest_cfg.risk.halt_reset_days),
        ),
        costs=costs,
        rules=rules,
        aliases=aliases,
        data_root=data_root,
        holdout_start_ms=int(_holdout_start()),
        allow_holdout=window.allow_holdout,
        initial_equity=float(sizing.initial_equity),
        months=eval_months,
        chunk_symbols=chunk_symbols,
        eval_start_ms=window.eval_start_ms,
        eval_end_ms=window.eval_end_ms,
        schedules=schedules,
        exit_schedules=exit_schedules,
    )


def _holdout_start() -> int:
    """封存集起点（M23 起点，11 §7）。"""
    return HOLDOUT_START_MS


def write_run(
    *,
    run_dir: Path,
    run_id: str,
    stage: str,
    window: StageWindow,
    rotation: RotationConfig,
    sizing: SizingConfig,
    bundles: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    """写出 summary.json / trades.parquet / equity.parquet / report.md（11 §6）。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "eval_start_ms": window.eval_start_ms,
        "eval_end_ms": window.eval_end_ms,
        "rotation": rotation.model_dump(),
        "sizing": sizing.model_dump(),
        "scenarios": {
            name: {
                "values": bundle.values,
                "monthly": bundle.monthly,
                "quarters": bundle.quarters,
                "segments": bundle.segments,
                "exit_reasons": bundle.exit_reasons,
                "holding_days_quantiles": bundle.holding_days_quantiles,
                "skipped": bundle.skipped,
                "risk_events": bundle.risk_events,
            }
            for name, bundle in bundles.items()
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    trade_frames = []
    equity_frames = []
    for name, result in results.items():
        frame = trades_frame(result)
        frame.insert(0, "scenario", name)
        trade_frames.append(frame)
        curve = equity_frame(result, float(sizing.initial_equity))
        curve.insert(0, "scenario", name)
        equity_frames.append(curve)
    _write_parquet(_concat(trade_frames), run_dir / "trades.parquet")
    _write_parquet(_concat(equity_frames), run_dir / "equity.parquet")
    (run_dir / "report.md").write_text(
        _report_markdown(run_id, stage, rotation, sizing, bundles), encoding="utf-8"
    )
    return summary


def _concat(frames: list[Any]) -> Any:
    """拼接 DataFrame（全部为空时返回空表）。"""
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return frames[0] if frames else pd.DataFrame()
    if len(usable) == 1:
        return usable[0]
    return pd.concat(usable, ignore_index=True)


def _report_markdown(
    run_id: str,
    stage: str,
    rotation: RotationConfig,
    sizing: SizingConfig,
    bundles: dict[str, Any],
) -> str:
    """生成 report.md（情景对比 + base 的月度/季度表）。"""
    lines = [
        f"# 轮动回测 {run_id}（stage={stage}）",
        "",
        f"- 参数：N={rotation.ma_days}、L={rotation.lookback_days}、K={rotation.top_k}、"
        f"调仓 {rotation.rebalance_hours}h、k_stop={rotation.stop_atr_mult}、"
        f"position_frac={sizing.position_frac}、risk_per_trade={sizing.risk_per_trade}",
        "",
        "| 情景 | 年化夏普 | 净收益 | 最大回撤 | 交易笔数 | 亏损月数 | 最差月 | 成本占比 |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, bundle in bundles.items():
        values = bundle.values
        lines.append(
            f"| {name} | {values['sharpe']:.3f} | {values['net_return'] * 100:.2f}% | "
            f"{values['max_drawdown'] * 100:.2f}% | {int(values['n_trades'])} | "
            f"{int(values['losing_months'])} | {values['worst_month'] * 100:.2f}% | "
            f"{values['cost_ratio']:.3f} |"
        )
    base = bundles.get("base")
    if base is not None:
        lines += ["", "## base 月收益", "", "| 月份 | 收益 |", "| :--- | ---: |"]
        lines += [f"| {month} | {value * 100:.2f}% |" for month, value in base.monthly.items()]
        lines += ["", "## base 分段（4 段）", ""]
        lines += [f"- {key}: {value * 100:.2f}%" for key, value in base.segments.items()]
        lines += ["", "## base 出场原因", ""]
        lines += [f"- {key}: {value}" for key, value in base.exit_reasons.items()]
        lines += ["", "## base 跳过原因", ""]
        lines += [f"- {key}: {value}" for key, value in base.skipped.items()]
    return "\n".join(lines) + "\n"


def read_neighbor_grid(path: Path) -> dict[str, Any]:
    """从 phase1.md 读取机器可读的参数网格（11 §7 neighbors 情景）。"""
    if not path.is_file():
        raise ValueError(f"缺少参数网格文件：{path}（11 §7）")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```yaml\s*(.*?)```", text, re.DOTALL)
    if match is None:
        raise ValueError(f"{path} 中没有 yaml 代码块的参数网格（11 §7）")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict) or "neighbor_grid" not in payload:
        raise ValueError(f"{path} 的 yaml 代码块缺少 neighbor_grid（11 §7）")
    grid = payload["neighbor_grid"]
    if "frozen" not in grid or "axes" not in grid:
        raise ValueError(f"{path} 的 neighbor_grid 必须含 frozen 与 axes（11 §7）")
    return dict(grid)


def neighbor_variants(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """按“只差一个参数”生成相邻组。"""
    frozen = {key: grid["frozen"][key] for key in grid["axes"]}
    variants: list[dict[str, Any]] = []
    for key, values in grid["axes"].items():
        for value in values:
            if value == frozen[key]:
                continue
            variant = dict(frozen)
            variant[key] = value
            variants.append(variant)
    return variants


def _load_rotation(path: Path, overrides: list[str]) -> RotationConfig:
    """加载 rotation.yaml 并按 key=value 覆盖（参数搜索用）。"""
    return load_config(RotationConfig, path, overrides=overrides)


def _probe_holdout_read(base_cfg: BaseConfig, window: StageWindow) -> None:
    """final 阶段：用官方 load_bars 显式声明 allow_holdout=True（11 §7）。"""
    if not window.allow_holdout:
        return
    load_bars(
        Path(base_cfg.paths.data_root),
        "BTCUSDT",
        HOLDOUT_START_MS,
        HOLDOUT_START_MS + MS_MIN,
        holdout_start_ms=HOLDOUT_START_MS,
        allow_holdout=True,
        columns=("open_time", "close"),
    )


def _metrics_for(results: dict[str, Any], ctx: SimContext) -> dict[str, Any]:
    """把每个情景的 SimResult 换算成指标。"""
    eval_months = month_keys(ctx.eval_start_ms, ctx.eval_end_ms)
    return {
        name: evaluate(
            result,
            initial_equity=ctx.initial_equity,
            months=eval_months,
            eval_start_ms=ctx.eval_start_ms,
            eval_end_ms=ctx.eval_end_ms,
        )
        for name, result in results.items()
    }


def main(argv: list[str] | None = None) -> int:
    """脚本入口：研究/确认/封存三个阶段共用同一份代码（11 §7）。"""
    args = build_parser().parse_args(argv)
    base_cfg = load_config(BaseConfig, Path(args.base))
    backtest_cfg = load_config(BacktestConfig, Path(args.backtest))
    sizing = load_config(SizingConfig, Path(args.sizing), overrides=list(args.sizing_params))
    universe_cfg = load_universe_config(Path(args.universe))
    artifact_root = Path(base_cfg.paths.artifact_root)
    window = stage_window(args.stage, docs_dir=Path(args.docs), artifact_root=artifact_root)
    check_window(window, window.eval_start_ms, window.eval_end_ms)
    _probe_holdout_read(base_cfg, window)
    rotation = _load_rotation(Path(args.rotation), list(args.params))
    rules = _load_rules(artifact_root / "exchange_info" / "rules.json")
    aliases = load_aliases(Path(base_cfg.paths.data_root) / "universe" / "aliases.json")
    run_id = _run_id(args)
    if args.neighbors_from:
        return _run_neighbors(
            args=args,
            base_cfg=base_cfg,
            backtest_cfg=backtest_cfg,
            sizing=sizing,
            universe_cfg=universe_cfg,
            window=window,
            rotation=rotation,
            rules=rules,
            aliases=aliases,
            run_id=run_id,
        )
    names = [name.strip() for name in str(args.scenarios).split(",") if name.strip()]
    ctx = build_context(
        window=window,
        rotation=rotation,
        sizing=sizing,
        base_cfg=base_cfg,
        backtest_cfg=backtest_cfg,
        universe_cfg=universe_cfg,
        rules=rules,
        aliases=aliases,
        scenario_names=names,
    )
    results = run_scenarios(ctx, names)
    bundles = _metrics_for(results, ctx)
    run_dir = Path(base_cfg.paths.artifact_root) / "reports" / "rotation" / run_id
    summary = write_run(
        run_dir=run_dir,
        run_id=run_id,
        stage=args.stage,
        window=window,
        rotation=rotation,
        sizing=sizing,
        bundles=bundles,
        results=results,
    )
    _log_experiment(
        args=args,
        base_cfg=base_cfg,
        run_id=run_id,
        rotation=rotation,
        sizing=sizing,
        bundles=bundles,
    )
    print(json.dumps(_console_summary(run_id, summary), ensure_ascii=False))
    return 0


def _console_summary(run_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """精简的控制台输出（脚本最终结果，允许 print）。"""
    return {
        "run_id": run_id,
        "scenarios": {
            name: {
                "sharpe": round(float(payload["values"]["sharpe"]), 4),
                "net_return": round(float(payload["values"]["net_return"]), 4),
                "max_drawdown": round(float(payload["values"]["max_drawdown"]), 4),
                "n_trades": int(payload["values"]["n_trades"]),
                "positive_segments": int(payload["values"]["positive_segments"]),
            }
            for name, payload in summary["scenarios"].items()
        },
    }


def _log_experiment(
    *,
    args: argparse.Namespace,
    base_cfg: BaseConfig,
    run_id: str,
    rotation: RotationConfig,
    sizing: SizingConfig,
    bundles: dict[str, Any],
) -> None:
    """追加 EXPERIMENTS.log（06 §7；测试段回测必须登记）。"""
    if args.no_log:
        return
    base_values = bundles["base"].values if "base" in bundles else {}
    cost_values = bundles["cost2x"].values if "cost2x" in bundles else {}
    record = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "run_id": run_id,
        "kind": args.kind,
        "stage": args.stage,
        "config_hash": json.dumps(
            {
                "rotation": rotation.model_dump(),
                "sizing": {
                    "mode": sizing.mode,
                    "position_frac": sizing.position_frac,
                    "risk_per_trade": sizing.risk_per_trade,
                    "leverage": sizing.leverage,
                    "max_positions": sizing.max_positions,
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        ),
        "sharpe": float(base_values.get("sharpe", float("nan"))),
        "net_return_pct": float(base_values.get("net_return", float("nan"))),
        "max_drawdown": float(base_values.get("max_drawdown", float("nan"))),
        "n_trades": float(base_values.get("n_trades", 0.0)),
        "cost2x_net_return_pct": float(cost_values.get("net_return", float("nan"))),
        "failed": False,
    }
    _append_experiment(Path(base_cfg.paths.artifact_root) / "reports" / "EXPERIMENTS.log", record)


def _run_neighbors(
    *,
    args: argparse.Namespace,
    base_cfg: BaseConfig,
    backtest_cfg: Any,
    sizing: SizingConfig,
    universe_cfg: Any,
    window: StageWindow,
    rotation: RotationConfig,
    rules: dict[str, SymbolRules],
    aliases: dict[str, str],
    run_id: str,
) -> int:
    """neighbors 情景：把 phase1.md 网格中只差一个参数的相邻组各跑一次 base（11 §7）。"""
    grid = read_neighbor_grid(Path(str(args.neighbors_from)))
    variants = neighbor_variants(grid)
    run_dir = Path(base_cfg.paths.artifact_root) / "reports" / "rotation" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for variant in variants:
        overrides = [f"{key}={value}" for key, value in variant.items()]
        cfg = _load_rotation(Path(args.rotation), overrides)
        ctx = build_context(
            window=window,
            rotation=cfg,
            sizing=sizing,
            base_cfg=base_cfg,
            backtest_cfg=backtest_cfg,
            universe_cfg=universe_cfg,
            rules=rules,
            aliases=aliases,
            scenario_names=["base"],
        )
        bundle = _metrics_for(run_scenarios(ctx, ["base"]), ctx)["base"]
        rows.append(
            {
                "variant": variant,
                "sharpe": bundle.values["sharpe"],
                "net_return": bundle.values["net_return"],
                "max_drawdown": bundle.values["max_drawdown"],
                "n_trades": bundle.values["n_trades"],
            }
        )
    (run_dir / "neighbors.json").write_text(
        json.dumps(
            {"frozen": rotation.model_dump(), "neighbors": rows}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(json.dumps({"run_id": run_id, "neighbors": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
