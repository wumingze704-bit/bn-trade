"""分钟数据质量报告。

规格：docs/03-数据与选币.md §2.4
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class QualityReport:
    """单个币的数据质量报告（03 §2.4）。"""

    symbol: str
    total_minutes: int
    missing_count: int
    missing_ratio: float
    max_contiguous_missing: int
    zero_trade_ratio: float
    price_jumps: int
    aggtrades_missing_count: int
    consistency_error_median: float
    max_consistency_error: float
    qualified: bool


def _longest_true_run(flags: pd.Series) -> int:
    """计算布尔序列中最长连续 True 的长度。"""
    longest = 0
    current = 0
    for value in flags:
        if bool(value):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def compute_quality(
    symbol: str,
    bars: pd.DataFrame,
    *,
    missing_ratio_max: float = 0.01,
    consistency_error_max: float = 0.02,
    price_jump_threshold: float = 0.20,
) -> QualityReport:
    """计算单个币的数据质量指标。

    Args:
        symbol: 币种（或 alias）。
        bars: 分钟 bars，须含 `open_time`、`close`、`quote_volume`、`buy_quote`、`sell_quote`；
            可选 `is_filled`（缺失补齐标记，缺失时按无缺失处理）。
        missing_ratio_max: 缺失分钟占比上限。
        consistency_error_max: aggTrades 与 K 线成交额一致性误差上限（中位数口径）。
        price_jump_threshold: 价格跳变阈值（|1m 收益|）。

    Returns:
        质量报告。
    """
    total = len(bars)
    is_filled = bars["is_filled"] if "is_filled" in bars else pd.Series(False, index=bars.index)
    missing_count = int(is_filled.astype(bool).sum())
    missing_ratio = missing_count / total if total else 0.0

    zero_trade = (bars["quote_volume"].fillna(0.0) <= 0).sum()
    zero_trade_ratio = zero_trade / total if total else 0.0

    returns = bars["close"].pct_change().abs()
    price_jumps = int((returns > price_jump_threshold).sum())

    # 成交额一致性：只在两者都有数据的分钟上计算，避免中位数掩盖缺失（03 §2.4）
    quote_volume = bars["quote_volume"].astype(float)
    has_atomic = bars["buy_quote"].notna() & bars["sell_quote"].notna()
    # klines 有成交但 aggTrades 缺失的分钟（真实成交分钟缺失，数据不完整）
    aggtrades_missing = (~has_atomic) & (quote_volume > 0)
    aggtrades_missing_count = int(aggtrades_missing.sum())

    matched = has_atomic & (quote_volume > 0)
    if bool(matched.any()):
        diff = (bars["buy_quote"] + bars["sell_quote"] - quote_volume).abs() / quote_volume.abs()
        consistency_error_median = float(diff[matched].median())
        max_consistency_error = float(diff[matched].max())
    else:
        consistency_error_median = float("nan")
        max_consistency_error = float("nan")

    qualified = (
        missing_ratio <= missing_ratio_max
        and aggtrades_missing_count == 0
        and (pd.isna(consistency_error_median) or consistency_error_median <= consistency_error_max)
    )

    return QualityReport(
        symbol=symbol,
        total_minutes=total,
        missing_count=missing_count,
        missing_ratio=missing_ratio,
        max_contiguous_missing=_longest_true_run(is_filled),
        zero_trade_ratio=zero_trade_ratio,
        price_jumps=price_jumps,
        aggtrades_missing_count=aggtrades_missing_count,
        consistency_error_median=consistency_error_median,
        max_consistency_error=max_consistency_error,
        qualified=qualified,
    )
