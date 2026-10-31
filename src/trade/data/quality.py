"""分钟数据质量报告。

规格：docs/03-数据与选币.md §2.4
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

_MINUTE_MS = 60_000
_DAY_MS = 86_400_000
_MISSING_RATIO_MAX = 0.01  # 03 §2.4
_AGGTRADES_MISSING_RATIO_MAX = 0.01  # 03 §2.4
_CONSISTENCY_ERROR_MAX = 0.02  # 03 §2.4


@dataclass(frozen=True)
class QualityReport:
    """单个币在指定时间窗口内的数据质量报告（03 §2.4）。"""

    symbol: str
    total_minutes: int
    missing_count: int
    missing_ratio: float
    max_contiguous_missing: int
    zero_trade_ratio: float
    price_jumps: int
    aggtrades_missing_count: int
    aggtrades_missing_ratio: float
    aggtrades_source_missing_days: int
    consistency_error_median: float
    max_consistency_error: float
    qualified: bool
    window_start_ms: int | None = None
    window_end_ms: int | None = None
    whale_threshold_missing_count: int = 0


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
    missing_ratio_max: float = _MISSING_RATIO_MAX,
    aggtrades_missing_ratio_max: float = _AGGTRADES_MISSING_RATIO_MAX,
    consistency_error_max: float = _CONSISTENCY_ERROR_MAX,
    price_jump_threshold: float = 0.20,
) -> QualityReport:
    """计算单个币的数据质量指标。

    Args:
        symbol: 币种（或 alias）。
        bars: 分钟 bars，须含 `open_time`、`close`、`quote_volume`、`buy_quote`、`sell_quote`；
            优先使用 `kline_missing` 与 `aggtrades_missing` 区分两类缺失，否则兼容旧字段。
        missing_ratio_max: K 线缺失分钟占比上限，默认值见 03 §2.4。
        aggtrades_missing_ratio_max: 逐笔缺失分钟占比上限，默认值见 03 §2.4。
        consistency_error_max: aggTrades 与 K 线成交额一致性误差上限（中位数口径）。
        price_jump_threshold: 价格跳变阈值（|1m 收益|）。

    Returns:
        质量报告。
    """
    total = len(bars)
    if "kline_missing" in bars:
        kline_missing = bars["kline_missing"].fillna(0).astype(bool)
    elif "is_filled" in bars:
        kline_missing = bars["is_filled"].fillna(0).astype(bool)
    else:
        kline_missing = pd.Series(False, index=bars.index)
    missing_count = int(kline_missing.sum())
    missing_ratio = missing_count / total if total else 0.0

    zero_trade = (bars["quote_volume"].fillna(0.0) <= 0).sum()
    zero_trade_ratio = zero_trade / total if total else 0.0

    returns = bars["close"].pct_change().abs()
    price_jumps = int((returns > price_jump_threshold).sum())

    # 成交额一致性：只在两者都有数据的分钟上计算，避免中位数掩盖缺失（03 §2.4）
    quote_volume = bars["quote_volume"].astype(float)
    has_atomic = bars["buy_quote"].notna() & bars["sell_quote"].notna()
    if "aggtrades_missing" in bars:
        aggtrades_missing = bars["aggtrades_missing"].fillna(0).astype(bool)
    else:
        aggtrades_missing = (~has_atomic) & (quote_volume > 0)
    aggtrades_missing_count = int(aggtrades_missing.sum())
    aggtrades_missing_ratio = aggtrades_missing_count / total if total else 0.0

    matched = has_atomic & ~aggtrades_missing & (quote_volume > 0)
    if bool(matched.any()):
        diff = (bars["buy_quote"] + bars["sell_quote"] - quote_volume).abs() / quote_volume.abs()
        consistency_error_median = float(diff[matched].median())
        max_consistency_error = float(diff[matched].max())
    else:
        consistency_error_median = float("nan")
        max_consistency_error = float("nan")

    is_filled = kline_missing | aggtrades_missing
    whale_threshold_missing_count = 0
    if "whale_thr" in bars:
        trade_count = bars["agg_n"].fillna(0) if "agg_n" in bars else quote_volume
        if "whale_threshold_missing" in bars:
            whale_threshold_missing_count = int(
                bars["whale_threshold_missing"].fillna(0).astype(bool).sum()
            )
        else:
            whale_threshold_missing_count = int(
                (bars["whale_thr"].isna() & (trade_count > 0)).sum()
            )
    aggtrades_source_missing_days = 0
    if "aggtrades_source_missing" in bars and "open_time" in bars:
        missing_source_times = bars.loc[
            bars["aggtrades_source_missing"].fillna(0).astype(bool), "open_time"
        ].astype("int64")
        aggtrades_source_missing_days = int((missing_source_times // _DAY_MS).nunique())
    qualified = (
        missing_ratio <= missing_ratio_max
        and aggtrades_missing_ratio <= aggtrades_missing_ratio_max
        and not pd.isna(consistency_error_median)
        and consistency_error_median <= consistency_error_max + 1e-12
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
        aggtrades_missing_ratio=aggtrades_missing_ratio,
        aggtrades_source_missing_days=aggtrades_source_missing_days,
        consistency_error_median=consistency_error_median,
        max_consistency_error=max_consistency_error,
        qualified=qualified,
        whale_threshold_missing_count=whale_threshold_missing_count,
    )


def compute_quality_windows(
    symbol: str,
    bars: pd.DataFrame,
    window_ends_ms: Iterable[int],
    *,
    window_days: int = 30,
) -> pd.DataFrame:
    """计算指定 UTC 窗口终点之前的币种质量结论，供选币按月读取（03 §2.4）。

    Args:
        symbol: 币种 symbol 或 alias。
        bars: 已按 `open_time` 标记缺失的分钟 bars。
        window_ends_ms: UTC 窗口右边界（毫秒，不含），通常为月度再平衡时点 R。
        window_days: 窗口长度，默认 30 天。

    Returns:
        含 `symbol`、窗口边界、各质量指标与 `qualified` 的 DataFrame。

    Raises:
        ValueError: 窗口长度非正或边界未按分钟对齐。
    """
    if window_days <= 0:
        raise ValueError("window_days 必须为正数")
    if "open_time" not in bars:
        raise ValueError("bars 缺少 open_time")
    source = bars.copy()
    source["open_time"] = source["open_time"].astype("int64")
    source = source.sort_values("open_time").drop_duplicates("open_time", keep="last")
    reports: list[dict[str, object]] = []
    for window_end_ms in sorted(set(int(value) for value in window_ends_ms)):
        if window_end_ms % _MINUTE_MS:
            raise ValueError(f"窗口终点未按分钟对齐：{window_end_ms}")
        window_start_ms = window_end_ms - window_days * _DAY_MS
        selected = source[
            (source["open_time"] >= window_start_ms) & (source["open_time"] < window_end_ms)
        ].copy()
        expected = pd.DataFrame(
            {"open_time": np.arange(window_start_ms, window_end_ms, _MINUTE_MS, dtype="int64")}
        )
        expected = expected.merge(selected.assign(_bar_present=1), on="open_time", how="left")
        absent = expected["_bar_present"].isna()
        expected = expected.drop(columns="_bar_present")
        if "kline_missing" not in expected:
            expected["kline_missing"] = (
                expected["is_filled"].fillna(0) if "is_filled" in expected else 0
            )
        expected["kline_missing"] = expected["kline_missing"].fillna(0)
        expected.loc[absent, "kline_missing"] = 1
        if "aggtrades_missing" not in expected:
            expected["aggtrades_missing"] = (
                expected["buy_quote"].isna() & expected["sell_quote"].isna()
            ) & (expected["quote_volume"].fillna(0) > 0)
        expected["aggtrades_missing"] = expected["aggtrades_missing"].fillna(0)
        expected["is_filled"] = (
            expected["kline_missing"].astype(bool) | expected["aggtrades_missing"].astype(bool)
        ).astype("int8")
        expected["quote_volume"] = expected["quote_volume"].fillna(0.0)
        expected["close"] = expected["close"].ffill()
        report = compute_quality(symbol, expected)
        report = replace(
            report,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        reports.append(asdict(report))
    return pd.DataFrame(reports)


def write_quality_cache(reports: pd.DataFrame, path: Path) -> None:
    """原子写入 T0.13 可按 `(symbol, window_end_ms)` 查询的质量缓存。

    Args:
        reports: `compute_quality_windows` 返回的质量结论表。
        path: 缓存 parquet 路径。

    Raises:
        ValueError: 缺少币种、窗口边界或合格结论列。
    """
    required = {"symbol", "window_start_ms", "window_end_ms", "qualified"}
    missing = required - set(reports.columns)
    if missing:
        raise ValueError(f"质量缓存缺少主键/结论列：{sorted(missing)}")
    output = reports.drop_duplicates(["symbol", "window_end_ms"], keep="last")
    output = output.sort_values(["symbol", "window_end_ms"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        output.to_parquet(temporary_path, index=False, compression="zstd")
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_quality_cache(path: Path) -> pd.DataFrame:
    """读取质量缓存，供 T0.13 在再平衡时点按币与窗口终点筛选。

    Args:
        path: 质量缓存 parquet 路径。

    Returns:
        按 symbol 与 window_end_ms 排序的质量结论表。

    Raises:
        FileNotFoundError: 缓存文件不存在。
        ValueError: 缓存缺少查询主键或合格结论列。
    """
    if not path.is_file():
        raise FileNotFoundError(f"未找到质量缓存：{path}")
    reports = pd.read_parquet(path)
    required = {"symbol", "window_start_ms", "window_end_ms", "qualified"}
    missing = required - set(reports.columns)
    if missing:
        raise ValueError(f"质量缓存 schema 不完整：{sorted(missing)}")
    return reports.sort_values(["symbol", "window_end_ms"]).reset_index(drop=True)
