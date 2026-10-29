"""规则策略 R0 信号生成（无模型下限）。

规格：docs/05-模型与训练.md §2；信号契约：docs/06-回测引擎.md §2
"""

from __future__ import annotations

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from trade.features.kline import brk_60, tbr_5, vol_z_60
from trade.labels.prefilter import prefilter_mask


class R0Config(BaseModel):
    """configs/train.yaml 的 r0 段（05 §2，阈值事先固定，不调参）。"""

    model_config = ConfigDict(extra="forbid")

    brk_60: float = Field(default=0.0)
    tbr_5: float = Field(default=0.60, ge=0.0, le=1.0)
    vol_z_60: float = Field(default=2.0)


class TrainConfig(BaseModel):
    """configs/train.yaml 的 pydantic 模型（P3 仅含 R0 与阈值，P4 继续扩展）。"""

    model_config = ConfigDict(extra="forbid")

    r0: R0Config = Field(default_factory=R0Config)
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


def r0_signals_for_symbol(
    bars: pd.DataFrame,
    r0: R0Config,
    *,
    vol_z_60_thr: float,
    r1_sigma: float,
    brk_60_thr: float,
    warmup_bars: int,
    symbol: str,
) -> pd.DataFrame:
    """为单个币生成 R0 信号（05 §2）。

    R0：预过滤通过 且 brk_60 > 0 且 tbr_5 ≥ 0.60 且 vol_z_60 ≥ 2.0 → p=1，否则 p=0。
    输出行为通过预过滤的样本分钟（is_sample=1）。

    Args:
        bars: 该币分钟 bars（含 03 §2.3 字段）。
        r0: R0 阈值（train.yaml）。
        vol_z_60_thr/r1_sigma/brk_60_thr: 预过滤阈值（features.yaml，04 §6）。
        warmup_bars: 预热根数，期内不产出样本。
        symbol: 币种符号。

    Returns:
        DataFrame，列 = [symbol, open_time, p, m, is_sample]。
    """
    n = len(bars)
    open_time = bars["open_time"].to_numpy(dtype="int64")
    is_filled = (
        bars["is_filled"].to_numpy(dtype="int64")
        if "is_filled" in bars.columns
        else np.zeros(n, dtype="int64")
    )
    warmed = np.arange(n) >= warmup_bars
    sample = prefilter_mask(bars, vol_z_60_thr, r1_sigma, brk_60_thr) & (is_filled == 0) & warmed

    b = brk_60(bars["high"].to_numpy(dtype="float64"), bars["close"].to_numpy(dtype="float64"))
    t5 = tbr_5(
        bars["taker_buy_quote_volume"].to_numpy(dtype="float64"),
        bars["quote_volume"].to_numpy(dtype="float64"),
    )
    v = vol_z_60(bars["quote_volume"].to_numpy(dtype="float64"))

    rule = (b > r0.brk_60) & (t5 >= r0.tbr_5) & (v >= r0.vol_z_60)
    p = np.where(sample & rule, 1.0, 0.0)
    m = np.zeros(n, dtype="float64")

    idx = np.nonzero(sample)[0]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "open_time": open_time[idx],
            "p": p[idx],
            "m": m[idx],
            "is_sample": np.ones(idx.shape[0], dtype="int64"),
        }
    )
