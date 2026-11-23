"""研究框架配置模型（docs/13-研究框架.md §2）。

字段与 `configs/research.yaml` 一一对应，未知字段报错（红线 12：缺配置直接抛错）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trade.common.config import ConfigError, load_config


class ResearchPaths(BaseModel):
    """研究数据与产物目录（相对仓库根）。"""

    model_config = ConfigDict(extra="forbid")

    klines_1h: Path
    klines_1d: Path
    funding: Path
    universe_hist: Path
    research_cache: Path
    experiments_log: Path
    artifacts: Path
    data_root: Path


class RollingConfig(BaseModel):
    """滚动验证折数（docs/13 §6）。"""

    model_config = ConfigDict(extra="forbid")

    train_months: int = Field(ge=6, le=36)
    folds: int = Field(ge=2, le=10)


class BootstrapConfig(BaseModel):
    """日分块 bootstrap（docs/13 §7）。"""

    model_config = ConfigDict(extra="forbid")

    iters: int = Field(ge=100, le=100_000)
    seed: int = Field(ge=0)


class ExecutionConfig(BaseModel):
    """执行模拟与成本（docs/13 §4）。"""

    model_config = ConfigDict(extra="forbid")

    half_spread_top_n: int = Field(ge=1)
    half_spread_top_bp: float = Field(gt=0.0, le=50.0)
    half_spread_other_bp: float = Field(gt=0.0, le=50.0)
    slip_min_bp: float = Field(gt=0.0, le=50.0)
    impact_y: float = Field(ge=0.0, le=10.0)
    impact_vol_hours: int = Field(ge=1, le=720)
    stop_slip_mult: float = Field(ge=1.0, le=10.0)
    cooldown_hours: int = Field(ge=0, le=168)
    delist_penalty: float = Field(ge=0.0, le=0.1)
    stop_max: float = Field(gt=0.0, le=0.5)


class PortfolioConfig(BaseModel):
    """组合层上限（docs/13 §3）。"""

    model_config = ConfigDict(extra="forbid")

    total_notional_cap: float = Field(gt=0.0, le=10.0)
    net_exposure_cap: float = Field(gt=0.0, le=10.0)
    margin_cap: float = Field(gt=0.0, le=1.0)
    neutral_max_gap: float = Field(gt=0.0, le=1.0)
    liq_cap_hours: int = Field(ge=1, le=720)
    max_positions: int = Field(ge=1, le=50)


class QualityConfig(BaseModel):
    """选币第 7 步的数据质量门槛（docs/13 §1）。"""

    model_config = ConfigDict(extra="forbid")

    missing_ratio_max: float = Field(gt=0.0, le=1.0)


class ResearchConfig(BaseModel):
    """`configs/research.yaml` 的 pydantic 模型。"""

    model_config = ConfigDict(extra="forbid")

    paths: ResearchPaths
    rolling: RollingConfig
    bootstrap: BootstrapConfig
    execution: ExecutionConfig
    portfolio: PortfolioConfig
    quality: QualityConfig
    scenarios: list[str] = Field(min_length=1)


def load_research_config(path: Path) -> ResearchConfig:
    """加载并校验研究配置。

    Args:
        path: `configs/research.yaml` 路径。

    Returns:
        ResearchConfig。

    Raises:
        ConfigError: 文件不可读或校验失败。
    """
    try:
        return load_config(ResearchConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载研究配置 {path}: {error}") from error
