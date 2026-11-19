"""轮动策略配置（configs/rotation.yaml）。

规格：docs/11-轮动策略.md §2
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trade.common.config import ConfigError, load_config


class RotationConfig(BaseModel):
    """轮动策略参数（未知字段报错）。

    字段与 docs/11-轮动策略.md §2 一一对应；任何一项改动都会改变回测结果，
    冻结后不得修改（docs/rotation/phase1.md 记录冻结文件的 SHA256）。
    """

    model_config = ConfigDict(extra="forbid")

    btc_symbol: str = Field(default="BTCUSDT", pattern=r"^[A-Z0-9]{3,20}$")
    ma_days: int = Field(default=50, ge=5, le=200, description="大盘过滤 BTC 均线天数 N")
    lookback_days: int = Field(default=7, ge=1, le=90, description="动量打分回看天数 L")
    top_k: int = Field(default=5, ge=1, le=20, description="目标持仓数 K")
    buffer_mult: int = Field(
        default=2, ge=1, le=4, description="持仓缓冲区倍数（前 2K 名继续持有）"
    )
    rebalance_hours: int = Field(default=4, ge=1, le=24, description="调仓周期（小时）")
    score_lag_hours: int = Field(default=4, ge=1, le=24, description="打分截止相对调仓时刻的滞后")
    alt_index_days: int = Field(default=7, ge=1, le=30, description="山寨等权指数收益回看天数")
    min_score: float = Field(default=0.0, ge=0.0, le=10.0, description="入选最低打分")
    stop_atr_period: int = Field(default=14, ge=2, le=100, description="ATR 周期（4h 根数）")
    stop_atr_mult: float = Field(default=3.0, ge=0.5, le=8.0, description="ATR 止损倍数 k_stop")
    stop_max_pct: float = Field(
        default=0.15, gt=0.0, lt=0.5, description="止损距离硬上限（5x 逐仓强平前留余量）"
    )
    stop_cooldown_hours: int = Field(default=24, ge=1, le=168, description="止损后禁止再买入小时数")
    exit_check_hours: int = Field(
        default=0, ge=0, le=24, description="整点卖出检查周期，0 表示关闭"
    )
    funding_filter: bool = Field(default=False, description="只选资金费 ≤ 当月全市场中位数的币")
    oi_bonus: float = Field(
        default=0.0, ge=0.0, le=2.0, description="OI 变化标准化值加分系数（0 = 关闭）"
    )
    oi_bonus_days: int = Field(default=7, ge=1, le=30, description="OI 变化回看天数")

    @model_validator(mode="after")
    def _check_invariants(self) -> RotationConfig:
        """调仓与检查周期必须是 24 的约数，否则 UTC 日边界对不齐（11 §2）。"""
        if self.rebalance_hours not in (1, 4, 8):
            raise ValueError("rebalance_hours 只能是 1 / 4 / 8（11 §2）")
        if 24 % self.rebalance_hours != 0:
            raise ValueError("rebalance_hours 必须整除 24（11 §2）")
        if self.exit_check_hours and 24 % self.exit_check_hours != 0:
            raise ValueError("exit_check_hours 必须为 0 或整除 24（11 §2）")
        if self.score_lag_hours < 1:
            raise ValueError("score_lag_hours 必须 ≥ 1（11 §2）")
        return self


def load_rotation_config(path: Path) -> RotationConfig:
    """加载并校验 configs/rotation.yaml。

    Args:
        path: 配置文件路径。

    Returns:
        RotationConfig。

    Raises:
        ConfigError: 文件无法读取、含未知字段或取值越界。
    """
    try:
        return load_config(RotationConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载轮动配置 {path}: {error}") from error
