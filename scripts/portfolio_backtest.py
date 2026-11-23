"""研究回测命令行：网格评估、情景诊断与试验登记（docs/13 §9）。

示例（本地 Windows）：
    python scripts/portfolio_backtest.py --grid configs/strategies/round_R1.yaml
"""

from __future__ import annotations

import argparse
import os
import platform
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from trade.common.config import BaseConfig, load_config
from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.config import load_research_config
from trade.research.pipeline import evaluate_grid, load_grid
from trade.research.stage import enforce_research, read_ledger

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


def _risk_limits(base: BaseConfig, sizing: SizingConfig, backtest_path: Path) -> RiskLimits:
    values = yaml.safe_load(backtest_path.read_text(encoding="utf-8"))
    risk = values.get("risk") if isinstance(values, dict) else None
    if not isinstance(risk, dict):
        raise ValueError(f"backtest 配置缺少 risk 段：{backtest_path}")
    return RiskLimits(
        daily_loss_stop=sizing.daily_loss_stop,
        daily_loss_halt=sizing.daily_loss_halt,
        max_drawdown_halt=sizing.max_drawdown_halt,
        max_consecutive_losses=sizing.max_consecutive_losses,
        consecutive_loss_pause_min=sizing.consecutive_loss_pause_min,
        halt_mode=str(risk["halt_mode"]),
        halt_reset_days=int(risk["halt_reset_days"]),
    )


def _update_ledger(path: Path, round_id: str, trials: int) -> None:
    """累加 trials_used 与轮次 trials，并把 pending 轮次标为 researching。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^trials_used:\s*(\d+)", text, flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"ledger 缺少 trials_used：{path}")
    total = int(match.group(1)) + trials
    text = re.sub(
        r"^trials_used:\s*\d+", f"trials_used: {total}", text, count=1, flags=re.MULTILINE
    )
    line_re = re.compile(rf"^(  - \{{id: {re.escape(round_id)},[^}}]*)\}}$", re.MULTILINE)
    line_match = line_re.search(text)
    if line_match is None:
        raise ValueError(f"ledger 缺少轮次 {round_id}")
    line = line_match.group(0)
    trials_match = re.search(r"trials:\s*(\d+)", line)
    if trials_match is None:
        raise ValueError(f"轮次 {round_id} 缺少 trials 字段")
    new_trials = int(trials_match.group(1)) + trials
    new_line = re.sub(r"trials:\s*\d+", f"trials: {new_trials}", line, count=1)
    new_line = re.sub(r"status:\s*(pending)", "status: researching", new_line, count=1)
    text = text.replace(line, new_line)
    path.write_text(text, encoding="utf-8")


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
    parser.add_argument("--backtest", default="configs/backtest.yaml")
    parser.add_argument("--ledger", default="docs/research/ledger.yaml")
    parser.add_argument("--workers", type=int, default=None, help="并行进程数，默认按平台")
    parser.add_argument("--no-scenarios", action="store_true", help="跳过压力情景（调试用）")
    args = parser.parse_args(argv)

    base = load_config(BaseConfig, _resolve(args.base))
    config = load_research_config(_resolve(args.research))
    sizing = load_config(SizingConfig, _resolve(args.sizing))
    grid = load_grid(_resolve(args.grid))
    round_id = str(args.round or grid.get("round") or _resolve(args.grid).stem)
    ledger_path = _resolve(args.ledger)
    ledger = read_ledger(ledger_path)
    research_start = str(ledger["research_start"])
    research_end = str(ledger["research_end"])
    start_ms = _day_ms(research_start)
    end_ms = _day_ms(research_end) + _MS_PER_DAY
    enforce_research(ledger_path=ledger_path, start_ms=start_ms, end_ms=end_ms)
    risk = _risk_limits(base, sizing, _resolve(args.backtest))
    workers = args.workers if args.workers is not None else _default_workers()
    out_dir = _ROOT / config.paths.artifacts / "reports"
    log_path = _ROOT / config.paths.experiments_log
    scenarios = () if args.no_scenarios else tuple(config.scenarios)

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
        scenarios=scenarios,
    )
    _update_ledger(ledger_path, round_id, len(grid["params"]))
    print(f"轮次 {round_id}：{len(grid['params'])} 组参数，试验已登记")
    print(
        f"达标：{report['passed']}；未通过项：{[k for k, v in report['checks'].items() if not v]}"
    )
    metrics = report.get("selected_metrics") or {}
    if metrics:
        print(f"拼接夏普：{metrics['annualized_sharpe']:.3f}")
    else:
        print(f"无法选参：{report.get('failure', '未知原因')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
