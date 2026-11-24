"""看门狗：检查各引擎实例心跳，必要时重启一次并告警（docs/07 通知、日报与看门狗）。


由 `deploy/trade-watchdog.timer` 每 5 分钟调用（systemd oneshot）。规则（3 号第六部分第 10 条）：

- 心跳超过 `engine.watchdog_stale_min` 分钟未更新 → 重启该实例一次并告警；
- 从第一次重启算起 24 小时内第二次超时 → 只告警，不再重启，直到使用者处理；
- 以下情况一律只告警、不重启：该实例有停机标志；单元未启用；实例被主动停止
  （`systemctl is-active` 非 active 且 `Result=success`）。
- 只重启 `trade-runner@*` / `trade-runner-live@*`，经 `sudo -n` 与 sudoers 白名单规则执行。

用法：`python scripts/live_watchdog.py --config configs/live.yaml`
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trade.live.config import load_live_config
from trade.live.units import unit_name

_ROOT = Path(__file__).resolve().parents[1]
_MS_PER_MINUTE = 60_000
_RESTART_COOLDOWN_MS = 24 * 60 * 60_000
_ALLOWED_PREFIXES = ("trade-runner@", "trade-runner-live@")


def _run(args: list[str]) -> tuple[int, str]:
    """执行一条只读/白名单命令，返回 (退出码, stdout)。

    只在这里集中处理 subprocess 异常，调用方按退出码判定，不吞掉规则判断。
    """
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return result.returncode, (result.stdout or "").strip()


def unit_allowed(unit: str) -> bool:
    """只允许重启 trade-runner@* 与 trade-runner-live@*（与 sudoers 规则一致）。"""
    return unit.startswith(_ALLOWED_PREFIXES)


def restart_command(unit: str) -> list[str] | None:
    """返回重启命令；不在白名单的单元返回 None（不执行任何命令）。"""
    if not unit_allowed(unit):
        return None
    return ["sudo", "-n", "systemctl", "restart", unit]


def restart_unit(unit: str) -> bool:
    """重启实例一次；返回是否成功（失败只告警，不抛异常）。"""
    command = restart_command(unit)
    if command is None:
        return False
    code, _ = _run(command)
    return code == 0


def unit_enabled(unit: str) -> bool:
    """单元是否已启用（未启用的实例不由看门狗重启）。"""
    code, out = _run(["systemctl", "is-enabled", unit])
    return code == 0 and out.strip() in {"enabled", "enabled-runtime", "static"}


def unit_active_state(unit: str) -> str:
    """`systemctl is-active` 的状态（active / inactive / failed / unknown）。"""
    code, out = _run(["systemctl", "is-active", unit])
    if not out:
        return "unknown" if code != 0 else "active"
    return out.strip()


def unit_result(unit: str) -> str:
    """`systemctl show -p Result --value` 的最近一次退出结果（success 表示主动停止/正常退出）。"""
    code, out = _run(["systemctl", "show", "-p", "Result", "--value", unit])
    if code != 0:
        return ""
    return out.strip()


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _read_heartbeat(path: Path) -> int:
    """读取心跳文件里的毫秒时间戳；内容非法时退回文件修改时间。"""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return int(path.stat().st_mtime * 1000)


def _alert_reason(
    *,
    halt_flag: bool,
    enabled: bool,
    active: str,
    result: str,
    last_restart_ms: int | None,
    now_ms: int,
    cooldown_ms: int,
) -> str:
    """给告警写清“为什么不重启”（便于人工判断）。"""
    if halt_flag:
        return "存在停机标志"
    if not enabled:
        return "实例单元未启用"
    if active not in {"active", "activating", "reloading"} and result == "success":
        return "实例已被主动停止"
    if last_restart_ms is not None and now_ms - last_restart_ms < cooldown_ms:
        return "24 小时内已重启过一次，只告警"
    return "只告警"


def _halt_flag(store: Any, candidate: str, mode: str) -> bool:
    """读取停机标志；状态库不存在或读不到时按 False 处理（此时也没有实例）。"""
    if store is None:
        return False
    row = store.get_engine_halt(candidate=candidate, mode=mode)
    return bool(row["halted"]) if row is not None else False


def _alert(text: str, db_path: Path) -> None:
    """写状态库事件；状态库不可用时只打印。"""
    print(f"[ALERT] {text}")
    if not db_path.is_file():
        return
    from trade.live.store import Store

    store = Store(db_path)
    try:
        store.insert_event(level="error", event=text, symbol=None, detail={"source": "watchdog"})
        store.commit()
    finally:
        store.close()


def _load_state(path: Path) -> dict[str, int]:
    """读取重启记录 `{unit: 首次重启毫秒}`；兼容旧版 `{unit: [毫秒, ...]}`。"""
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    restarts: dict[str, int] = {}
    for unit, value in raw.items():
        if isinstance(value, list) and value:
            restarts[str(unit)] = int(value[-1])
        elif isinstance(value, int):
            restarts[str(unit)] = int(value)
    return restarts


def main() -> None:
    parser = argparse.ArgumentParser(description="引擎看门狗")
    parser.add_argument("--config", default="configs/live.yaml")
    args = parser.parse_args()
    config = load_live_config(_resolve(args.config))
    db_path = Path(config.storage.db_path)
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    state_path = db_path.parent / "watchdog.json"
    restarts = _load_state(state_path)
    store = None
    if db_path.is_file():
        from trade.live.store import Store

        store = Store(db_path)
    now = int(datetime.now(tz=UTC).timestamp() * 1000)
    stale_min = config.engine.watchdog_stale_min
    stale_ms = stale_min * _MS_PER_MINUTE

    try:
        for heartbeat in sorted(db_path.parent.glob("*.heartbeat")):
            stem = heartbeat.stem
            if "-" not in stem:
                continue
            candidate, mode = stem.rsplit("-", 1)
            if mode not in {"shadow", "paper", "live"}:
                continue
            unit = unit_name(candidate, mode)
            if not unit_allowed(unit):
                _alert(f"{unit} 不在看门狗白名单（不重启）", db_path)
                continue
            last = _read_heartbeat(heartbeat)
            if now - last <= stale_ms:
                continue
            halt_flag = _halt_flag(store, candidate, mode)
            enabled = unit_enabled(unit)
            active = unit_active_state(unit)
            result = unit_result(unit)
            action = decide(
                stale=True,
                halt_flag=halt_flag,
                enabled=enabled,
                active=active,
                result=result,
                last_restart_ms=restarts.get(unit),
                now_ms=now,
                cooldown_ms=_RESTART_COOLDOWN_MS,
            )
            if action == "restart":
                ok = restart_unit(unit)
                restarts[unit] = now
                _alert(f"{unit} 心跳超时，已{'重启' if ok else '尝试重启失败'}（看门狗）", db_path)
            else:
                reason = _alert_reason(
                    halt_flag=halt_flag,
                    enabled=enabled,
                    active=active,
                    result=result,
                    last_restart_ms=restarts.get(unit),
                    now_ms=now,
                    cooldown_ms=_RESTART_COOLDOWN_MS,
                )
                _alert(
                    f"{unit} 心跳已超时 {stale_min} 分钟，{reason}（不重启）",
                    db_path,
                )

    finally:
        if store is not None:
            store.close()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"restarts": restarts}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()


def decide(
    *,
    stale: bool,
    halt_flag: bool,
    enabled: bool,
    active: str,
    result: str,
    last_restart_ms: int | None,
    now_ms: int,
    cooldown_ms: int = _RESTART_COOLDOWN_MS,
) -> str:
    """判定一次心跳超时的处理方式（`restart` / `alert` / `skip`）。"""
    if not stale:
        return "skip"
    if halt_flag:
        return "alert"
    if not enabled:
        return "alert"
    if active not in {"active", "activating", "reloading"} and result == "success":
        return "alert"
    if last_restart_ms is not None and now_ms - last_restart_ms < cooldown_ms:
        return "alert"
    return "restart"
