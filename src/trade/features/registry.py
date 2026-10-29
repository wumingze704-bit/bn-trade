"""特征注册表与特征配置模型。

规格：docs/04-特征与标签.md §3、§4、§5、§6、§7
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from trade.features import context, flow, futures, kline

# 执行类原子量列（03 §2.2）：只用于标签与成交模拟，禁止作为特征（04 §2 第 8 条）。
EXECUTION_ATOMIC_COLUMNS = frozenset(
    {
        "p_1s",
        "p_1s_filled",
        "p_3s",
        "p_3s_filled",
        "hi_post_1s",
        "lo_post_1s",
        "hi_post_3s",
        "lo_post_3s",
    }
)

# 特征函数的输入：coin 数组 + btc 数组（按 open_time 对齐，等长）。
RawFeatureFn = Callable[[dict[str, np.ndarray], dict[str, np.ndarray]], np.ndarray]


@dataclass(frozen=True)
class Feature:
    """单个特征注册项（04 §3）。"""

    name: str
    group: str
    func: RawFeatureFn
    lookback: int
    norm: str

    def __post_init__(self) -> None:
        if self.norm not in {"none", "ewm_z"}:
            raise ValueError(f"非法 norm：{self.name}:{self.norm}")
        if self.name in EXECUTION_ATOMIC_COLUMNS:
            raise ValueError(f"执行类原子量不得作为特征：{self.name}")


class EwmConfig(BaseModel):
    """EWM 标准化参数（04 §5）。"""

    model_config = ConfigDict(extra="forbid")

    halflife_bars: int = Field(default=4320, ge=1)
    clip: float = Field(default=5.0, gt=0.0)
    min_var: float = Field(default=1e-12, gt=0.0)


class FeatureSwitches(BaseModel):
    """特征开关（04 §4.5）。"""

    model_config = ConfigDict(extra="forbid")

    disabled: list[str] = Field(default_factory=list)


class PrefilterCfg(BaseModel):
    """预过滤阈值与告警区间（04 §6）。"""

    model_config = ConfigDict(extra="forbid")

    vol_z_60: float = Field(default=1.0)
    r1_sigma: float = Field(default=2.0, gt=0.0)
    brk_60: float = Field(default=0.0)
    retention_min: float = Field(default=0.05, ge=0.0, le=1.0)
    retention_max: float = Field(default=0.60, ge=0.0, le=1.0)


class LabelCfg(BaseModel):
    """三重障碍标签参数（04 §7）。"""

    model_config = ConfigDict(extra="forbid")

    k_sl: float = Field(default=1.0, gt=0.0)
    rr: float = Field(default=2.0, gt=0.0)
    sl_min: float = Field(default=0.005, gt=0.0, le=1.0)
    sl_max: float = Field(default=0.015, gt=0.0, le=1.0)
    H_label: int = Field(default=60, ge=1)
    entry_latency_ms: int = Field(default=1000)
    c_rt: float = Field(default=0.0012, ge=0.0)
    r_mult_clip: tuple[float, float] = (-1.5, 3.0)
    fill_ratio_max: float = Field(default=0.10, ge=0.0, le=1.0)
    positive_min: float = Field(default=0.10, ge=0.0, le=1.0)
    positive_max: float = Field(default=0.50, ge=0.0, le=1.0)


class FeaturesConfig(BaseModel):
    """configs/features.yaml 的 pydantic 模型。"""

    model_config = ConfigDict(extra="forbid")

    ewm: EwmConfig = Field(default_factory=EwmConfig)
    warmup_bars: int = Field(default=43200, ge=0)
    features: FeatureSwitches = Field(default_factory=FeatureSwitches)
    prefilter: PrefilterCfg = Field(default_factory=PrefilterCfg)
    label: LabelCfg = Field(default_factory=LabelCfg)


# 全部 37 个特征（04 §4.5），顺序与文档一致。
FEATURES: tuple[Feature, ...] = (
    # K 线类（group = kline）
    Feature("r_1", "kline", lambda c, b: kline.r_1(c["close"]), 2, "ewm_z"),
    Feature("r_5", "kline", lambda c, b: kline.r_5(c["close"]), 6, "ewm_z"),
    Feature("r_15", "kline", lambda c, b: kline.r_15(c["close"]), 16, "ewm_z"),
    Feature("r_60", "kline", lambda c, b: kline.r_60(c["close"]), 61, "ewm_z"),
    Feature("rng", "kline", lambda c, b: kline.rng(c["high"], c["low"], c["close"]), 2, "ewm_z"),
    Feature(
        "body",
        "kline",
        lambda c, b: kline.body(c["open"], c["high"], c["low"], c["close"]),
        1,
        "none",
    ),
    Feature(
        "up_wick",
        "kline",
        lambda c, b: kline.up_wick(c["high"], c["low"], c["open"], c["close"]),
        1,
        "none",
    ),
    Feature("vol_z_60", "kline", lambda c, b: kline.vol_z_60(c["quote_volume"]), 60, "none"),
    Feature(
        "vol_ratio_1440",
        "kline",
        lambda c, b: kline.vol_ratio_1440(c["quote_volume"]),
        1440,
        "ewm_z",
    ),
    Feature(
        "tbr",
        "kline",
        lambda c, b: kline.tbr(c["taker_buy_quote_volume"], c["quote_volume"]),
        1,
        "none",
    ),
    Feature(
        "tbr_5",
        "kline",
        lambda c, b: kline.tbr_5(c["taker_buy_quote_volume"], c["quote_volume"]),
        5,
        "none",
    ),
    Feature(
        "cvd_norm",
        "kline",
        lambda c, b: kline.cvd_norm(c["taker_buy_quote_volume"], c["quote_volume"]),
        60,
        "ewm_z",
    ),
    Feature(
        "cvd_15",
        "kline",
        lambda c, b: kline.cvd_15(c["taker_buy_quote_volume"], c["quote_volume"]),
        60,
        "ewm_z",
    ),
    Feature("velocity", "kline", lambda c, b: kline.velocity(c["count"]), 60, "ewm_z"),
    Feature(
        "avg_trade_ratio",
        "kline",
        lambda c, b: kline.avg_trade_ratio(c["quote_volume"], c["count"]),
        60,
        "ewm_z",
    ),
    Feature("rv_15", "kline", lambda c, b: kline.rv_15(c["close"]), 16, "ewm_z"),
    Feature("rv_ratio", "kline", lambda c, b: kline.rv_ratio(c["close"]), 241, "ewm_z"),
    Feature(
        "atr_pct", "kline", lambda c, b: kline.atr_pct(c["high"], c["low"], c["close"]), 14, "ewm_z"
    ),
    Feature("brk_60", "kline", lambda c, b: kline.brk_60(c["high"], c["close"]), 61, "ewm_z"),
    Feature("brk_240", "kline", lambda c, b: kline.brk_240(c["high"], c["close"]), 241, "ewm_z"),
    Feature("dd_60", "kline", lambda c, b: kline.dd_60(c["low"], c["close"]), 61, "ewm_z"),
    Feature(
        "vwap_dev_60",
        "kline",
        lambda c, b: kline.vwap_dev_60(c["quote_volume"], c["volume"], c["close"]),
        60,
        "ewm_z",
    ),
    # 订单流类（group = flow）
    Feature(
        "whale_ratio",
        "flow",
        lambda c, b: flow.whale_ratio(
            c["buy_quote"], c["sell_quote"], c["whale_buy_quote"], c["whale_sell_quote"]
        ),
        1,
        "none",
    ),
    Feature(
        "whale_net",
        "flow",
        lambda c, b: flow.whale_net(
            c["buy_quote"], c["sell_quote"], c["whale_buy_quote"], c["whale_sell_quote"]
        ),
        1,
        "none",
    ),
    Feature(
        "whale_net_15",
        "flow",
        lambda c, b: flow.whale_net_15(
            c["buy_quote"], c["sell_quote"], c["whale_buy_quote"], c["whale_sell_quote"]
        ),
        15,
        "none",
    ),
    Feature(
        "burst_1s",
        "flow",
        lambda c, b: flow.burst_1s(c["max_buy_1s_quote"], c["quote_volume"]),
        60,
        "ewm_z",
    ),
    Feature(
        "max_trade_rel",
        "flow",
        lambda c, b: flow.max_trade_rel(c["max_trade_quote"], c["whale_thr"]),
        1,
        "ewm_z",
    ),
    # 市场环境类（group = context）
    Feature("btc_r_1", "context", lambda c, b: context.btc_r_1(b["close"]), 2, "ewm_z"),
    Feature("btc_r_15", "context", lambda c, b: context.btc_r_15(b["close"]), 16, "ewm_z"),
    Feature("btc_rv_60", "context", lambda c, b: context.btc_rv_60(b["close"]), 61, "ewm_z"),
    Feature(
        "ex_r_15", "context", lambda c, b: context.ex_r_15(c["close"], b["close"]), 1441, "ewm_z"
    ),
    Feature("hour_sin", "context", lambda c, b: context.hour_sin(c["open_time"]), 1, "none"),
    Feature("hour_cos", "context", lambda c, b: context.hour_cos(c["open_time"]), 1, "none"),
    # 合约类（group = futures）
    Feature("oi_chg_15", "futures", lambda c, b: futures.oi_chg_15(c["oi"]), 16, "ewm_z"),
    Feature("oi_chg_60", "futures", lambda c, b: futures.oi_chg_60(c["oi"]), 61, "ewm_z"),
    Feature("funding", "futures", lambda c, b: futures.funding(c["funding_rate"]), 1, "ewm_z"),
    Feature(
        "futures_missing",
        "futures",
        lambda c, b: futures.futures_missing(c["oi"], c["funding_rate"]),
        1,
        "none",
    ),
)

FEATURES_BY_NAME: dict[str, Feature] = {feature.name: feature for feature in FEATURES}


def enabled_features(cfg: FeaturesConfig) -> list[Feature]:
    """按 features.yaml 的开关返回启用特征（保持文档顺序）。"""
    disabled = set(cfg.features.disabled)
    unknown = disabled - set(FEATURES_BY_NAME)
    if unknown:
        raise ValueError(f"features.yaml 关闭了不存在的特征：{sorted(unknown)}")
    return [feature for feature in FEATURES if feature.name not in disabled]


def feature_hash(features: Sequence[Feature]) -> str:
    """计算启用特征的有序列表哈希（04 §3）。"""
    payload = "|".join(f"{feature.name}:{feature.lookback}:{feature.norm}" for feature in features)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
