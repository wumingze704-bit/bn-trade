"""分钟 bars 加载与时间切分辅助。

规格：docs/03-数据与选币.md §2.3、§5、§6
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from trade.common.config import BaseConfig

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
