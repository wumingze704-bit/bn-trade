"""通知配置模型。

规格：docs/07-实盘与风控.md §8；configs/live.yaml 的 notify 节
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from trade.common.config import ConfigError

Provider = Literal["wxpusher", "wecom", "feishu"]


class ChannelConfig(BaseModel):
    """单个通知通道配置（07 §8.2/§8.3/§8.4）。"""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    enabled: bool = True
    token_env: str | None = None  # wxpusher appToken / feishu bot token
    uid_env: str | None = None  # wxpusher UID
    key_env: str | None = None  # wecom webhook key
    events: list[str] = Field(default_factory=list)
    rate_per_min: int = Field(default=10, ge=1, le=1000)
    redact_amounts: bool = False
    max_bytes: int = Field(default=4096, ge=1, le=65536)


class NotifyConfig(BaseModel):
    """`notify:` 节配置（07 §8、configs/live.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    channels: list[ChannelConfig]
    heartbeat_min: int = Field(default=60, ge=1, le=1440)
    daily_report_utc: str = Field(default="00:05", pattern=r"^\d{2}:\d{2}$")


def load_notify_config(path: Path) -> NotifyConfig:
    """从 live.yaml（或其 `notify` 节）加载通知配置。

    Args:
        path: `configs/live.yaml` 路径，或仅含 `notify` 节的 YAML。

    Returns:
        通过 `NotifyConfig` 校验的配置实例。

    Raises:
        ConfigError: 文件无法读取、不是映射或缺少 `notify` 节。
        ValidationError: 字段缺失、类型不符或事件/限流配置非法。
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"无法读取配置 {path}: {error}") from error
    if not isinstance(values, dict):
        raise ConfigError(f"配置必须为 YAML 映射：{path}")
    notify = values.get("notify")
    if notify is None and "channels" in values:
        notify = values  # 文件本身就是 notify 节
    if notify is None or not isinstance(notify, dict):
        raise ConfigError(f"配置缺少 notify 节：{path}")
    return NotifyConfig.model_validate(notify)
