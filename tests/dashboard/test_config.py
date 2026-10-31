"""看板配置校验测试。

规格：docs/10-可视化看板.md §2、§11
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from trade.dashboard.config import DashboardConfig, load_dashboard_config

_VALID: dict[str, Any] = {
    "server": {"address": "127.0.0.1", "port": 8501},
    "paths": {
        "state_db": "./artifacts/live/state.db",
        "live_dir": "./artifacts/live",
        "reports_dir": "./artifacts/reports",
        "models_dir": "./artifacts/models",
    },
    "push_sec": {"status": 5, "db_poll": 1},
    "cache_sec": {"realtime": 2, "research": 600},
    "display": {"tz": "Asia/Shanghai"},
    "control": {"enabled": True, "pin_env": "DASHBOARD_CONTROL_PIN"},
}


def _write(tmp_path: Path, values: dict[str, Any]) -> Path:
    path = tmp_path / "dashboard.yaml"
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    config = load_dashboard_config(_write(tmp_path, _VALID))
    assert config.server.address == "127.0.0.1"
    assert config.display.tz == "Asia/Shanghai"


def test_rejects_non_loopback_address(tmp_path: Path) -> None:
    values = {**_VALID, "server": {"address": "0.0.0.0", "port": 8501}}
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, values))


def test_rejects_public_address(tmp_path: Path) -> None:
    values = {**_VALID, "server": {"address": "8.8.8.8", "port": 8501}}
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, values))


def test_accepts_ipv6_loopback() -> None:
    config = DashboardConfig(
        server={"address": "::1", "port": 8501},  # type: ignore[arg-type]
        paths={  # type: ignore[arg-type]
            "state_db": "./a",
            "live_dir": "./a",
            "reports_dir": "./a",
            "models_dir": "./a",
        },
        display={"tz": "Asia/Shanghai"},  # type: ignore[arg-type]
        control={"enabled": True, "pin_env": "X"},  # type: ignore[arg-type]
    )
    assert config.server.address == "::1"


def test_rejects_unknown_field(tmp_path: Path) -> None:
    values = {**_VALID, "server": {"address": "127.0.0.1", "port": 8501, "bogus": 1}}
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, values))


def test_rejects_invalid_tz(tmp_path: Path) -> None:
    values = {**_VALID, "display": {"tz": "Mars/Olympus"}}
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, values))
