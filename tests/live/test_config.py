"""实盘配置加载单测。

规格：docs/07-实盘与风控.md §10
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trade.common.config import ConfigError
from trade.live.config import LiveConfig, load_live_config


def _write_live_yaml(tmp_path: Path, **overrides: object) -> Path:
    values = {
        "mode": "shadow",
        "model_dir": "./artifacts/models/x",
        "sizing": "./configs/sizing.yaml",
        "exits": "./configs/exits.yaml",
        "notify": {"channels": [], "heartbeat_min": 60, "daily_report_utc": "00:05"},
        "storage": {"db_path": "./artifacts/live/state.db", "bars_dir": "./artifacts/live/bars"},
    }
    values.update(overrides)
    path = tmp_path / "live.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def test_load_live_config_ok(tmp_path: Path) -> None:
    path = _write_live_yaml(tmp_path)
    config = load_live_config(path)
    assert config.mode == "shadow"


def test_load_live_config_rejects_unknown_field(tmp_path: Path) -> None:
    path = _write_live_yaml(tmp_path, unknown="x")
    with pytest.raises(ValidationError):
        load_live_config(path)


def test_live_config_candidate_pool_defaults() -> None:
    pool = LiveConfig(
        mode="paper",
        model_dir=Path("x"),
        sizing=Path("sizing.yaml"),
        exits=Path("exits.yaml"),
        notify={"channels": []},  # type: ignore[arg-type]
        storage={"db_path": Path("x"), "bars_dir": Path("y")},  # type: ignore[arg-type]
    ).candidate_pool
    assert pool.size == 20
    assert pool.enabled is True


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_live_config(tmp_path / "missing.yaml")
