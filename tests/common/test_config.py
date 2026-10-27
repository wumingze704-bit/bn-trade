"""配置加载器测试。

规格：docs/08-开发计划与验收.md §2 P0
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from trade.common.config import BaseConfig, ConfigError, load_config


def test_config_merge_order(tmp_path: Path) -> None:
    base_path = tmp_path / "base.yaml"
    specific_path = tmp_path / "specific.yaml"
    base_path.write_text(
        "market: usdm\n"
        "quote_asset: USDT\n"
        'data_window: {end_month: "2026-09", months: 12, warmup_days: 10, holdout_months: 2}\n'
        "fees: {usdm: {maker: 0.0002, taker: 0.0005}, spot: {maker: 0.001, taker: 0.001}}\n"
        "display_tz: Asia/Shanghai\n"
        "paths: {data_root: ./data, artifact_root: ./artifacts}\n"
        "seed: 42\n",
        encoding="utf-8",
    )
    specific_path.write_text("seed: 7\n", encoding="utf-8")

    config = load_config(BaseConfig, base_path, specific_path, ["seed=11"])

    assert config.seed == 11


def test_config_rejects_unknown_fields(tmp_path: Path) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text("unknown: true\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(BaseConfig, base_path)


def test_config_rejects_invalid_override_syntax(tmp_path: Path) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text("seed: 42\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="key=value"):
        load_config(BaseConfig, base_path, overrides=["seed"])


def test_config_rejects_out_of_range_values(tmp_path: Path) -> None:
    base_path = tmp_path / "base.yaml"
    base_path.write_text("market: invalid\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(BaseConfig, base_path)
