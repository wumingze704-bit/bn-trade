"""aggTrades 归集逐笔 → 分钟原子量聚合。

规格：docs/03-数据与选币.md §2.2；docs/04-特征与标签.md §2（执行类原子量）
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trade.data._aggregate_numba import aggregate_arrays_numba

# aggTrades CSV 列名（03 §1.3）
AGGTRADES_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)

# 分钟原子量列顺序（03 §2.2）
MINUTE_ATOMIC_COLUMNS = (
    "open_time",
    "agg_n",
    "buy_quote",
    "sell_quote",
    "max_trade_quote",
    "max_buy_1s_quote",
    "whale_buy_quote",
    "whale_sell_quote",
    "whale_thr",
    "p_1s",
    "p_1s_filled",
    "p_3s",
    "p_3s_filled",
    "hi_post_1s",
    "lo_post_1s",
    "hi_post_3s",
    "lo_post_3s",
)

# K 线 12 列（03 §1.2），1m 与 1d 共用
KLINE_CSV_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
_DAY_MS = 86_400_000


def coerce_aggtrades(df: pd.DataFrame) -> pd.DataFrame:
    """把 aggTrades DataFrame 的列类型标准化。

    Args:
        df: 原始 aggTrades（可能含字符串布尔）。

    Returns:
        列类型标准化后的副本（price/quantity float，transact_time int64，is_buyer_maker bool）。
    """
    out = df.copy()
    out["price"] = out["price"].astype(float)
    out["quantity"] = out["quantity"].astype(float)
    out["transact_time"] = out["transact_time"].astype("int64")
    if out["is_buyer_maker"].dtype != bool:
        out["is_buyer_maker"] = out["is_buyer_maker"].astype(str).str.lower() == "true"
    return out


def _finalize_minute(
    open_time: int,
    trades: list[tuple[int, float, float, bool]],
    whale_thr: float,
    prev_close: float | None,
) -> dict[str, float | int]:
    """把一个分钟内的逐笔聚合为原子量。

    Args:
        open_time: 分钟起点（毫秒）。
        trades: `(transact_time, price, quantity, is_buyer_maker)` 列表，按时间升序。
        whale_thr: 大单阈值。
        prev_close: 上一分钟收盘价（p_1s/p_3s 无成交时的回退值）。

    Returns:
        该分钟的原子量字典。
    """
    quote_values = [price * qty for _, price, qty, _ in trades]
    buy_quote = sum(
        qv for (_, _, _, maker), qv in zip(trades, quote_values, strict=False) if not maker
    )
    sell_quote = sum(
        qv for (_, _, _, maker), qv in zip(trades, quote_values, strict=False) if maker
    )
    max_trade_quote = max(quote_values)
    whale_buy_quote = sum(
        qv
        for (_, _, _, maker), qv in zip(trades, quote_values, strict=False)
        if not maker and qv >= whale_thr
    )
    whale_sell_quote = sum(
        qv
        for (_, _, _, maker), qv in zip(trades, quote_values, strict=False)
        if maker and qv >= whale_thr
    )

    buy_by_second: dict[int, float] = {}
    for (ts, _price, _qty, maker), qv in zip(trades, quote_values, strict=False):
        if not maker:
            second = ts // 1000
            buy_by_second[second] = buy_by_second.get(second, 0.0) + qv
    max_buy_1s_quote = max(buy_by_second.values(), default=0.0)

    def entry_price(limit_ms: int) -> tuple[float, int]:
        prices = [price for ts, price, _, _ in trades if ts <= open_time + limit_ms]
        if prices:
            return prices[-1], 0
        return prev_close if prev_close is not None else float("nan"), 1

    def post_high_low(limit_ms: int, fallback: float) -> tuple[float, float]:
        prices = [price for ts, price, _, _ in trades if ts > open_time + limit_ms]
        if prices:
            return max(prices), min(prices)
        return fallback, fallback

    p_1s, p_1s_filled = entry_price(1000)
    p_3s, p_3s_filled = entry_price(3000)
    hi_post_1s, lo_post_1s = post_high_low(1000, p_1s)
    hi_post_3s, lo_post_3s = post_high_low(3000, p_3s)

    return {
        "open_time": open_time,
        "agg_n": len(trades),
        "buy_quote": buy_quote,
        "sell_quote": sell_quote,
        "max_trade_quote": max_trade_quote,
        "max_buy_1s_quote": max_buy_1s_quote,
        "whale_buy_quote": whale_buy_quote,
        "whale_sell_quote": whale_sell_quote,
        "whale_thr": whale_thr,
        "p_1s": p_1s,
        "p_1s_filled": p_1s_filled,
        "p_3s": p_3s,
        "p_3s_filled": p_3s_filled,
        "hi_post_1s": hi_post_1s,
        "lo_post_1s": lo_post_1s,
        "hi_post_3s": hi_post_3s,
        "lo_post_3s": lo_post_3s,
    }


def aggregate_aggtrades_python(
    chunks: Iterable[pd.DataFrame],
    whale_thr: float,
    prev_close: float | None = None,
) -> pd.DataFrame:
    """纯 Python 参考实现，供 Numba 版本逐字段对拍（03 §2.2）。

    Args:
        chunks: 按时间升序的 aggTrades DataFrame 分块（可迭代）。
        whale_thr: 本段数据使用的大单阈值（03 §2.2，应为前一日 p99）。
        prev_close: 第一分钟 `p_1s`/`p_3s` 无成交时的上一分钟收盘价；省略时为 NaN。

    Returns:
        一行一分钟的原子量 DataFrame，列见 MINUTE_ATOMIC_COLUMNS。
    """
    rows: list[dict[str, float | int]] = []
    current_minute: int | None = None
    current_trades: list[tuple[int, float, float, bool]] = []
    last_minute_close = prev_close

    for chunk in chunks:
        df = coerce_aggtrades(chunk)
        for row in df.itertuples(index=False, name="T"):
            ts = int(row.transact_time)
            minute = ts // 60000 * 60000
            if current_minute is None:
                current_minute = minute
            elif minute != current_minute:
                rows.append(
                    _finalize_minute(current_minute, current_trades, whale_thr, last_minute_close)
                )
                last_minute_close = current_trades[-1][1]
                current_minute = minute
                current_trades = []
            current_trades.append(
                (ts, float(row.price), float(row.quantity), bool(row.is_buyer_maker))
            )

    if current_trades:
        rows.append(_finalize_minute(current_minute, current_trades, whale_thr, last_minute_close))

    if not rows:
        return pd.DataFrame(columns=MINUTE_ATOMIC_COLUMNS)

    frame = pd.DataFrame(rows).reindex(columns=MINUTE_ATOMIC_COLUMNS)
    frame["open_time"] = frame["open_time"].astype("int64")
    frame["agg_n"] = frame["agg_n"].astype("int64")
    frame["p_1s_filled"] = frame["p_1s_filled"].astype("int64")
    frame["p_3s_filled"] = frame["p_3s_filled"].astype("int64")
    integer_columns = {"open_time", "agg_n", "p_1s_filled", "p_3s_filled"}
    for column in MINUTE_ATOMIC_COLUMNS:
        if column not in integer_columns:
            frame[column] = frame[column].astype("float64")
    return frame


@dataclass(frozen=True)
class DayAggregation:
    """单日 aggTrades 的分钟原子量与精确大单阈值。"""

    minutes: pd.DataFrame
    p99_quote: float
    n_trades: int


def _frame_from_matrix(values: np.ndarray) -> pd.DataFrame:
    """将 Numba 输出矩阵转换为稳定 schema 与计数类型。"""
    frame = pd.DataFrame(values, columns=MINUTE_ATOMIC_COLUMNS)
    for column in ("open_time", "agg_n", "p_1s_filled", "p_3s_filled"):
        frame[column] = frame[column].astype("int64")
    return frame


def aggregate_aggtrades_day(
    chunks: Iterable[pd.DataFrame],
    whale_thr: float,
    prev_close: float | None = None,
) -> DayAggregation:
    """单遍消费一天的 aggTrades 块，汇总分钟原子量并精确计算当天 p99（03 §2.2）。

    Args:
        chunks: 按 `transact_time` 升序的一次性 DataFrame 块迭代器。
        whale_thr: 当天使用的前一日 p99；缺失时传 NaN，不回退到当日阈值。
        prev_close: 日首之前最近一分钟的 K 线收盘价。

    Returns:
        `DayAggregation`，含分钟结果、当日单笔成交额精确 99 分位数与逐笔数。
    """
    quote_chunks: list[np.ndarray] = []
    minute_chunks: list[np.ndarray] = []
    pending_times: np.ndarray | None = None
    pending_prices: np.ndarray | None = None
    pending_quantities: np.ndarray | None = None
    pending_makers: np.ndarray | None = None
    last_close = prev_close if prev_close is not None else float("nan")
    has_last_close = prev_close is not None
    trade_count = 0

    for chunk in chunks:
        normalized = coerce_aggtrades(chunk)
        times = normalized["transact_time"].to_numpy(dtype="int64")
        prices = normalized["price"].to_numpy(dtype="float64")
        quantities = normalized["quantity"].to_numpy(dtype="float64")
        makers = normalized["is_buyer_maker"].to_numpy(dtype=np.bool_)
        if not len(times):
            continue
        quote_chunks.append(prices * quantities)
        trade_count += len(times)

        if pending_times is not None:
            times = np.concatenate((pending_times, times))
            prices = np.concatenate((pending_prices, prices))
            quantities = np.concatenate((pending_quantities, quantities))
            makers = np.concatenate((pending_makers, makers))
        minute_keys = times // 60_000
        complete_count = int(np.searchsorted(minute_keys, minute_keys[-1], side="left"))
        if complete_count:
            minute_chunks.append(
                aggregate_arrays_numba(
                    times[:complete_count],
                    prices[:complete_count],
                    quantities[:complete_count],
                    makers[:complete_count],
                    whale_thr,
                    last_close,
                    has_last_close,
                )
            )
            last_close = float(prices[complete_count - 1])
            has_last_close = True
        pending_times = times[complete_count:]
        pending_prices = prices[complete_count:]
        pending_quantities = quantities[complete_count:]
        pending_makers = makers[complete_count:]

    if pending_times is not None and len(pending_times):
        minute_chunks.append(
            aggregate_arrays_numba(
                pending_times,
                pending_prices,
                pending_quantities,
                pending_makers,
                whale_thr,
                last_close,
                has_last_close,
            )
        )

    if not quote_chunks:
        return DayAggregation(pd.DataFrame(columns=MINUTE_ATOMIC_COLUMNS), float("nan"), 0)
    quote_values = np.concatenate(quote_chunks)
    quote_chunks.clear()
    quantile_position = 0.99 * (trade_count - 1)
    lower_index = int(np.floor(quantile_position))
    upper_index = int(np.ceil(quantile_position))
    quote_values.partition((lower_index, upper_index))
    fraction = quantile_position - lower_index
    p99_quote = float(
        quote_values[lower_index]
        + fraction * (quote_values[upper_index] - quote_values[lower_index])
    )
    minute_values = np.concatenate(minute_chunks, axis=0)
    return DayAggregation(_frame_from_matrix(minute_values), p99_quote, trade_count)


def aggregate_aggtrades(
    chunks: Iterable[pd.DataFrame],
    whale_thr: float,
    prev_close: float | None = None,
) -> pd.DataFrame:
    """调用单遍 Numba 汇总器，返回分钟原子量（03 §2.2）。"""
    return aggregate_aggtrades_day(chunks, whale_thr, prev_close).minutes


def fill_missing_minutes(
    bars: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    prev_close: float | None = None,
) -> pd.DataFrame:
    """补齐 `[start_ms, end_ms)` 区间内缺失的分钟。

    缺失分钟的价格 OHLC 用上一分钟收盘价填充、成交量填 0，并置 `is_filled = 1`（03 §1.1 第 6 条）。

    Args:
        bars: 已聚合的分钟 DataFrame，须含 `open_time` 升序列。
        start_ms: 期望起点（毫秒，须对齐到分钟）。
        end_ms: 期望终点（毫秒，开区间，须对齐到分钟）。
        prev_close: 区间起点之前的上一个收盘价；省略时用 NaN。

    Returns:
        补齐后的 DataFrame（仍按 open_time 升序）。
    """
    if bars.empty:
        existing: dict[int, pd.DataFrame] = {}
        first_close = prev_close
    else:
        sorted_bars = bars.sort_values("open_time")
        existing = {int(row.open_time): row for row in sorted_bars.itertuples(index=False)}
        first_close = prev_close

    out_rows: list[dict[str, object]] = []
    running_close = first_close
    minute = start_ms
    while minute < end_ms:
        row = existing.get(minute)
        if row is not None:
            out_rows.append(dict(row._asdict()))
            if "close" in row._asdict():
                running_close = float(row.close)
            else:
                running_close = float(row.p_1s) if not pd.isna(row.p_1s) else running_close
        else:
            out_rows.append(_missing_minute_row(minute, running_close))
        minute += 60_000
    return pd.DataFrame(out_rows)


def _missing_minute_row(open_time: int, close: float | None) -> dict[str, object]:
    """构造补齐分钟行（OHLC=上一收盘价，成交量为 0，is_filled=1）。"""
    price = close if close is not None else float("nan")
    return {
        "open_time": open_time,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 0.0,
        "is_filled": 1,
    }


def _align_metrics(bars: pd.DataFrame, metrics: pd.DataFrame | None) -> pd.DataFrame:
    """按 metrics 的真实可用时刻滞后对齐 OI（04 §2 第 2 条）。"""
    if metrics is None or metrics.empty:
        bars["oi"] = np.nan
        return bars
    source = metrics.copy()
    if "timestamp_ms" in source:
        timestamp_ms = normalize_timestamp_ms(source["timestamp_ms"])
    elif "create_time" in source:
        timestamp_ms = (
            pd.to_datetime(source["create_time"], utc=True).dt.as_unit("ms").astype("int64")
        )
    else:
        raise ValueError("metrics 缺少 timestamp_ms/create_time 时间列")
    oi_column = "oi" if "oi" in source else "sum_open_interest"
    if oi_column not in source:
        raise ValueError("metrics 缺少 oi/sum_open_interest 列")
    source["_available_time_ms"] = timestamp_ms + 300_000
    source["oi"] = pd.to_numeric(source[oi_column], errors="coerce")
    source = source[["_available_time_ms", "oi"]].dropna(subset=["oi"])
    source = source.sort_values("_available_time_ms").drop_duplicates(
        "_available_time_ms", keep="last"
    )
    return pd.merge_asof(
        bars.sort_values("close_time"),
        source,
        left_on="close_time",
        right_on="_available_time_ms",
        direction="backward",
    ).drop(columns="_available_time_ms")


def _align_funding(bars: pd.DataFrame, funding: pd.DataFrame | None) -> pd.DataFrame:
    """只把结算时间不晚于 K 线收盘的最近一期资金费率向后对齐（04 §2 第 3 条）。"""
    if funding is None or funding.empty:
        bars["funding_rate"] = np.nan
        bars["funding_time"] = pd.Series(pd.NA, index=bars.index, dtype="Int64")
        return bars
    source = funding.copy()
    if "funding_time" in source:
        timestamp_ms = pd.to_numeric(source["funding_time"], errors="raise").astype("int64")
    elif "funding_time_ms" in source:
        timestamp_ms = pd.to_numeric(source["funding_time_ms"], errors="raise").astype("int64")
    elif "calc_time" in source:
        timestamp_ms = pd.to_numeric(source["calc_time"], errors="raise").astype("int64")
    else:
        raise ValueError("fundingRate 缺少 funding_time/funding_time_ms/calc_time 时间列")
    timestamp_ms = normalize_timestamp_ms(timestamp_ms)
    rate_column = "funding_rate" if "funding_rate" in source else "last_funding_rate"
    if rate_column not in source:
        raise ValueError("fundingRate 缺少 funding_rate/last_funding_rate 列")
    source["funding_time"] = timestamp_ms
    source["funding_rate"] = pd.to_numeric(source[rate_column], errors="coerce")
    source = source[["funding_time", "funding_rate"]].dropna(subset=["funding_rate"])
    source = source.sort_values("funding_time").drop_duplicates("funding_time", keep="last")
    return pd.merge_asof(
        bars.sort_values("close_time"),
        source,
        left_on="close_time",
        right_on="funding_time",
        direction="backward",
    )


def assemble_bars(
    klines: pd.DataFrame,
    atomic: pd.DataFrame,
    *,
    start_ms: int,
    end_ms: int,
    metrics: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
    previous_close: float | None = None,
    aggtrades_missing_days: Iterable[int] = (),
    whale_threshold_missing_days: Iterable[int] = (),
) -> pd.DataFrame:
    """组装一个完整 UTC 月的 K 线、逐笔原子量、metrics 与 settled funding（03 §2.3）。

    Args:
        klines: K 线数据，含 03 §1.2 字段；`ignore` 列可选。
        atomic: aggTrades 分钟原子量，列见 `MINUTE_ATOMIC_COLUMNS`。
        start_ms: UTC 分钟对齐的月起点（毫秒，含）。
        end_ms: UTC 分钟对齐的区间终点（毫秒，不含）。
        metrics: 可选 `timestamp_ms`/`create_time` 与 `oi`/`sum_open_interest`。
        funding: 可选 `funding_time`/`funding_time_ms`/`calc_time` 与费率列。
        previous_close: 月首之前最近一根 K 线收盘价，用于缺失价格回退。
        aggtrades_missing_days: UTC 日起点毫秒，归档文件缺失且沿用最近 p99 的日期。
        whale_threshold_missing_days: UTC 日起点毫秒，缺少可用前一日 p99 的日期。

    Returns:
        每分钟一行、可由 `load_bars` 读取的完整 DataFrame。

    Raises:
        ValueError: 时间范围未按分钟对齐或输入缺少必需列。
    """
    if start_ms % 60_000 or end_ms % 60_000 or end_ms <= start_ms:
        raise ValueError("bars 区间必须为正且按 UTC 分钟对齐")
    kline_columns = (
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    )
    kline_source = klines.drop(columns="ignore", errors="ignore").copy()
    missing_columns = [column for column in kline_columns if column not in kline_source]
    if missing_columns:
        raise ValueError(f"K 线缺少必需列：{missing_columns}")
    kline_source["open_time"] = normalize_timestamp_ms(kline_source["open_time"])
    kline_source["close_time"] = normalize_timestamp_ms(kline_source["close_time"])
    kline_source = kline_source.sort_values("open_time").drop_duplicates("open_time", keep="last")
    grid = pd.DataFrame({"open_time": np.arange(start_ms, end_ms, 60_000, dtype="int64")})
    bars = grid.merge(kline_source[list(kline_columns)], on="open_time", how="left", sort=True)
    kline_missing = bars["close"].isna()
    prior_close = bars["close"].shift().ffill()
    if len(prior_close):
        prior_close.iloc[0] = previous_close
    for column in ("open", "high", "low", "close"):
        bars[column] = bars[column].where(~kline_missing, prior_close)
    for column in ("volume", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume"):
        bars.loc[kline_missing, column] = 0
    bars["close_time"] = bars["close_time"].fillna(bars["open_time"] + 59_999).astype("int64")
    bars["kline_missing"] = kline_missing.astype("int8")

    atomic_source = atomic.copy()
    absent_atomic_columns = [
        column for column in MINUTE_ATOMIC_COLUMNS if column not in atomic_source
    ]
    if absent_atomic_columns:
        raise ValueError(f"原子量缺少必需列：{absent_atomic_columns}")
    atomic_source["open_time"] = atomic_source["open_time"].astype("int64")
    atomic_source = atomic_source.sort_values("open_time").drop_duplicates("open_time", keep="last")
    bars = bars.merge(atomic_source[list(MINUTE_ATOMIC_COLUMNS)], on="open_time", how="left")
    atomic_missing = bars["agg_n"].isna() & ~kline_missing & (bars["quote_volume"] > 0)
    prior_price = bars["close"].shift().ffill()
    if len(prior_price):
        prior_price.iloc[0] = previous_close
    for column in MINUTE_ATOMIC_COLUMNS:
        if column in (
            "open_time",
            "whale_thr",
            "p_1s",
            "p_3s",
            "p_1s_filled",
            "p_3s_filled",
            "hi_post_1s",
            "lo_post_1s",
            "hi_post_3s",
            "lo_post_3s",
        ):
            continue
        bars[column] = bars[column].fillna(0)
    bars["whale_thr"] = bars["whale_thr"].ffill()
    for column in ("p_1s", "p_3s", "hi_post_1s", "lo_post_1s", "hi_post_3s", "lo_post_3s"):
        bars.loc[atomic_missing, column] = prior_price.loc[atomic_missing]
    bars.loc[atomic_missing, ["p_1s_filled", "p_3s_filled"]] = 1
    bars["aggtrades_missing"] = atomic_missing.astype("int8")
    bars["is_filled"] = (kline_missing | atomic_missing).astype("int8")
    day_starts = bars["open_time"] // _DAY_MS * _DAY_MS
    bars["aggtrades_source_missing"] = day_starts.isin(set(aggtrades_missing_days)).astype("int8")
    bars["whale_threshold_missing"] = day_starts.isin(set(whale_threshold_missing_days)).astype(
        "int8"
    )
    bars = _align_metrics(bars, metrics)
    bars = _align_funding(bars, funding)
    bars = bars.sort_values("open_time").reset_index(drop=True)
    return bars


def write_monthly_bars(bars: pd.DataFrame, output_path: Path) -> None:
    """先写临时 parquet 再原子替换月文件（03 §2.3）。

    Args:
        bars: `assemble_bars` 返回的单月分钟数据。
        output_path: `data/bars/{alias}/{YYYYMM}.parquet` 目标路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        bars.to_parquet(temporary_path, index=False, compression="zstd")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def daily_whale_thresholds(trades: pd.DataFrame, date_col: str = "transact_time") -> pd.DataFrame:
    """第一遍：计算每个 UTC 日的单笔成交额 99 分位数（03 §2.2）。

    Args:
        trades: aggTrades DataFrame（含 price、quantity、transact_time）。
        date_col: 交易时间列名。

    Returns:
        每 UTC 日一行的 `(date, p99_quote, n_trades)` DataFrame。
    """
    df = coerce_aggtrades(trades)
    df["date"] = pd.to_datetime(df[date_col], unit="ms", utc=True).dt.date
    df["quote"] = df["price"] * df["quantity"]
    grouped = df.groupby("date").agg(
        p99_quote=("quote", lambda s: float(s.quantile(0.99))),
        n_trades=("quote", "size"),
    )
    return grouped.reset_index()


def normalize_timestamp_ms(values: pd.Series) -> pd.Series:
    """把时间戳归一为毫秒：值 > 1e14 视为微秒，除以 1000（03 §1.1 第 2 条）。"""
    as_int = values.astype("int64")
    if as_int.max() > 10**14:
        return as_int // 1000
    return as_int


def has_header(first_line: str) -> bool:
    """检测 CSV 首行是否为表头：首列非数字即表头（03 §1.1 第 1 条）。"""
    first_token = first_line.split(",", 1)[0].strip()
    return not first_token.replace(".", "", 1).replace("-", "", 1).isdigit()


def _read_arrow_chunks(source: Any, *, header: bool) -> Iterable[pd.DataFrame]:
    """按固定块与显式类型读取四个聚合所需列（03 §2.2）。"""
    import pyarrow as pa
    import pyarrow.csv as pa_csv

    read_options = pa_csv.ReadOptions(
        block_size=32 * 1024 * 1024,
        column_names=list(AGGTRADES_COLUMNS),
        skip_rows=1 if header else 0,
    )
    convert_options = pa_csv.ConvertOptions(
        include_columns=["price", "quantity", "transact_time", "is_buyer_maker"],
        column_types={
            "price": pa.float64(),
            "quantity": pa.float64(),
            "transact_time": pa.int64(),
            "is_buyer_maker": pa.bool_(),
        },
        true_values=["true", "True", "TRUE"],
        false_values=["false", "False", "FALSE"],
    )
    with pa.PythonFile(source, mode="r") as arrow_source:
        reader = pa_csv.open_csv(
            arrow_source,
            read_options=read_options,
            parse_options=pa_csv.ParseOptions(delimiter=","),
            convert_options=convert_options,
        )
        for batch in reader:
            chunk = batch.to_pandas()
            chunk["transact_time"] = normalize_timestamp_ms(chunk["transact_time"])
            yield coerce_aggtrades(chunk)


def read_aggtrades_csv(path: str | Path) -> Iterable[pd.DataFrame]:
    """读取 aggTrades CSV，按 32 MiB 块处理表头与时间戳单位。"""
    file_path = Path(path)
    with file_path.open(encoding="utf-8") as handle:
        header = has_header(handle.readline())
    with file_path.open("rb") as handle:
        yield from _read_arrow_chunks(handle, header=header)


def read_aggtrades_zip(path: str | Path) -> Iterable[pd.DataFrame]:
    """从日度 ZIP 流式读取 aggTrades，兼容表头与时间戳单位（03 §2.2）。"""
    import zipfile

    file_path = Path(path)
    with zipfile.ZipFile(file_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"zip 内没有 CSV：{file_path}")
        csv_name = csv_names[0]
        with archive.open(csv_name) as handle:
            header = has_header(handle.readline().decode("utf-8").strip())
        with archive.open(csv_name) as handle:
            yield from _read_arrow_chunks(handle, header=header)


def read_klines_zip(path: str | Path) -> pd.DataFrame:
    """读取 Binance K 线月度 ZIP（1m/1d 通用），兼容表头与时间戳单位（03 §1.1）。

    Args:
        path: K 线 zip 文件路径。

    Returns:
        11 列 DataFrame（去掉 `ignore`），`open_time`/`close_time` 为 int64 毫秒，
        数值列为 float64，按 `open_time` 升序去重。

    Raises:
        ValueError: zip 内没有 CSV，或列数不符。
    """
    import zipfile

    file_path = Path(path)
    with zipfile.ZipFile(file_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"zip 内没有 CSV：{file_path}")
        with archive.open(csv_names[0]) as handle:
            header = 0 if has_header(handle.readline().decode("utf-8").strip()) else None
    frame = pd.read_csv(file_path, compression="zip", header=header)
    if header is None:
        if len(frame.columns) != len(KLINE_CSV_COLUMNS):
            raise ValueError(
                f"CSV 列数不符：{file_path}，期望 {len(KLINE_CSV_COLUMNS)}，"
                f"实际 {len(frame.columns)}"
            )
        frame.columns = list(KLINE_CSV_COLUMNS)
    frame = frame.rename(columns=lambda column: str(column).strip())
    for column in ("open_time", "close_time"):
        frame[column] = normalize_timestamp_ms(frame[column]).astype("int64")
    frame = frame.drop(columns="ignore", errors="ignore")
    for column in frame.columns:
        if column not in ("open_time", "close_time"):
            frame[column] = frame[column].astype("float64")
    return (
        frame.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
