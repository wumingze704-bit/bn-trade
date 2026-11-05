"""实盘运行配置模型（pydantic）。

规格：docs/07-实盘与风控.md §10；configs/live.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from trade.common.config import ConfigError
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.live.notifier.config import NotifyConfig

RunMode = Literal["paper", "shadow", "live"]


class CandidatePoolConfig(BaseModel):
    """动态候选池（07 §3.5、configs/live.yaml candidate_pool 节）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    size: int = Field(default=20, ge=1, le=200)
    refresh_minutes: int = Field(default=15, ge=1, le=1440)


class UniverseLiveConfig(BaseModel):
    """月度再平衡时点与失败熔断（07 §3.5）。"""

    model_config = ConfigDict(extra="forbid")

    rebalance_utc: str = Field(default="00:05", pattern=r"^\d{2}:\d{2}$")
    rebalance_fail_halt_hours: int = Field(default=24, ge=1, le=720)


class RiskConfig(BaseModel):
    """实盘风险与追价参数（07 §5、§7；configs/live.yaml risk 节）。"""

    model_config = ConfigDict(extra="forbid")

    cooldown_bars: int = Field(default=15, ge=1)
    max_chase: float = Field(default=0.003, gt=0, le=0.1)
    pause_after_losses_min: int = Field(default=120, ge=1)
    spread_mult_limit: float = Field(default=3.0, gt=1.0)


class HealthConfig(BaseModel):
    """健康检查阈值（07 §5.5、§7；configs/live.yaml health 节）。"""

    model_config = ConfigDict(extra="forbid")

    ws_lag_sec: float = Field(default=2.0, gt=0)
    order_error_limit_5m: int = Field(default=5, ge=1)
    clock_drift_ms: int = Field(default=1000, ge=1)
    weight_usage_limit: float = Field(default=0.7, gt=0, le=1.0)
    fee_mismatch_reject_ratio: float = Field(default=1.2, gt=1.0)


class StorageConfig(BaseModel):
    """状态库与原子量录制路径（07 §3.3、§6.1；configs/live.yaml storage 节）。"""

    model_config = ConfigDict(extra="forbid")

    db_path: Path
    bars_dir: Path
    retention_days: int = Field(default=90, ge=1, le=3650)


class LiveConfig(BaseModel):
    """`configs/live.yaml` 的 pydantic 模型（07 §10）。"""

    model_config = ConfigDict(extra="forbid")

    mode: RunMode
    model_dir: Path
    candidate_pool: CandidatePoolConfig = Field(default_factory=CandidatePoolConfig)
    universe: Path | None = None  # 月度选币规则（07 §3.5），可选
    universe_live: UniverseLiveConfig = Field(default_factory=UniverseLiveConfig)
    margin_type: str = "ISOLATED"
    position_mode: str = "ONE_WAY"
    sizing: Path
    risk: RiskConfig = Field(default_factory=RiskConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    exits: Path
    notify: NotifyConfig
    storage: StorageConfig


def load_live_config(path: Path) -> LiveConfig:
    """加载并校验 live.yaml（07 §10）。

    Args:
        path: `configs/live.yaml` 路径。

    Returns:
        通过 `LiveConfig` 校验的配置实例。

    Raises:
        ConfigError: 文件无法读取或不是 YAML 映射。
        ValidationError: 字段缺失、类型不符或非法。
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"无法读取配置 {path}: {error}") from error
    if not isinstance(values, dict):
        raise ConfigError(f"配置必须为 YAML 映射：{path}")
    return LiveConfig.model_validate(values)


def load_sizing_config(path: Path) -> SizingConfig:
    """加载 sizing.yaml 并校验为 `SizingConfig`（06 §5.4）。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"无法读取配置 {path}: {error}") from error
    if not isinstance(values, dict):
        raise ConfigError(f"配置必须为 YAML 映射：{path}")
    return SizingConfig.model_validate(values)


def load_exits_config(path: Path) -> ExitConfig:
    """加载 exits.yaml 并校验为 `ExitConfig`（06 §3.3）。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"无法读取配置 {path}: {error}") from error
    if not isinstance(values, dict):
        raise ConfigError(f"配置必须为 YAML 映射：{path}")
    return ExitConfig.model_validate(values)
