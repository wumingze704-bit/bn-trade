"""分钟 bars 加载与时间切分辅助。

规格：docs/03-数据与选币.md §2.3、§5、§6
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trade.common.config import BaseConfig
from trade.data.universe import SnapshotCoin, UniverseSnapshot, read_universe_snapshot

# K 线 12 列去掉 ignore（03 §1.2）
KLINE_COLUMNS = (
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

# aggTrades 分钟原子量（03 §2.2）
ATOMIC_COLUMNS = (
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

# 必需列（特征 + 标签所需）；metrics/funding 为可选列（03 §2.3）
REQUIRED_BAR_COLUMNS = (*KLINE_COLUMNS, *ATOMIC_COLUMNS, "is_filled")
OPTIONAL_BAR_COLUMNS = ("oi", "funding_rate", "funding_time")

# 一天毫秒数（用于从数据窗口推导预热起点，03 §6）
_MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class WindowBounds:
    """回测数据时间窗口（均为 UTC 毫秒）。"""

    start_ms: int
    end_ms: int
    holdout_start_ms: int


def _month_start_ms(year: int, month: int) -> int:
    """返回某 UTC 月 1 日 00:00 的毫秒时间戳。"""
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """返回 (year, month) 偏移 delta 个月后的年月。"""
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def window_bounds(base_cfg: BaseConfig) -> WindowBounds:
    """从 base.yaml 的数据窗口推导 (start_ms, end_ms, holdout_start_ms)（03 §6）。

    Args:
        base_cfg: 基础配置，含 data_window（end_month、months、warmup_days、
            holdout_months）。

    Returns:
        WindowBounds：start_ms 含预热期，end_ms 为 M24 之后一个月起点，
        holdout_start_ms 为封存集 M23 起点。
    """
    end_year, end_month = (int(part) for part in base_cfg.data_window.end_month.split("-"))
    months = base_cfg.data_window.months
    holdout_months = base_cfg.data_window.holdout_months
    m1_year, m1_month = _shift_month(end_year, end_month, -(months - 1))
    start_ms = _month_start_ms(m1_year, m1_month) - base_cfg.data_window.warmup_days * _MS_PER_DAY
    end_ms = _month_start_ms(*_shift_month(end_year, end_month, 1))
    holdout_start_ms = _month_start_ms(*_shift_month(end_year, end_month, -(holdout_months - 1)))
    return WindowBounds(start_ms=start_ms, end_ms=end_ms, holdout_start_ms=holdout_start_ms)


def _validate_columns(bars: pd.DataFrame, alias: str) -> None:
    missing = [col for col in REQUIRED_BAR_COLUMNS if col not in bars.columns]
    if missing:
        raise ValueError(f"bars 缺少必需列（{alias}）：{missing}")


def load_bars(
    data_root: Path,
    alias: str,
    start_ms: int,
    end_ms: int,
    *,
    holdout_start_ms: int,
    allow_holdout: bool = False,
) -> pd.DataFrame:
    """读取 [start_ms, end_ms) 的分钟数据（03 §5）。

    跨月自动拼接，open_time 升序 int64（不设为索引），去重并校验 schema。

    Args:
        data_root: 数据根目录（data_root/bars/{alias}/{YYYYMM}.parquet）。
        alias: 币种别名（如 C01）。
        start_ms: 起点毫秒（含）。
        end_ms: 终点毫秒（不含）。
        holdout_start_ms: 封存集起点（M23，必填）；请求与其重叠且
            allow_holdout=False 时抛错。必填是为了防止调用方漏传守卫（03 §6、
            AGENTS.md §6 红线 2）。
        allow_holdout: 是否允许读取封存集（仅 `--final` 可开，03 §6）。

    Raises:
        ValueError: 请求与封存集重叠、无数据或缺少必需列。
    """
    if not allow_holdout and end_ms > holdout_start_ms:
        raise ValueError("请求范围与封存集重叠，且 allow_holdout=False（03 §6）")

    bars_dir = data_root / "bars" / alias
    files = sorted(bars_dir.glob("*.parquet"))
    if not files:
        raise ValueError(f"未找到 bars 文件：{bars_dir}")

    frames = [pd.read_parquet(path) for path in files]
    bars = pd.concat(frames, ignore_index=True)
    bars = bars[(bars["open_time"] >= start_ms) & (bars["open_time"] < end_ms)]
    bars = bars.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    if bars.empty:
        raise ValueError(f"区间内无分钟数据：{alias} [{start_ms}, {end_ms})")
    _validate_columns(bars, alias)
    return bars


def month_key(open_time_ms: int) -> str:
    """返回 open_time 所属 UTC 月的 "YYYY-MM"。"""
    day = datetime.fromtimestamp(open_time_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}"


def _month_number(open_time_ms: int) -> int:
    """返回 year*12 + month 的整数（便于比较）。"""
    day = datetime.fromtimestamp(open_time_ms // 1000, tz=UTC)
    return day.year * 12 + day.month


def month_index(open_time_ms: int, end_month: str, months: int) -> int | None:
    """返回 1-based 月序号 M1..M{months}；不在窗口内返回 None（03 §6）。"""
    end_year, end_month_num = (int(part) for part in end_month.split("-"))
    end_number = end_year * 12 + end_month_num
    number = _month_number(open_time_ms)
    start_number = end_number - months + 1
    if number < start_number or number > end_number:
        return None
    return number - start_number + 1


def segment_of(
    open_time_ms: int,
    end_month: str,
    months: int,
    tuning_months: int,
    holdout_months: int,
) -> str:
    """返回分钟所属切分段：warmup / tuning / test / holdout（03 §6）。"""
    idx = month_index(open_time_ms, end_month, months)
    if idx is None:
        return "warmup"
    if idx > months - holdout_months:
        return "holdout"
    if idx <= tuning_months:
        return "tuning"
    return "test"


def universe_months(base_cfg: BaseConfig) -> list[str]:
    """返回数据窗口内的 M1..M{months} 月字符串（03 §6）。"""
    end_year, end_month = (int(part) for part in base_cfg.data_window.end_month.split("-"))
    year, month = _shift_month(end_year, end_month, -(base_cfg.data_window.months - 1))
    months: list[str] = []
    for _ in range(base_cfg.data_window.months):
        months.append(f"{year:04d}-{month:02d}")
        year, month = _shift_month(year, month, 1)
    return months


def rebalance_ms_of(month: str) -> int:
    """返回某月（YYYY-MM）1 日 00:00 UTC 的毫秒时间戳（03 §4.2）。"""
    year, month_num = (int(part) for part in month.split("-"))
    return _month_start_ms(year, month_num)


def load_universe_snapshot(data_root: Path, month: str) -> UniverseSnapshot:
    """返回某月的交易范围快照（03 §4.2、§5）。"""
    return read_universe_snapshot(data_root / "universe" / f"{month}.json")


def collect_universe_coins(data_root: Path, months: Sequence[str]) -> list[SnapshotCoin]:
    """合并各月快照的入选币，按 symbol 去重（哈希角色与别名跨月稳定，03 §4.5）。"""
    seen: dict[str, SnapshotCoin] = {}
    for month in months:
        snapshot = load_universe_snapshot(data_root, month)
        for coin in snapshot.coins:
            seen.setdefault(coin.symbol, coin)
    return [seen[symbol] for symbol in sorted(seen)]


def list_daily_symbols(data_root: Path) -> list[str]:
    """列出全市场日线目录中的 symbol（03 §2.1）。"""
    daily_root = data_root / "daily"
    if not daily_root.is_dir():
        return []
    return sorted(path.name for path in daily_root.iterdir() if path.is_dir())


def load_daily_bars(data_root: Path, symbol: str) -> pd.DataFrame:
    """读取某 symbol 的日线（data_root/daily/{symbol}/*.parquet 或 *.csv，03 §2.1）。"""
    daily_dir = data_root / "daily" / symbol
    files = sorted([*daily_dir.glob("*.parquet"), *daily_dir.glob("*.csv")])
    if not files:
        raise ValueError(f"未找到日线文件：{daily_dir}")
    frames: list[pd.DataFrame] = []
    for path in files:
        frames.append(pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path))
    bars = pd.concat(frames, ignore_index=True)
    if "open_time" not in bars.columns:
        raise ValueError(f"日线缺少 open_time 列：{daily_dir}")
    return bars.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def segment_ids(open_time: np.ndarray) -> np.ndarray:
    """返回每根 K 线的数据段编号（03 §6）。

    同一币的分钟数据按「连续可得区间」划分为若干段；相邻 `open_time` 间隔超过 1 分钟
    （即两次入选之间超过 30 天未下载的月份空档）视为段界。段内滚动窗口与 EWM 状态
    不得跨越段界，段首 `warmup_days` 天只作预热、不产生样本。

    Args:
        open_time: 升序 int64 毫秒时间戳数组。

    Returns:
        与输入等长的 int64 段编号（从 0 起，连续递增）。
    """
    open_time = np.asarray(open_time, dtype="int64")
    if open_time.shape[0] == 0:
        return np.empty(0, dtype="int64")
    boundaries = np.r_[False, np.diff(open_time) > 60_000]
    return np.cumsum(boundaries).astype("int64")


def load_universe_membership(data_root: Path, months: Sequence[str]) -> dict[str, frozenset[str]]:
    """返回 month -> 该月入选 symbol 集合（03 §4.2），供按月交易范围过滤。"""
    membership: dict[str, frozenset[str]] = {}
    for month in months:
        snapshot = load_universe_snapshot(data_root, month)
        membership[month] = frozenset(coin.symbol for coin in snapshot.coins)
    return membership


def universe_trade_mask(
    open_time: np.ndarray,
    symbol: str,
    membership: Mapping[str, frozenset[str]],
    new_entry_delay_days: int,
) -> np.ndarray:
    """返回该 symbol 每分钟是否可交易（06 §3.1 条件 8、03 §4.2）。

    当月入选 `U(R)` 的分钟可交易；若上月不在 `U(R)`（新进入），则自再平衡 `R` 起
    `new_entry_delay_days` 天内不可开仓。不在任何入选月份的分钟不可交易（只用于特征预热）。

    Args:
        open_time: 升序 int64 毫秒时间戳数组。
        symbol: 币种符号（如 `SOLUSDT`）。
        membership: month -> 入选 symbol 集合（`load_universe_membership`）。
        new_entry_delay_days: 新进入币的监控不开仓天数（`universe.yaml`）。

    Returns:
        与输入等长的 bool 数组，True 表示该分钟可交易。
    """
    open_time = np.asarray(open_time, dtype="int64")
    n = open_time.shape[0]
    if n == 0:
        return np.zeros(0, dtype=bool)
    months = sorted(membership)
    month_str = pd.to_datetime(open_time, unit="ms", utc=True).strftime("%Y-%m").to_numpy()
    mask = np.zeros(n, dtype=bool)
    delay_ms = new_entry_delay_days * _MS_PER_DAY
    for i, month in enumerate(months):
        if symbol not in membership[month]:
            continue
        in_month = month_str == month
        if i == 0 or symbol not in membership[months[i - 1]]:
            in_month &= open_time >= rebalance_ms_of(month) + delay_ms
        mask |= in_month
    return mask


def new_entry_symbols(
    membership: Mapping[str, frozenset[str]], tuning_months: int
) -> frozenset[str]:
    """返回「训练段从未入选、测试段才首次入选」的 symbol 集合（03 §4.5、06 §7 S10）。

    Args:
        membership: month -> 入选 symbol 集合。
        tuning_months: 调参期月数（前 tuning_months 个月视为训练段）。

    Returns:
        新进入组的 symbol 集合。
    """
    months = sorted(membership)
    tuning = set(months[:tuning_months])
    symbols = {symbol for month in membership for symbol in membership[month]}
    new_entry: set[str] = set()
    for symbol in symbols:
        first = next((month for month in months if symbol in membership[month]), None)
        if first is not None and first not in tuning:
            new_entry.add(symbol)
    return frozenset(new_entry)
