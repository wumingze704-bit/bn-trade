"""systemd 单元与 sudoers 规则测试（3 号第六部分第 10、12 条、第八部分 6b）。

规格：docs/07-实盘与风控.md 部署与看门狗小节；docs/02 §5

"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_交易单元用on_failure且直接调用venv_python() -> None:
    for name in ("trade-runner@.service", "trade-runner-live@.service"):
        text = _read(name)
        assert "Restart=on-failure" in text, name
        assert "Restart=always" not in text, name
        assert "/home/ubuntu/trade-venv/bin/python" in text, name
        assert "bash -l" not in text and "/bin/bash" not in text, name


def test_交易单元带文件系统加固() -> None:
    for name in ("trade-runner@.service", "trade-runner-live@.service"):
        text = _read(name)
        for key in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "ReadWritePaths=/home/ubuntu/trade/artifacts/live",
        ):
            assert key in text, f"{name} 缺少 {key}"


def test_看板单元直接调用python并加固() -> None:
    text = _read("trade-dashboard.service")
    assert "Restart=on-failure" in text
    assert "/bin/bash" not in text and "bash -l" not in text
    assert "/home/ubuntu/trade-venv/bin/python" in text
    for key in ("NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=strict"):
        assert key in text, key


def test_看门狗单元保留sudo所需权限并加固() -> None:
    text = _read("trade-watchdog.service")
    for key in ("PrivateTmp=true", "ProtectSystem=full"):
        assert key in text, key
    # sudo 是 setuid 程序，NoNewPrivileges=true 会让它无法工作（docs/07 §12）
    active = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "NoNewPrivileges=true" not in active
    assert "live_watchdog.py" in text


def test_sudoers只允许执行白名单重启脚本() -> None:
    text = _read("sudoers-trade-watchdog")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 1, lines
    rule = lines[0]
    assert rule == "ubuntu ALL=(root) NOPASSWD: /usr/local/sbin/trade-restart-unit"
    for forbidden in ("stop", "rm ", "sh ", "bash", "/bin/sh", "systemctl", "*"):
        assert forbidden not in rule, f"sudoers 规则不应包含 {forbidden}"


def test_重启脚本只接受trade_runner实例() -> None:
    text = _read("trade-restart-unit")
    assert "IFS= read -r unit" in text
    assert "trade-runner@*.service" in text
    assert "trade-runner-live@*.service" in text
    assert "exec /usr/bin/systemctl restart" in text
    assert "exit 64" in text
    assert "stop" not in text
