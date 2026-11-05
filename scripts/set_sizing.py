"""仓位调节脚本：--show / --reset / --position-frac 等白名单键。

规格：docs/07-实盘与风控.md §7.1.1

用法：
  python scripts/set_sizing.py --config configs/live.yaml --show
  python scripts/set_sizing.py --config configs/live.yaml --position-frac 0.25
  python scripts/set_sizing.py --config configs/live.yaml --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trade.live.config import load_live_config, load_sizing_config
from trade.live.control import (
    OVERRIDE_KEYS,
    apply_override,
    effective_sizing_from_store,
    reset_overrides,
)
from trade.live.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="仓位调节")
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--equity", type=float, default=None, help="实时权益（校验用，缺省取 initial_equity）"
    )
    for key in sorted(OVERRIDE_KEYS):
        parser.add_argument(f"--{key.replace('_', '-')}", dest=key, default=None)
    args = parser.parse_args()

    config = load_live_config(Path(args.config))
    sizing = load_sizing_config(config.sizing)
    store = Store(config.storage.db_path)

    if args.show:
        effective = effective_sizing_from_store(store, sizing)
        for key in sorted(OVERRIDE_KEYS):
            print(f"{key} = {getattr(effective, key)}")
        store.close()
        return

    if args.reset:
        reset_overrides(store)
        store.commit()
        print("[OK] 已恢复 sizing.yaml 默认值")
        store.close()
        return

    overrides = {key: getattr(args, key) for key in OVERRIDE_KEYS if getattr(args, key) is not None}
    if not overrides:
        print("未提供任何覆盖项（可用 --position-frac 等）", file=sys.stderr)
        store.close()
        sys.exit(1)

    equity = args.equity if args.equity is not None else sizing.initial_equity
    for key, value in overrides.items():
        effective = apply_override(
            store,
            key=key,
            value=str(value),
            source="cli",
            sizing=sizing,
            equity=float(equity),
            min_notional_median=5.0,
            sl_max=0.02,
            max_leverage=20,
        )
        store.commit()
        print(f"[OK] {key} -> {getattr(effective, key)}")
    store.close()


if __name__ == "__main__":
    main()
