"""配置加载器测试。

规格：docs/08-开发计划与验收.md §2 P0
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from trade.common.config import BaseConfig, ConfigError, load_config

_BASE: dict[str, Any] = {
    "market": "usdm",
    "quote_asset": "USDT",
    "data_window": {
        "end_month": "2026-09",
        "months": 24,
        "warmup_days": 30,
        "tuning_months": 12,
        "holdout_months": 2,
    },
    "fees": {"maker": 0.0002, "taker": 0.0005},
    "display_tz": "Asia/Shanghai",
    "paths": {"data_root": "./data", "artifact_root": "./artifacts"},
    "seed": 42,
}


def _write(tmp_path: Path, name: str, values: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return path


def test_config_merge_order(tmp_path: Path) -> None:
    base_path = _write(tmp_path, "base.yaml", _BASE)
    specific_path = _write(tmp_path, "specific.yaml", {"seed": 7})

    config = load_config(BaseConfig, base_path, specific_path, ["seed=11"])

    assert config.seed == 11


def test_config_specific_overrides_nested_key_only(tmp_path: Path) -> None:
    base_path = _write(tmp_path, "base.yaml", _BASE)
    specific_path = _write(tmp_path, "specific.yaml", {"data_window": {"months": 36}})

    config = load_config(BaseConfig, base_path, specific_path)

    assert config.data_window.months == 36
    # 专用配置只覆盖指定键，未覆盖的兄弟字段保留基础值
    assert config.data_window.warmup_days == 30


def test_config_cli_override_uses_dot_path(tmp_path: Path) -> None:
    base_path = _write(tmp_path, "base.yaml", _BASE)

    config = load_config(BaseConfig, base_path, overrides=["data_window.months=36"])

    assert config.data_window.months == 36


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    base_path = _write(tmp_path, "base.yaml", {"unknown": True})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(BaseConfig, base_path)


def test_config_rejects_invalid_override_syntax(tmp_path: Path) -> None:
    base_path = _write(tmp_path, "base.yaml", _BASE)

    with pytest.raises(ConfigError, match="key=value"):
        load_config(BaseConfig, base_path, overrides=["seed"])


def test_config_rejects_unknown_market(tmp_path: Path) -> None:
    values = deepcopy(_BASE)
    values["market"] = "invalid"
    base_path = _write(tmp_path, "base.yaml", values)

    with pytest.raises(ValidationError):
        load_config(BaseConfig, base_path)


def test_config_rejects_spot_market(tmp_path: Path) -> None:
    # 现货模式已于 v2.1 删除，仅允许 usdm（docs/01 §5）
    values = deepcopy(_BASE)
    values["market"] = "spot"
    base_path = _write(tmp_path, "base.yaml", values)

    with pytest.raises(ValidationError):
        load_config(BaseConfig, base_path)


def test_config_rejects_nested_fees(tmp_path: Path) -> None:
    # 费率在 v2.1 为单市场扁平结构，不再有 usdm/spot 二级键（docs/02 §7）
    values = deepcopy(_BASE)
    values["fees"] = {"usdm": {"maker": 0.0002, "taker": 0.0005}}
    base_path = _write(tmp_path, "base.yaml", values)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(BaseConfig, base_path)


def test_config_rejects_out_of_range_tuning_months(tmp_path: Path) -> None:
    values = deepcopy(_BASE)
    values["data_window"]["tuning_months"] = -1
    base_path = _write(tmp_path, "base.yaml", values)

    with pytest.raises(ValidationError):
        load_config(BaseConfig, base_path)
