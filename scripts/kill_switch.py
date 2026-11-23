"""紧急停机脚本：撤全部挂单与条件单、平掉全部仓位、写停机标志、停止实例。

规格：docs/07-实盘与风控.md §7.3

用法（服务器）：
    python scripts/kill_switch.py --candidate <候选ID> --mode live
    python scripts/kill_switch.py --candidate <候选ID> --mode paper
    python scripts/kill_switch.py --candidate <候选ID> --mode shadow   # 只写停机标志

停机标志只能由人工用 `scripts/live_runner.py --clear-halt` 清除；看板不提供清除功能。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
from pathlib import Path

import yaml

from trade.live.credentials import CredentialError, build_client
from trade.live.feed import rules_from_exchange_info
from trade.live.killswitch import KillSwitchError, kill_switch
from trade.live.store import Store
from trade.live.units import unit_name

_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _stop_unit(candidate: str, mode: str) -> None:
    """停止 systemd 实例（本机无 systemd 时忽略；不影响停机标志）。"""
    unit = unit_name(candidate, mode)
    try:
        subprocess.run(
            ["systemctl", "stop", f"{unit}.service"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        print(f"[OK] 已请求停止 {unit}")
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[WARN] 停止 {unit} 失败（请手动检查）：{error}")


async def _main(args: argparse.Namespace) -> int:
    config_path = _resolve(args.config)
    if not config_path.is_file():
        print(f"[FAIL] 配置文件不存在：{config_path}")
        return 1
    values = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db_path = Path(str(values.get("storage", {}).get("db_path", "")))
    if not db_path:
        print("[FAIL] 配置缺少 storage.db_path")
        return 1
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    store = Store(db_path)
    client = None
    try:
        step_sizes: dict[str, float] = {}
        if args.mode != "shadow":
            client = build_client(args.mode)
            info = await client.exchange_info()
            rules = rules_from_exchange_info(info)
            step_sizes = {
                symbol: float(meta.step_size)
                for symbol, meta in rules.items()
                if meta.step_size is not None
            }
        summary = await kill_switch(
            client=client,
            store=store,
            candidate=args.candidate,
            mode=args.mode,
            step_sizes=step_sizes,
            reason=args.reason,
        )
        print(
            f"[OK] 停机完成：撤单 {summary['cancelled_orders']}、"
            f"撤条件单 {summary['cancelled_algos']}、平仓 {len(summary['closed'])}、"
            f"剩余 {len(summary['remaining'])}"
        )
        _stop_unit(args.candidate, args.mode)
        return 0
    except (CredentialError, KillSwitchError) as error:
        print(f"[FAIL] {error}")
        return 1
    finally:
        store.close()
        if client is not None:
            await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="紧急停机")
    parser.add_argument("--candidate", required=True, help="候选 ID")
    parser.add_argument("--mode", choices=["shadow", "paper", "live"], required=True)
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
