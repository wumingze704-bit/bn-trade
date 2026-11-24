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


def _run_systemctl(args: list[str]) -> tuple[int, str]:
    """执行 systemctl（失败返回非 0，不抛异常）。"""
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return result.returncode, (result.stdout or "").strip()


def _unit_inactive(unit: str) -> bool:
    """确认实例已停止：`systemctl is-active` 返回 inactive / failed / unknown。"""
    code, out = _run_systemctl(["systemctl", "is-active", f"{unit}.service"])
    if code != 0 and not out:
        return False
    return out in {"inactive", "failed", "unknown"}


def _stop_unit(candidate: str, mode: str) -> bool:
    """停止 systemd 实例并确认已停止；返回是否确认停止。

    先以当前用户执行，失败时用只读 sudo 规则之外的 `sudo -n systemctl stop` 重试；
    两次都无法确认停止时返回 False（此时 kill_switch 不得动账户）。
    """
    unit = unit_name(candidate, mode)
    for command in (
        ["systemctl", "stop", f"{unit}.service"],
        ["sudo", "-n", "systemctl", "stop", f"{unit}.service"],
    ):
        _run_systemctl(command)
        if _unit_inactive(unit):
            print(f"[OK] 已确认停止 {unit}")
            return True
    print(f"[FAIL] 无法确认 {unit} 已停止")
    return False


async def _stop_unit_async(candidate: str, mode: str) -> bool:
    """在线程中执行阻塞的停止操作（kill_switch 需要异步回调）。"""
    return await asyncio.to_thread(_stop_unit, candidate, mode)


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
            stop_runner=lambda: _stop_unit_async(args.candidate, args.mode),
            heartbeat_path=db_path.parent / f"{args.candidate}-{args.mode}.heartbeat",
        )
        print(
            f"[OK] 停机完成：撤单 {summary['cancelled_orders']}、"
            f"撤条件单 {summary['cancelled_algos']}、平仓 {len(summary['closed'])}、"
            f"剩余 {len(summary['remaining'])}"
        )
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
