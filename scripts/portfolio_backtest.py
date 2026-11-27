"""研究回测命令行：网格评估、情景诊断与试验登记（docs/13 §9）。

示例（本地 Windows）：
    python scripts/portfolio_backtest.py --grid configs/strategies/round_R1.yaml

脚本只写 `artifacts/reports/` 与 `EXPERIMENTS.log`，不写 `docs/research/ledger.yaml`
（1 号第三部分第 5 条：ledger 由研究任务在本地按日志更新）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

from trade.common.config import BaseConfig, load_config
from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.config import ResearchConfig, load_research_config, load_research_sizing
from trade.research.engine import Engine, RunResult, ScenarioOptions
from trade.research.metrics import summarize
from trade.research.pipeline import build_market, evaluate_grid, load_grid
from trade.research.stage import enforce_research, read_ledger
from trade.strategies.registry import build_strategy

_ROOT = Path(__file__).resolve().parents[1]
_MS_PER_DAY = 86_400_000


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _day_ms(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _default_workers() -> int:
    """服务器（Linux）按指令取 1；本地取 min(CPU−1, 4) 控制内存。"""
    if platform.system() == "Linux":
        return 1
    return max(1, min((os.cpu_count() or 2) - 1, 4))


def _risk_limits(config: ResearchConfig, sizing: SizingConfig) -> RiskLimits:
    """由研究配置的 risk 节与（已按档位取值的）仓位配置构造风控参数（docs/13 §2）。"""
    return RiskLimits(
        daily_loss_stop=sizing.daily_loss_stop,
        daily_loss_halt=sizing.daily_loss_halt,
        max_drawdown_halt=sizing.max_drawdown_halt,
        max_consecutive_losses=sizing.max_consecutive_losses,
        consecutive_loss_pause_min=sizing.consecutive_loss_pause_min,
        halt_mode=config.risk.halt_mode,
        halt_reset_days=config.risk.halt_reset_days,
    )


def _shift_months(moment: datetime, months: int) -> datetime:
    """按整月平移（最小规模运行的窗口起点）。"""
    total = moment.year * 12 + (moment.month - 1) + months
    year, month = total // 12, total % 12 + 1
    return datetime(year, month, min(moment.day, 28), tzinfo=UTC)


def _single_run(
    market,
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk: RiskLimits,
    family: str,
    params: dict,
    start_ms: int,
    end_ms: int,
    sizing_mode: str,
) -> RunResult:
    """单策略、单区间的连续运行（最小规模运行使用）。"""
    engine = Engine(
        market=market,
        strategy=build_strategy(family, params, stop_max=config.execution.stop_max),
        sizing_cfg=sizing,
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=fee_taker,
        risk_limits=risk,
        options=ScenarioOptions(),
        seed=config.bootstrap.seed,
        sizing_mode=sizing_mode,
    )
    return engine.run(start_ms, end_ms)


def _smoke(
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk: RiskLimits,
    grid: dict,
    start_ms: int,
    end_ms: int,
    start_month: str,
    end_month: str,
    months: int,
    sizing_mode: str,
) -> int:
    """最小规模运行：1 个方向 × 1 组参数 × `months` 个月，核对两种口径与记录字段。

    结果写入 `artifacts/reports/smoke/<UTC 时间>.json`，不写 `EXPERIMENTS.log`
    （这不是研究期网格评估，不计试验次数）。
    """
    family = str(grid["family"])
    params = dict(grid["params"][0])
    params["sizing_mode"] = sizing_mode
    window_start_ms = int(
        _shift_months(datetime.fromtimestamp(end_ms // 1000, tz=UTC), -months).timestamp() * 1000
    )
    market = build_market(
        config,
        start_ms=start_ms,
        end_ms=end_ms,
        start_month=start_month,
        end_month=end_month,
    )
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "family": family,
        "params": params,
        "sizing_mode": sizing_mode,
        "window": {"start_ms": window_start_ms, "end_ms": end_ms, "months": months},
        "signal_basis": config.equity.signal,
        "exec_basis": config.equity.exec,
    }
    records: list[dict[str, object]] = []
    for label, basis in (("signal", config.equity.signal), ("exec", config.equity.exec)):
        basis_sizing = sizing.model_copy(update={"initial_equity": basis})
        result = _single_run(
            market,
            config=config,
            sizing=basis_sizing,
            fee_taker=fee_taker,
            risk=risk,
            family=family,
            params=params,
            start_ms=window_start_ms,
            end_ms=end_ms,
            sizing_mode=sizing_mode,
        )
        metrics = summarize(result, bootstrap=config.bootstrap, trial_sharpes=[])
        report[f"{label}_metrics"] = {
            "annualized_sharpe": metrics["annualized_sharpe"],
            "daily_sharpe": metrics["daily_sharpe"],
            "max_drawdown": metrics["max_drawdown"],
            "trades": metrics["trades"],
            "dropped": metrics["dropped"],
            "dropped_min_notional": metrics["dropped_min_notional"],
            "attempted": metrics["attempted"],
            "funding_estimated": metrics["funding_estimated"],
            "funding_missing": metrics["funding_missing"],
            "mark_fallback": metrics["mark_fallback"],
            "cost_total": metrics["cost_total"],
            "funding_total": metrics["funding_total"],
            "long_gross": metrics["long_gross"],
            "short_gross": metrics["short_gross"],
        }
        print(
            f"[{sizing_mode}] {label}: 交易 {metrics['trades']}，应开仓 {metrics['attempted']}，"
            f"最小下单额丢弃 {metrics['dropped_min_notional']}，"
            f"资金费估算 {metrics['funding_estimated']} / 中位数 {metrics['funding_missing']}，"
            f"标记价退回 {metrics['mark_fallback']}，实现 β {metrics['realized_beta']:.3f}，"
            f"alpha 夏普 {metrics['alpha_sharpe']:.3f}"
        )
        records.append(
            {
                "kind": "exec" if label == "exec" else "tuning",
                "family": family,
                "params": params,
                "daily_sharpe": metrics["daily_sharpe"],
                "annualized_sharpe": metrics["annualized_sharpe"],
                "trades": metrics["trades"],
                "equity_basis": basis,
            }
        )
    report["record_fields"] = records
    out_dir = _ROOT / config.paths.artifacts / "reports" / "smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"{stamp}.json"
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"写入 {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """portfolio_backtest.py 入口。"""
    parser = argparse.ArgumentParser(description="研究网格回测（docs/13 §9）")
    parser.add_argument(
        "--grid", required=True, help="网格 YAML（configs/strategies/round_R1.yaml）"
    )
    parser.add_argument("--round", default=None, help="轮次 ID，默认取网格配置")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--research", default="configs/research.yaml")
    parser.add_argument("--sizing", default="configs/sizing_research.yaml")
    parser.add_argument(
        "--ledger", default="docs/research/ledger.yaml", help="只读取研究区间，脚本不写 ledger"
    )
    parser.add_argument("--workers", type=int, default=None, help="并行进程数，默认按平台")
    parser.add_argument("--no-scenarios", action="store_true", help="跳过压力情景（调试用）")
    parser.add_argument("--with-flow", action="store_true", help="加载主动买入成交数据")
    parser.add_argument("--with-premium", action="store_true", help="加载溢价指数数据")
    parser.add_argument(
        "--smoke-months",
        type=int,
        default=0,
        help="最小规模运行：只用 1 组参数跑最近 N 个月，核对两种口径与记录字段",
    )
    parser.add_argument(
        "--smoke-sizing-mode",
        default=None,
        choices=("fixed_risk", "vol_target"),
        help="最小规模运行的仓位模式（模式 A / B 各跑一次时分别指定）",
    )
    args = parser.parse_args(argv)

    base = load_config(BaseConfig, _resolve(args.base))
    config = load_research_config(_resolve(args.research))
    research_sizing = load_research_sizing(_resolve(args.sizing))
    sizing = research_sizing.with_tier()
    grid_path = _resolve(args.grid)
    grid = load_grid(grid_path)
    # 网格参数里的 sizing_mode 是仓位模式维度；未写明时取配置默认值（13 §3）。
    default_mode = research_sizing.sizing_mode
    grid = {
        **grid,
        "params": [
            {**dict(params), "sizing_mode": str(params.get("sizing_mode", default_mode))}
            for params in grid["params"]
        ],
    }
    round_id = str(args.round or grid.get("round") or grid_path.stem)
    ledger_path = _resolve(args.ledger)
    ledger = read_ledger(ledger_path)
    research_start = str(ledger["research_start"])
    research_end = str(ledger["research_end"])
    start_ms = _day_ms(research_start)
    end_ms = _day_ms(research_end) + _MS_PER_DAY
    enforce_research(ledger_path=ledger_path, start_ms=start_ms, end_ms=end_ms)
    risk = _risk_limits(config, sizing)
    if args.smoke_months:
        return _smoke(
            config=config,
            sizing=sizing,
            fee_taker=base.fees.taker,
            risk=risk,
            grid=grid,
            start_ms=start_ms,
            end_ms=end_ms,
            start_month=research_start[:7],
            end_month=research_end[:7],
            months=args.smoke_months,
            sizing_mode=args.smoke_sizing_mode or research_sizing.sizing_mode,
        )
    workers = args.workers if args.workers is not None else _default_workers()
    out_dir = _ROOT / config.paths.artifacts / "reports"
    log_path = _ROOT / config.paths.experiments_log
    scenarios = () if args.no_scenarios else tuple(config.scenarios)
    # 可执行性检查：四个档位各跑一次 500 USDT 拼接路径（docs/13 §7 第 9 条）
    exec_tiers: dict[str, tuple[SizingConfig, RiskLimits]] = {}
    for tier_name in ("standard", "progressive", "aggressive", "extreme"):
        tier_cfg = research_sizing.with_tier(tier_name)
        exec_tiers[tier_name] = (
            tier_cfg.model_copy(update={"initial_equity": config.equity.exec}),
            _risk_limits(config, tier_cfg),
        )

    report = evaluate_grid(
        config=config,
        sizing=sizing,
        fee_taker=base.fees.taker,
        risk_limits=risk,
        grid=grid,
        round_id=round_id,
        start_ms=start_ms,
        end_ms=end_ms,
        start_month=research_start[:7],
        end_month=research_end[:7],
        workers=workers,
        out_dir=out_dir,
        log_path=log_path,
        grid_path=grid_path,
        scenarios=scenarios,
        with_flow=args.with_flow,
        with_premium=args.with_premium,
        exec_tiers=exec_tiers,
    )
    print(f"轮次 {round_id}：{len(grid['params'])} 组参数，试验已登记（kind=tuning）")
    print("ledger 未修改：按 EXPERIMENTS.log 的 kind=tuning 条数在本地更新 trials_used")
    print(
        f"达标：{report['passed']}；未通过项：{[k for k, v in report['checks'].items() if not v]}"
    )
    metrics = report.get("selected_metrics") or {}
    if metrics:
        print(f"拼接夏普（信号口径）：{metrics['annualized_sharpe']:.3f}")
        tiers = report.get("exec_tiers") or {}
        for name, stats in tiers.items():
            print(
                f"可执行口径 [{name}]：夏普 {stats['annualized_sharpe']:.3f}，"
                f"复合月化 {stats['compound_monthly']:.2%}，"
                f"丢弃 {stats['dropped_min_notional']:.0f}/{stats['attempted']:.0f}"
            )
        print(f"满足可执行性检查的档位：{report.get('qualified_exec_tiers')}")
    else:
        print(f"无法选参：{report.get('failure', '未知原因')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
