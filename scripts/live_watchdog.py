"""看门狗：检查各引擎实例心跳，必要时重启一次并告警（docs/07 §6.10）。

由 `deploy/trade-watchdog.timer` 每 5 分钟调用（systemd oneshot）。规则：
- 心跳超过 `engine.watchdog_stale_min` 分钟未更新 → 重启该实例一次并告警；
- 同一实例 1 小时内第二次超时 → 只告警，不再重启（避免重启风暴）。

用法：`python scripts/live_watchdog.py --config configs/live.yaml`
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from trade.live.config import load_live_config
from trade.live.units import unit_name

_ROOT = Path(__file__).resolve().parents[1]
_MS_PER_MINUTE = 60_000
_RESTART_WINDOW_MS = 60 * 60_000


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _read_heartbeat(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return int(path.stat().st_mtime * 1000)


def _restart(unit: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "restart", unit],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


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


def main() -> None:
    parser = argparse.ArgumentParser(description="引擎看门狗")
    parser.add_argument("--config", default="configs/live.yaml")
    args = parser.parse_args()
    config = load_live_config(_resolve(args.config))
    db_path = Path(config.storage.db_path)
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    state_path = db_path.parent / "watchdog.json"
    state: dict[str, list[int]] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    now = int(datetime.now(tz=UTC).timestamp() * 1000)
    stale_ms = config.engine.watchdog_stale_min * _MS_PER_MINUTE
    for heartbeat in sorted(db_path.parent.glob("*.heartbeat")):
        stem = heartbeat.stem
        if "-" not in stem:
            continue
        candidate, mode = stem.rsplit("-", 1)
        if mode not in {"shadow", "paper", "live"}:
            continue
        last = _read_heartbeat(heartbeat)
        if now - last <= stale_ms:
            continue
        unit = unit_name(candidate, mode)
        recent = [ts for ts in state.get(unit, []) if now - ts < _RESTART_WINDOW_MS]
        if recent:
            state[unit] = recent
            _alert(
                f"{unit} 心跳已超时 {config.engine.watchdog_stale_min} 分钟，"
                "1 小时内已重启过，只告警",
                db_path,
            )
            continue
        ok = _restart(unit)
        recent.append(now)
        state[unit] = recent
        _alert(f"{unit} 心跳超时，已{'重启' if ok else '尝试重启失败'}（看门狗）", db_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
