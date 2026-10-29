"""合约类特征（#34~#37：持仓量变化、资金费率、缺失标记）。

规格：docs/04-特征与标签.md §4.4
"""

from __future__ import annotations

import numpy as np

from trade.features.kline import shift


def oi_chg_15(oi: np.ndarray) -> np.ndarray:
    """#34 oi_chg_15 = ln(OI_t / OI_{t-15})（3 个 5 分钟周期前的对数变化）。"""
    oi_arr = np.asarray(oi, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(oi_arr / shift(oi_arr, 15))


def oi_chg_60(oi: np.ndarray) -> np.ndarray:
    """#35 oi_chg_60 = ln(OI_t / OI_{t-60})（12 个 5 分钟周期前的对数变化）。"""
    oi_arr = np.asarray(oi, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(oi_arr / shift(oi_arr, 60))


def funding(funding_rate: np.ndarray) -> np.ndarray:
    """#36 funding = 最近一期已结算资金费率。"""
    return np.asarray(funding_rate, dtype="float64")


def futures_missing(oi: np.ndarray, funding_rate: np.ndarray) -> np.ndarray:
    """#37 futures_missing = 1（持仓量或资金费率缺失），否则 0。"""
    oi_missing = ~np.isfinite(np.asarray(oi, dtype="float64"))
    fr_missing = ~np.isfinite(np.asarray(funding_rate, dtype="float64"))
    return np.where(oi_missing | fr_missing, 1.0, 0.0)
