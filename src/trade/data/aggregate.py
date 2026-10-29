"""aggTrades 归集逐笔 → 分钟原子量聚合。

规格：docs/03-数据与选币.md §2.2；docs/04-特征与标签.md §2（执行类原子量）
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

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


def aggregate_aggtrades(
    chunks: Iterable[pd.DataFrame],
    whale_thr: float,
    prev_close: float | None = None,
) -> pd.DataFrame:
    """把 aggTrades 分块流式聚合为分钟原子量（跨块同分钟自动合并）。

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
    return frame


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


def read_aggtrades_csv(path: str | Path, *, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    """读取 aggTrades CSV，处理表头有无与时间戳单位，分块返回。

    Args:
        path: CSV 文件路径。
        chunksize: 每块行数。

    Yields:
        每块标准化后的 aggTrades DataFrame。
    """
    file_path = Path(path)
    with file_path.open(encoding="utf-8") as handle:
        first_line = handle.readline()
    header = 0 if has_header(first_line) else None
    reader = pd.read_csv(file_path, chunksize=chunksize, header=header)
    for chunk in reader:
        if header is None:
            chunk.columns = list(AGGTRADES_COLUMNS)
        else:
            chunk = chunk.rename(columns=str.strip)
        chunk["transact_time"] = normalize_timestamp_ms(chunk["transact_time"])
        yield coerce_aggtrades(chunk)


def read_aggtrades_zip(path: str | Path, *, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    """从日度 zip 读取 aggTrades CSV，分块返回（03 §2.2）。

    与 `read_aggtrades_csv` 相同的表头检测与时间戳归一逻辑。
    """
    import zipfile

    file_path = Path(path)
    with zipfile.ZipFile(file_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"zip 内没有 CSV：{file_path}")
        csv_name = csv_names[0]
        with archive.open(csv_name) as handle:
            first_line = handle.readline().decode("utf-8").strip()
    header = 0 if has_header(first_line) else None
    reader = pd.read_csv(file_path, compression="zip", chunksize=chunksize, header=header)
    for chunk in reader:
        if header is None:
            chunk.columns = list(AGGTRADES_COLUMNS)
        else:
            chunk = chunk.rename(columns=str.strip)
        chunk["transact_time"] = normalize_timestamp_ms(chunk["transact_time"])
        yield coerce_aggtrades(chunk)
