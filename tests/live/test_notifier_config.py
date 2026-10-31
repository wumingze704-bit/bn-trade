"""通知配置加载测试。

规格：docs/07-实盘与风控.md §8；configs/live.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from trade.common.config import ConfigError
from trade.live.notifier.config import load_notify_config

_NOTIFY: dict[str, Any] = {
    "notify": {
        "channels": [
            {
                "provider": "wxpusher",
                "token_env": "WXPUSHER_APP_TOKEN",
                "uid_env": "WXPUSHER_UID",
                "events": ["open"],
            },
            {"provider": "wecom", "key_env": "WECOM_WEBHOOK_KEY", "events": ["alert"]},
            {"provider": "feishu", "enabled": False, "events": []},
        ],
        "heartbeat_min": 60,
        "daily_report_utc": "00:05",
    }
}


def _write(tmp_path: Path, values: dict[str, Any]) -> Path:
    path = tmp_path / "live.yaml"
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return path


def test_load_notify_from_live_yaml(tmp_path: Path) -> None:
    config = load_notify_config(_write(tmp_path, _NOTIFY))
    assert len(config.channels) == 3
    assert config.channels[0].provider == "wxpusher"
    assert config.channels[2].enabled is False
    assert config.heartbeat_min == 60


def test_load_notify_standalone_section(tmp_path: Path) -> None:
    config = load_notify_config(_write(tmp_path, _NOTIFY["notify"]))
    assert len(config.channels) == 3


def test_missing_notify_section(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_notify_config(_write(tmp_path, {"mode": "shadow"}))


def test_reject_unknown_channel_field(tmp_path: Path) -> None:
    values = {"notify": {"channels": [{"provider": "wecom", "bogus": 1}]}}
    with pytest.raises(ValidationError):
        load_notify_config(_write(tmp_path, values))
