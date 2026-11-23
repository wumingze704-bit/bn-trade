"""轮动策略第二轮研究：按给定参数组批量回测，并按时间顺序切成开发期 / 验证折窗口。

规格：docs/11-轮动策略.md §7；研究协议见 docs/rotation/decisions.md 第 27~31 条

复用冻结实现（`scripts/rotation_backtest.build_context`、`trade.rotation.engine.run_scenarios`、
`trade.rotation.metrics.compute_metrics`），不重新实现策略，也不修改任何冻结文件。

用法（仓库根目录）：
    python scripts/rotation_grid_scan.py --configs <grid.yaml> --run-id rot_rd2_r1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rotation_backtest as rb  # noqa: E402

from trade.backtest.engine import BacktestConfig  # noqa: E402
from trade.common.config import BaseConfig, load_config  # noqa: E402
from trade.common.sizing import SizingConfig  # noqa: E402
from trade.data.universe import load_aliases, load_universe_config  # noqa: E402
from trade.rotation.data import MS_DAY, month_keys  # noqa: E402
from trade.rotation.engine import SimResult, run_scenarios  # noqa: E402
from trade.rotation.metrics import compute_metrics, eval_segments  # noqa: E402
from trade.rotation.stage import stage_window  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _ms(year: int, month: int) -> int:
    """UTC 某月 1 日 00:00 的毫秒时间戳。"""
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


# 时间窗（decisions.md 第 28 条）：开发期与两个按时间顺序的验证折
WINDOWS: dict[str, tuple[int, int]] = {
    "dev": (_ms(2024, 11), _ms(2025, 10)),
    "v1": (_ms(2025, 10), _ms(2026, 4)),
    "v2": (_ms(2026, 4), _ms(2026, 8)),
}


def build_parser() -> argparse.ArgumentParser:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="轮动策略网格扫描（按时间窗切片）")
    parser.add_argument("--configs", required=True, help="网格 YAML（name + params 列表）")
    parser.add_argument("--run-id", required=True, help="运行标识")
    parser.add_argument("--scenarios", default="base", help="逗号分隔的情景名")
    return parser


def window_values(
    result: SimResult, start_ms: int, end_ms: int, fallback_equity: float
) -> dict[str, Any]:
    """某个时间窗内的指标（复用冻结的 compute_metrics，起点权益取窗口前最后一个日末权益）。

    Args:
        result: 一个情景的模拟结果。
        start_ms: 窗口起点（含）。
        end_ms: 窗口终点（不含）。
        fallback_equity: 窗口前没有日末权益时使用的起点权益。

    Returns:
        指标字典；窗口内没有日末权益时返回空字典。
    """
    daily = [(ts, equity) for ts, equity in result.daily if start_ms <= ts < end_ms]
    if not daily:
        return {}
    prior = [equity for ts, equity in result.daily if ts < start_ms]
    initial = prior[-1] if prior else fallback_equity
    sliced = SimResult(
        scenario=result.scenario,
        trades=[t for t in result.trades if start_ms <= t.exit_ts_ms < end_ms],
        daily=daily,
        final_equity=daily[-1][1],
    )
    months = month_keys(start_ms, end_ms)
    bundle = compute_metrics(
        sliced,
        initial_equity=initial,
        months=months,
        segments=eval_segments(months),
        eval_years=(end_ms - start_ms) / (365.0 * MS_DAY),
    )
    return {
        "net_return": bundle.values["net_return"],
        "sharpe": bundle.values["sharpe"],
        "max_drawdown": bundle.values["max_drawdown"],
        "n_trades": bundle.values["n_trades"],
        "losing_months": bundle.values["losing_months"],
        "worst_month": bundle.values["worst_month"],
        "cost_ratio": bundle.values["cost_ratio"],
        "turnover_annual": bundle.values["turnover_annual"],
        "bootstrap_mean_low": bundle.values["bootstrap_mean_low"],
        "bootstrap_mean_high": bundle.values["bootstrap_mean_high"],
    }


def main(argv: list[str] | None = None) -> int:
    """入口：逐组回测并写出 grid.json（含全窗口与 dev/v1/v2 三窗指标）。"""
    args = build_parser().parse_args(argv)
    base_cfg = load_config(BaseConfig, ROOT / "configs" / "base.yaml")
    backtest_cfg = load_config(BacktestConfig, ROOT / "configs" / "backtest.yaml")
    sizing = load_config(SizingConfig, ROOT / "configs" / "sizing.yaml")
    universe_cfg = load_universe_config(ROOT / "configs" / "universe.yaml")
    artifact_root = Path(base_cfg.paths.artifact_root)
    # 与确认回测同一套阶段守卫（confirm 窗口内，不读封存集）
    window = stage_window("confirm", docs_dir=ROOT / "docs", artifact_root=artifact_root)
    rotation_path = ROOT / "configs" / "rotation_frozen.yaml"
    rules = rb._load_rules(artifact_root / "exchange_info" / "rules.json")
    aliases = load_aliases(Path(base_cfg.paths.data_root) / "universe" / "aliases.json")
    scenarios = [name.strip() for name in str(args.scenarios).split(",") if name.strip()]
    grid = yaml.safe_load(Path(args.configs).read_text(encoding="utf-8"))
    if not isinstance(grid, list) or not grid:
        raise ValueError(f"{args.configs} 必须是 name/params 列表（decisions.md 第 30 条）")

    rows: list[dict[str, Any]] = []
    for item in grid:
        name = str(item["name"])
        params = {str(key): value for key, value in (item.get("params") or {}).items()}
        overrides = [f"{key}={value}" for key, value in params.items()]
        rotation = rb._load_rotation(rotation_path, overrides)
        ctx = rb.build_context(
            window=window,
            rotation=rotation,
            sizing=sizing,
            base_cfg=base_cfg,
            backtest_cfg=backtest_cfg,
            universe_cfg=universe_cfg,
            rules=rules,
            aliases=aliases,
            scenario_names=scenarios,
        )
        results = run_scenarios(ctx, scenarios)
        entry: dict[str, Any] = {"name": name, "params": params, "scenarios": {}}
        for scenario in scenarios:
            result = results[scenario]
            entry["scenarios"][scenario] = {
                "full": window_values(
                    result, ctx.eval_start_ms, ctx.eval_end_ms, ctx.initial_equity
                ),
                "windows": {
                    key: window_values(result, start, end, ctx.initial_equity)
                    for key, (start, end) in WINDOWS.items()
                },
            }
        rows.append(entry)
        base = entry["scenarios"][scenarios[0]]["windows"]
        rb._append_experiment(
            artifact_root / "reports" / "EXPERIMENTS.log",
            {
                "ts": datetime.now(tz=UTC).isoformat(),
                "run_id": f"{args.run_id}__{name}",
                "kind": "tuning",
                "stage": "research_rd2",
                "config_hash": json.dumps(
                    {"rotation": rotation.model_dump(), "params": params},
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                "sharpe": entry["scenarios"][scenarios[0]]["full"]["sharpe"],
                "net_return_pct": entry["scenarios"][scenarios[0]]["full"]["net_return"],
                "v1_net_return": base["v1"]["net_return"],
                "v2_net_return": base["v2"]["net_return"],
                "failed": False,
            },
        )
        print(
            json.dumps(
                {
                    "name": name,
                    "params": params,
                    "full_net": round(base_full(entry, scenarios[0]), 4),
                    "v1_net": round(base["v1"]["net_return"], 4),
                    "v2_net": round(base["v2"]["net_return"], 4),
                    "v1_sharpe": round(base["v1"]["sharpe"], 3),
                    "v2_sharpe": round(base["v2"]["sharpe"], 3),
                },
                ensure_ascii=True,
            )
        )

    run_dir = artifact_root / "reports" / "rotation" / str(args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "grid.json").write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "scenarios": scenarios,
                "windows": {key: list(value) for key, value in WINDOWS.items()},
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"run_id": args.run_id, "grid": str(run_dir / "grid.json")}))
    return 0


def base_full(entry: dict[str, Any], scenario: str) -> float:
    """取某组某情景在完整 confirm 窗口的净收益。"""
    return float(entry["scenarios"][scenario]["full"]["net_return"])


if __name__ == "__main__":
    raise SystemExit(main())
