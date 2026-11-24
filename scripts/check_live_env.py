"""检查实盘引擎与看板的静态环境（不访问币安、不写状态库）。

用法：`python scripts/check_live_env.py --config configs/live.yaml [--candidate <ID>]`

输出：配置是否可加载、交接文件与数据目录是否存在、运行模式与关键阈值。
退出码 0 表示静态检查通过；1 表示有缺失（每项单独列出，不回退默认值）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trade.live.candidate import CandidateError, load_candidate
from trade.live.config import load_live_config
from trade.live.feed import load_universe_history
from trade.live.runner import load_risk_limits, load_strategy
from trade.research.config import load_research_sizing

_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: Path | str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def main() -> None:
    parser = argparse.ArgumentParser(description="实盘引擎静态环境检查")
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--candidate", default=None, help="可选：同时检查候选文件")
    args = parser.parse_args()
    config = load_live_config(_resolve(args.config))
    engine = config.engine
    problems: list[str] = []
    for name, path in (
        ("ledger", engine.ledger),
        ("research", engine.research),
        ("base", engine.base),
        ("backtest", engine.backtest),
        ("sizing_research", engine.sizing_research),
    ):
        if not _resolve(path).is_file():
            problems.append(f"{name} 文件不存在：{_resolve(path)}")
    for name, path in (
        ("klines_1h", engine.klines_1h),
        ("klines_1d", engine.klines_1d),
        ("funding", engine.funding),
        ("universe_hist", engine.universe_hist),
    ):
        if not _resolve(path).is_dir():
            problems.append(f"{name} 目录不存在：{_resolve(path)}")
    try:
        load_universe_history(_resolve(engine.universe_hist))
    except Exception as error:  # noqa: BLE001 - 诊断脚本汇总所有问题
        problems.append(f"选币快照不可用：{error}")
    if args.candidate:
        try:
            spec, _entry = load_candidate(
                root=_ROOT,
                candidates_dir=_resolve(engine.candidates_dir),
                ledger_path=_resolve(engine.ledger),
                candidate_id=args.candidate,
            )
            load_strategy(spec, stop_max=0.5)
            load_risk_limits(
                load_research_sizing(_resolve(engine.sizing_research)).with_tier(),
                "shadow",
            )
        except CandidateError as error:
            problems.append(str(error))
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        raise SystemExit(1)
    print(
        f"[OK] 配置可加载；资金上限 {engine.live_capital_cap} USDT；"
        f"历史回补 {engine.history_days_hourly}/{engine.history_days_daily} 天"
    )


if __name__ == "__main__":
    main()
