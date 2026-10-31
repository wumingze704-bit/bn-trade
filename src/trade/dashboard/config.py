"""看板配置模型与加载。

规格：docs/10-可视化看板.md §11
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade.common.config import load_config


class ServerConfig(BaseModel):
    """HTTP 服务监听配置（10 §2）。"""

    model_config = ConfigDict(extra="forbid")

    address: str = "127.0.0.1"
    port: int = Field(default=8501, ge=1, le=65535)

    @field_validator("address")
    @classmethod
    def validate_loopback(cls, value: str) -> str:
        """看板只能监听回环地址，拒绝 0.0.0.0 或其他非回环地址（10 §2）。"""
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError(f"非法监听地址：{value}") from error
        if not address.is_loopback:
            raise ValueError(f"看板只能监听回环地址，拒绝 {value}（docs/10 §2）")
        return value


class DashboardPathsConfig(BaseModel):
    """看板读取的目录与状态库路径（10 §11）。"""

    model_config = ConfigDict(extra="forbid")

    state_db: Path
    live_dir: Path
    reports_dir: Path
    models_dir: Path


class PushConfig(BaseModel):
    """WebSocket 推送与状态库轮询间隔，单位秒（10 §11）。"""

    model_config = ConfigDict(extra="forbid")

    status: int = Field(default=5, ge=1, le=3600)
    db_poll: int = Field(default=1, ge=1, le=60)


class CacheConfig(BaseModel):
    """接口缓存秒数（10 §11）。"""

    model_config = ConfigDict(extra="forbid")

    realtime: int = Field(default=2, ge=0, le=3600)
    research: int = Field(default=600, ge=0, le=86400)


class DisplayConfig(BaseModel):
    """前端展示参数（10 §4、§11）。"""

    model_config = ConfigDict(extra="forbid")

    tz: str
    amount_decimals: int = Field(default=4, ge=0, le=12)
    kline_max_bars: int = Field(default=720, ge=1, le=10000)
    equity_max_points: int = Field(default=2000, ge=1, le=100000)

    @field_validator("tz")
    @classmethod
    def validate_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"未知时区：{value}") from error
        return value


class ControlConfig(BaseModel):
    """控制页配置（10 §8、§11）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    pin_env: str
    max_pin_failures: int = Field(default=5, ge=1, le=100)
    lock_minutes: int = Field(default=15, ge=1, le=1440)


class DashboardConfig(BaseModel):
    """看板配置根模型（10 §11）。"""

    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    paths: DashboardPathsConfig
    push_sec: PushConfig = Field(default_factory=PushConfig)
    cache_sec: CacheConfig = Field(default_factory=CacheConfig)
    display: DisplayConfig
    control: ControlConfig


def load_dashboard_config(path: Path) -> DashboardConfig:
    """加载并校验看板 YAML 配置（10 §11）。

    Args:
        path: `configs/dashboard.yaml` 路径。

    Returns:
        通过 `DashboardConfig` 校验的配置实例。

    Raises:
        ConfigError: 文件不是有效 YAML 映射。
        ValidationError: 字段缺失、类型不符或监听地址非回环。
    """
    return load_config(DashboardConfig, path)
