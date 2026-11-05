"""模型与训练配置（configs/train.yaml 的 model 段）。

规格：docs/05-模型与训练.md §3、§4、§5、§6
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelConfig(BaseModel):
    """训练超参与种子集成设置（05 §6；超参只能来自 train.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    arch: str = Field(default="tcn", pattern=r"^(tcn|lstm|lgbm)$")
    window_L: int = Field(default=60, ge=1, le=1440)  # noqa: N815 - 与 05 §1 文档名一致
    hidden: int = Field(default=64, ge=8, le=512)
    blocks: int = Field(default=4, ge=1, le=12)
    kernel: int = Field(default=3, ge=2, le=9)
    dropout: float = Field(default=0.2, ge=0.0, lt=1.0)
    loss: str = Field(default="bce", pattern=r"^(bce|focal)$")
    focal_gamma: float = Field(default=2.0, gt=0.0, le=10.0)
    lambda_reg: float = Field(default=0.3, ge=0.0, le=10.0)
    learning_rate: float = Field(default=1.0e-3, gt=0.0, le=1.0)
    weight_decay: float = Field(default=1.0e-4, ge=0.0)
    batch_size: int = Field(default=2048, ge=1, le=65536)
    max_epochs: int = Field(default=30, ge=1, le=1000)
    early_stopping_patience: int = Field(default=5, ge=1, le=100)
    warmup_epochs: int = Field(default=1, ge=0, le=10)
    grad_clip_norm: float = Field(default=1.0, gt=0.0)
    bf16: bool = True
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44, 45, 46])
    data_placement: str = Field(default="gpu", pattern=r"^(gpu|pinned_cpu)$")
    balance: str = Field(default="loss_weight", pattern=r"^(loss_weight|sampler)$")
    time_decay_halflife_months: int | None = Field(default=None, ge=1, le=240)
    lgbm_num_boost_round: int = Field(default=500, ge=1)
    lgbm_early_stopping_rounds: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def _check_seeds(self) -> ModelConfig:
        if not self.seeds:
            raise ValueError("seeds 不能为空（05 §6 第 4 条种子集成）")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds 不能重复")
        if self.arch == "tcn" and self.window_L > 120:
            raise ValueError("TCN 感受野（L ≤ 120）与 window_L 不匹配（05 §3、§6.1）")
        return self


class WalkForwardConfig(BaseModel):
    """Walk-Forward 折设置（03 §6、05 §6）。"""

    model_config = ConfigDict(extra="forbid")

    window: str = Field(default="expanding", pattern=r"^(expanding|rolling_12m)$")
    purge_bars: int = Field(default=60, ge=0)
    embargo_days: int = Field(default=1, ge=0, le=30)
    fold_train_start: str = "2024-10"
    max_folds: int = Field(default=5, ge=1, le=24)
