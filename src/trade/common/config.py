"""配置读取、合并与校验。

规格：docs/02-系统架构与部署.md §7；docs/08-开发计划与验收.md §2 P0
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T", bound=BaseModel)


class ConfigError(Exception):
    """配置文件无法读取或合并时抛出的异常。"""


class DataWindowConfig(BaseModel):
    """历史数据时间窗口配置。"""

    model_config = ConfigDict(extra="forbid")

    end_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    months: int = Field(ge=1, le=120)
    warmup_days: int = Field(ge=1, le=365)
    tuning_months: int = Field(ge=0, le=120)
    holdout_months: int = Field(ge=0, le=12)


class FeeConfig(BaseModel):
    """单一市场的挂单与吃单费率。"""

    model_config = ConfigDict(extra="forbid")

    maker: float = Field(ge=0, le=0.1)
    taker: float = Field(ge=0, le=0.1)


class PathsConfig(BaseModel):
    """数据与产物目录。"""

    model_config = ConfigDict(extra="forbid")

    data_root: Path
    artifact_root: Path


class BaseConfig(BaseModel):
    """所有运行配置共用的基础字段。"""

    model_config = ConfigDict(extra="forbid")

    market: str = Field(pattern=r"^usdm$")
    quote_asset: str = Field(pattern=r"^[A-Z0-9]{2,12}$")
    data_window: DataWindowConfig
    fees: FeeConfig
    display_tz: str
    paths: PathsConfig
    seed: int = Field(ge=0)

    @field_validator("display_tz")
    @classmethod
    def validate_display_tz(cls, value: str) -> str:
        """确保显示时区是系统可识别的 IANA 时区。"""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"未知时区：{value}") from error
        return value


def load_config(
    config_model: type[T],
    base_path: Path,
    specific_path: Path | None = None,
    overrides: Sequence[str] = (),
) -> T:
    """按基础、专用、命令行的优先级加载并校验 YAML 配置。

    Args:
        config_model: 用于校验最终配置的 Pydantic 模型。
        base_path: 基础 YAML 配置文件路径。
        specific_path: 可选的专用 YAML 配置文件路径。
        overrides: `key=value` 格式的命令行覆盖项，支持点分隔嵌套键。

    Returns:
        通过 `config_model` 校验的配置实例。

    Raises:
        ConfigError: 文件不是有效的 YAML 映射，或覆盖语法无效。
        ValidationError: 合并结果不符合 Pydantic 模型。
    """
    values = _read_yaml(base_path)
    if specific_path is not None:
        values = _deep_merge(values, _read_yaml(specific_path))
    for override in overrides:
        _apply_override(values, override)
    return config_model.model_validate(values)


def _read_yaml(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            values = yaml.safe_load(config_file)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"无法读取配置文件 {path}: {error}") from error
    if not isinstance(values, dict) or not all(isinstance(key, str) for key in values):
        raise ConfigError(f"配置文件必须包含字符串键的 YAML 映射：{path}")
    return values


def _deep_merge(base: Mapping[str, object], specific: Mapping[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in specific.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _apply_override(values: dict[str, object], override: str) -> None:
    key_path, separator, raw_value = override.partition("=")
    keys = key_path.split(".")
    if not separator or any(not key for key in keys):
        raise ConfigError(f"覆盖项必须使用 key=value 格式：{override}")
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError as error:
        raise ConfigError(f"覆盖项不是有效的 YAML 值：{override}") from error

    target = values
    for key in keys[:-1]:
        child = target.get(key)
        if child is None:
            child = {}
            target[key] = child
        if not isinstance(child, dict):
            raise ConfigError(f"覆盖项路径不是映射：{key_path}")
        target = child
    target[keys[-1]] = value
