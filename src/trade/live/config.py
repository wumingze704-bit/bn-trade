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


class EngineConfig(BaseModel):
    """低频引擎的路径、节拍与阈值（07 §6、§12；configs/live.yaml engine 节）。

    所有字段都有默认值，旧版 `configs/live.yaml`（没有 engine 节）仍可加载；
    运行引擎时建议在 YAML 中显式写出，便于核查。
    """

    model_config = ConfigDict(extra="forbid")

    # 交接文件
    ledger: Path = Path("./docs/research/ledger.yaml")
    candidates_dir: Path = Path("./docs/research/candidates")
    final_dir: Path = Path("./docs/research/final")
    shadow_dir: Path = Path("./docs/research/shadow")
    # 研究与基础配置（费率、执行成本、组合上限、停机方式）
    research: Path = Path("./configs/research.yaml")
    base: Path = Path("./configs/base.yaml")
    backtest: Path = Path("./configs/backtest.yaml")
    sizing_research: Path = Path("./configs/sizing_research.yaml")
    # 研究数据目录：回补 1h / 1d K 线与资金费
    klines_1h: Path = Path("./data/klines_1h")
    klines_1d: Path = Path("./data/klines_1d")
    funding: Path = Path("./data/funding")
    universe_hist: Path = Path("./data/universe_hist")
    # 实盘密钥环境变量文件（只检查权限，不读取内容；docs/07 §5）
    env_file: Path = Path("./.env.live")
    # 候选用到 premium_1h 时开启溢价指数回补（每币每小时多一次 REST 请求）
    with_premium: bool = False
    # 历史回补长度（天）：1h 取策略最长回看 + 缓冲；1d 取 30 天成交额中位数的 2 倍以上
    history_days_hourly: int = Field(default=45, ge=3, le=400)
    history_days_daily: int = Field(default=120, ge=35, le=800)
    funding_limit: int = Field(default=1000, ge=10, le=1000)
    # 资金与运行参数
    live_capital_cap: float = Field(default=500.0, gt=0.0, le=1000.0)
    hide_pnl_until_final: bool = True
    rebalance_delay_sec: int = Field(default=30, ge=0, le=600)
    post_only_wait_sec: int = Field(default=45, ge=1, le=600)
    ioc_tick_buffer: int = Field(default=2, ge=0, le=20)
    heartbeat_minutes: int = Field(default=5, ge=1, le=60)
    loop_sleep_sec: int = Field(default=20, ge=1, le=300)
    # 风控阈值（07 §6、§7）
    max_order_errors_1h: int = Field(default=5, ge=1, le=100)
    clock_drift_ms: int = Field(default=1000, ge=1, le=60_000)
    spread_median_mult: float = Field(default=5.0, gt=1.0, le=50.0)
    spread_lookback_hours: int = Field(default=24, ge=1, le=720)
    net_exposure_cap: float = Field(default=0.5, gt=0.0, le=2.0)
    net_exposure_grace_min: int = Field(default=10, ge=1, le=240)
    fee_reject_ratio: float = Field(default=1.2, gt=1.0, le=5.0)
    # 日报（UTC）与看门狗
    daily_report_utc: str = Field(default="00:10", pattern=r"^\d{2}:\d{2}$")
    watchdog_stale_min: int = Field(default=15, ge=1, le=240)


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
    engine: EngineConfig = Field(default_factory=EngineConfig)


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
