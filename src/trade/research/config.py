"""研究框架配置模型（docs/13-研究框架.md §2）。

字段与 `configs/research.yaml` 一一对应，未知字段报错（红线 12：缺配置直接抛错）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trade.common.config import ConfigError, load_config
from trade.common.sizing import SizingConfig


class ResearchPaths(BaseModel):
    """研究数据与产物目录（相对仓库根）。"""

    model_config = ConfigDict(extra="forbid")

    klines_1h: Path
    klines_1d: Path
    klines_1h_flow: Path
    premium_1h: Path
    mark_1h: Path
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
    liq_cap_hours: int = Field(ge=1, le=720)
    max_positions: int = Field(ge=1, le=50)


class QualityConfig(BaseModel):
    """选币第 7 步的数据质量门槛（docs/13 §1）。"""

    model_config = ConfigDict(extra="forbid")

    missing_ratio_max: float = Field(gt=0.0, le=1.0)


class RiskConfig(BaseModel):
    """研究、最终检验的 R6/R8 停机方式（docs/13 §2、§4）。

    影子盘与实盘固定 `strict`（docs/07 §6），不读取本节的取值。
    """

    model_config = ConfigDict(extra="forbid")

    halt_mode: str = Field(pattern=r"^(strict|reset)$")
    halt_reset_days: int = Field(ge=1, le=90)


class EquityConfig(BaseModel):
    """两种权益口径，USDT（docs/13 §2）。

    `signal`：研究与最终检验的达标判定、选参、Deflated Sharpe 与试验登记使用；
    `exec`：可执行性检查、档位选择、影子盘与实盘使用。
    """

    model_config = ConfigDict(extra="forbid")

    signal: float = Field(gt=0.0, le=1_000_000.0)
    exec: float = Field(gt=0.0, le=100_000.0)

    @model_validator(mode="after")
    def _check_basis(self) -> EquityConfig:
        """可执行口径必须小于信号口径（docs/13 §2）。"""
        if self.exec >= self.signal:
            raise ValueError("equity.exec 必须小于 equity.signal（docs/13 §2）")
        return self


_RISK_TIER_NAMES = ("standard", "progressive", "aggressive")


class RiskTierConfig(BaseModel):
    """单个风险档位的仓位与停机参数（docs/13 §2，数值由使用者确定，红线 5）。"""

    model_config = ConfigDict(extra="forbid")

    risk_per_trade: float = Field(ge=0.001, le=0.05)
    target_vol: float = Field(gt=0.0, le=2.0)
    disaster_loss_cap: float = Field(gt=0.0, le=0.10)
    daily_loss_stop: float = Field(gt=0.0, lt=1.0)
    daily_loss_halt: float = Field(gt=0.0, lt=1.0)
    max_drawdown_halt: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_thresholds(self) -> RiskTierConfig:
        """日亏损停止必须早于日亏损熔断（docs/06 §5.4 校验 4）。"""
        if self.daily_loss_stop >= self.daily_loss_halt:
            raise ValueError("daily_loss_stop 必须小于 daily_loss_halt（docs/13 §2）")
        if self.daily_loss_halt >= self.max_drawdown_halt:
            raise ValueError("daily_loss_halt 必须小于 max_drawdown_halt（docs/13 §2）")
        return self


class ResearchConfig(BaseModel):
    """`configs/research.yaml` 的 pydantic 模型。"""

    model_config = ConfigDict(extra="forbid")

    paths: ResearchPaths
    rolling: RollingConfig
    bootstrap: BootstrapConfig
    execution: ExecutionConfig
    portfolio: PortfolioConfig
    quality: QualityConfig
    risk: RiskConfig
    equity: EquityConfig
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


class ResearchSizingConfig(SizingConfig):
    """`configs/sizing_research.yaml` 的模型：风险包络 + 三个风险档位（docs/13 §2）。

    顶层各项是研究默认（标准档）的取值；`risk_tiers` 给出三档参数，研究默认 `tier: standard`，
    冻结候选时按 1 号第九部分第 2 条在三档中选择实盘用的档位（红线 5：数值由使用者确定）。
    """

    model_config = ConfigDict(extra="forbid")

    tier: str = Field(pattern=r"^(standard|progressive|aggressive)$")
    risk_tiers: dict[str, RiskTierConfig]

    @model_validator(mode="after")
    def _check_tiers(self) -> ResearchSizingConfig:
        """三个档位必须齐全且不得有多余档位（docs/13 §2）。"""
        missing = [name for name in _RISK_TIER_NAMES if name not in self.risk_tiers]
        if missing:
            raise ValueError(f"risk_tiers 缺少档位：{missing}（docs/13 §2）")
        unknown = sorted(set(self.risk_tiers) - set(_RISK_TIER_NAMES))
        if unknown:
            raise ValueError(f"risk_tiers 含未知档位：{unknown}（docs/13 §2）")
        return self

    def selected_tier(self) -> RiskTierConfig:
        """返回当前选中档位的参数。"""
        return self.risk_tiers[self.tier]

    def effective_max_risk_per_trade(self) -> float:
        """单笔止损亏损硬上限：取 0.025 与该档防灾止损单笔亏损上限的较大者（docs/13 §3）。"""
        return max(0.025, self.selected_tier().disaster_loss_cap)

    def with_tier(self) -> SizingConfig:
        """把选中档位的仓位与停机阈值应用到风险包络，返回纯 `SizingConfig`。

        Returns:
            SizingConfig，`risk_per_trade`、`max_risk_per_trade`、`target_vol`
            与 R5~R8 阈值取所选档位。
        """
        tier = self.selected_tier()
        values = self.model_dump(exclude={"tier", "risk_tiers"})
        values.update(
            risk_per_trade=tier.risk_per_trade,
            max_risk_per_trade=self.effective_max_risk_per_trade(),
            target_vol=tier.target_vol,
            daily_loss_stop=tier.daily_loss_stop,
            daily_loss_halt=tier.daily_loss_halt,
            max_drawdown_halt=tier.max_drawdown_halt,
        )
        return SizingConfig.model_validate(values)


def load_research_sizing(path: Path) -> ResearchSizingConfig:
    """加载并校验研究期风险包络与风险档位。

    Args:
        path: `configs/sizing_research.yaml` 路径。

    Returns:
        ResearchSizingConfig。

    Raises:
        ConfigError: 文件不可读或校验失败。
    """
    try:
        return load_config(ResearchSizingConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载研究期风险包络 {path}: {error}") from error
