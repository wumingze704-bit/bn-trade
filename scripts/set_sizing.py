"""运行中控制：暂停开仓、恢复开仓、请求全部平仓、调整 risk_per_trade。

规格：docs/07-实盘与风控.md §6.8

用法（服务器）：
    python scripts/set_sizing.py --candidate <候选ID> --show
    python scripts/set_sizing.py --candidate <候选ID> --risk-per-trade 0.0075
    python scripts/set_sizing.py --candidate <候选ID> --pause-open
    python scripts/set_sizing.py --candidate <候选ID> --resume-open
    python scripts/set_sizing.py --candidate <候选ID> --flatten

`risk_per_trade` 只允许 [0.005, 0.01]，只影响运行中的参数，不写回配置。
不提供清除停机标志与切换运行模式的功能。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from trade.live.control import (
    ControlError,
    control_state,
    request_flatten,
    set_pause_open,
    set_risk_per_trade,
)
from trade.live.store import Store

_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def main() -> None:
    parser = argparse.ArgumentParser(description="运行中控制")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--risk-per-trade", type=float, default=None)
    parser.add_argument("--pause-open", action="store_true")
    parser.add_argument("--resume-open", action="store_true")
    parser.add_argument("--flatten", action="store_true")
    args = parser.parse_args()
    values = yaml.safe_load(_resolve(args.config).read_text(encoding="utf-8"))
    db_path = Path(str(values.get("storage", {}).get("db_path", "")))
    if not db_path:
        raise SystemExit("配置缺少 storage.db_path")
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    store = Store(db_path)
    try:
        changed = False
        if args.pause_open:
            set_pause_open(store, candidate=args.candidate, paused=True, source="cli")
            changed = True
        if args.resume_open:
            set_pause_open(store, candidate=args.candidate, paused=False, source="cli")
            changed = True
        if args.flatten:
            request_flatten(store, candidate=args.candidate, source="cli")
            changed = True
        if args.risk_per_trade is not None:
            try:
                set_risk_per_trade(
                    store,
                    candidate=args.candidate,
                    value=args.risk_per_trade,
                    source="cli",
                )
            except ControlError as error:
                raise SystemExit(f"[FAIL] {error}") from error
            changed = True
        state = control_state(store, args.candidate)
        print(f"候选 {args.candidate}：{state}")
        if not changed and not args.show:
            print(
                "未指定操作（--show / --pause-open / --resume-open / --flatten / --risk-per-trade）"
            )
    finally:
        store.close()


if __name__ == "__main__":
    main()
