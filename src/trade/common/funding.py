"""资金费结算事件提取与区间求和（回测与标签共用）。

规格：docs/06-回测引擎.md §3.3；docs/03-数据与选币.md §2.3
"""

from __future__ import annotations

import numpy as np
import pandas as pd  # type: ignore[import-untyped]


def funding_settlements(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """提取 bars 中的资金费结算事件 (funding_time, funding_rate)（03 §2.3、06 §3.3）。

    bars 的 `funding_rate`/`funding_time` 已按「最近已结算一期」向后对齐（03 §2.3），
    相邻两次结算之间多根 K 线共享同一个 `funding_time`；对 `funding_time` 保序去重即得
    结算时刻序列，每个时刻的费率就是该期结算费率。列缺失时返回空数组（资金费计 0）。
    """
    if "funding_time" not in bars.columns or "funding_rate" not in bars.columns:
        return np.empty(0, dtype="int64"), np.empty(0, dtype="float64")
    ft = pd.to_numeric(bars["funding_time"], errors="coerce").to_numpy(dtype="float64")
    fr = pd.to_numeric(bars["funding_rate"], errors="coerce").to_numpy(dtype="float64")
    valid = np.isfinite(ft) & np.isfinite(fr)
    ft = ft[valid].astype("int64")
    fr = fr[valid]
    if ft.size == 0:
        return np.empty(0, dtype="int64"), np.empty(0, dtype="float64")
    order = np.argsort(ft, kind="stable")
    ft, fr = ft[order], fr[order]
    unique_idx = np.nonzero(np.r_[True, ft[1:] != ft[:-1]])[0]
    return ft[unique_idx], fr[unique_idx]


def funding_rate_between(
    times: np.ndarray, rates: np.ndarray, entry_ts: int, exit_ts: int
) -> float:
    """持仓跨越的资金费结算费率之和（06 §3.3）。

    结算时刻 `ft` 满足 `entry_ts < ft <= exit_ts` 时该持仓在结算时点仍持有，记一次
    资金费（多头按 `PnL -= funding_rate × 名义价值`，费率为正时支付）。返回跨越的各期
    费率之和，乘名义价值即资金费金额。
    """
    if times.size == 0:
        return 0.0
    lo = int(np.searchsorted(times, entry_ts, side="right"))
    hi = int(np.searchsorted(times, exit_ts, side="right"))
    return float(rates[lo:hi].sum())
