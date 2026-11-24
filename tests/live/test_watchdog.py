"""看门狗规则测试（3 号第六部分第 10 条、第八部分 6b）。

规格：docs/07-实盘与风控.md 通知、日报与看门狗

"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
_HOUR_MS = 3_600_000


def _load_watchdog() -> ModuleType:
    """按路径加载 scripts/live_watchdog.py（scripts 不是包）。"""
    path = ROOT / "scripts" / "live_watchdog.py"
    spec = importlib.util.spec_from_file_location("live_watchdog_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def watchdog() -> ModuleType:
    return _load_watchdog()


def test_有停机标志时不重启(watchdog: ModuleType) -> None:
    """kill_switch 写下的停机标志优先于心跳超时。"""
    action = watchdog.decide(
        stale=True,
        halt_flag=True,
        enabled=True,
        active="active",
        result="success",
        last_restart_ms=None,
        now_ms=1_000_000_000_000,
        cooldown_ms=24 * _HOUR_MS,
    )
    assert action == "alert"


def test_主动停止的实例不重启(watchdog: ModuleType) -> None:
    """systemctl stop 后 Result=success 表示主动停止，只告警。"""
    action = watchdog.decide(
        stale=True,
        halt_flag=False,
        enabled=True,
        active="inactive",
        result="success",
        last_restart_ms=None,
        now_ms=1_000_000_000_000,
        cooldown_ms=24 * _HOUR_MS,
    )
    assert action == "alert"


def test_单元未启用时不重启(watchdog: ModuleType) -> None:
    action = watchdog.decide(
        stale=True,
        halt_flag=False,
        enabled=False,
        active="inactive",
        result="success",
        last_restart_ms=None,
        now_ms=1_000_000_000_000,
        cooldown_ms=24 * _HOUR_MS,
    )
    assert action == "alert"


def test_崩溃后重启一次(watchdog: ModuleType) -> None:
    """非主动停止（Result 为 exit-code）且链路内没有重启记录 → 重启一次。"""
    action = watchdog.decide(
        stale=True,
        halt_flag=False,
        enabled=True,
        active="failed",
        result="exit-code",
        last_restart_ms=None,
        now_ms=1_000_000_000_000,
        cooldown_ms=24 * _HOUR_MS,
    )
    assert action == "restart"


def test_24小时内第二次超时只告警(watchdog: ModuleType) -> None:
    """3 号第六部分第 10 条：从第一次重启算起 24 小时内第二次超时不再重启。"""
    now = 1_000_000_000_000
    recent = watchdog.decide(
        stale=True,
        halt_flag=False,
        enabled=True,
        active="failed",
        result="exit-code",
        last_restart_ms=now - 2 * _HOUR_MS,
        now_ms=now,
        cooldown_ms=24 * _HOUR_MS,
    )
    expired = watchdog.decide(
        stale=True,
        halt_flag=False,
        enabled=True,
        active="failed",
        result="exit-code",
        last_restart_ms=now - 25 * _HOUR_MS,
        now_ms=now,
        cooldown_ms=24 * _HOUR_MS,
    )
    assert recent == "alert"
    assert expired == "restart"


def test_状态文件读取重启记录(watchdog: ModuleType, tmp_path: Path) -> None:
    """写出的 {"restarts": {...}} 能被读回，24 小时冷却才不会丢失（实测发现）。"""
    import json

    path = tmp_path / "watchdog.json"
    path.write_text(json.dumps({"restarts": {"trade-runner@C1-shadow": 123}}), encoding="utf-8")
    assert watchdog._load_state(path) == {"trade-runner@C1-shadow": 123}


def test_不在白名单的单元不执行命令(watchdog: ModuleType) -> None:
    """只允许 trade-runner@* 与 trade-runner-live@*（sudoers 同一条规则）。"""
    expected = ["sudo", "-n", "/usr/local/sbin/trade-restart-unit"]
    assert watchdog.restart_command("trade-runner@C1-shadow.service") == expected
    assert watchdog.restart_command("trade-runner-live@C1.service") == expected
    assert watchdog.restart_command("trade-dashboard.service") is None
    assert watchdog.restart_command("trade-watchdog.service") is None


def test_重启用sudo且失败返回False(watchdog: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(args: list[str], input_text: str | None = None) -> tuple[int, str]:
        calls.append((args, input_text))
        return (1, "") if (input_text or "").startswith("trade-runner@C2") else (0, "")

    monkeypatch.setattr(watchdog, "_run", fake_run)
    assert watchdog.restart_unit("trade-runner@C1-shadow.service") is True
    assert watchdog.restart_unit("trade-runner@C2-shadow.service") is False
    before = len(calls)
    assert watchdog.restart_unit("trade-dashboard.service") is False
    assert len(calls) == before
    assert all(args == ["sudo", "-n", "/usr/local/sbin/trade-restart-unit"] for args, _ in calls)
    assert calls[0][1] == "trade-runner@C1-shadow.service\n"
    assert watchdog.restart_unit("trade-runner@C3-shadow") is True
    assert calls[-1][1] == "trade-runner@C3-shadow.service\n"
