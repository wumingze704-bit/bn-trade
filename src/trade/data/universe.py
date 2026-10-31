"""规则化月度选币：配置、交易范围快照、哈希角色与别名。

规格：docs/03-数据与选币.md §4
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trade.common.config import ConfigError, load_config

_MS_PER_DAY = 86_400_000
_DAYS_7 = 7 * _MS_PER_DAY
# TradFi 合约的 underlyingType（03 §4.2 第 2 步）
_TRADFI_UNDERLYING = {"STOCK", "COMMODITY", "INDEX", "ETF", "FX"}


class RuleConfig(BaseModel):
    """选币规则（03 §4.2、§4.6 rule 段）。"""

    model_config = ConfigDict(extra="forbid")

    rebalance: str = Field(default="monthly", pattern=r"^monthly$")
    new_entry_delay_days: int = Field(default=2, ge=0, le=365)
    top_n: int = Field(default=60, ge=1)
    min_listing_days: int = Field(default=60, ge=1)
    lookback_days: int = Field(default=30, ge=1)
    min_daily_quote_median: float = Field(default=20_000_000.0, gt=0)
    step_notional_max: float = Field(default=0.5, gt=0)
    min_notional_max: float = Field(default=5.0, gt=0)
    min_leverage: int = Field(default=5, ge=1)
    tick_rel_max: float = Field(default=0.0002, gt=0)
    exclude_bases: list[str] = Field(default_factory=lambda: ["BTC", "ETH"])
    exclude_stable: bool = True
    exclude_tradfi: bool = True
    excluded_symbols: list[str] = Field(default_factory=list)


class RolesConfig(BaseModel):
    """哈希角色规则（03 §4.5 roles 段）。"""

    model_config = ConfigDict(extra="forbid")

    holdout_mod: int = Field(default=5, ge=2)
    holdout_rem: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_rem(self) -> RolesConfig:
        if self.holdout_rem >= self.holdout_mod:
            raise ValueError("holdout_rem 必须小于 holdout_mod")
        return self


class QualityRuleConfig(BaseModel):
    """选币第 7 步的数据质量门槛（03 §2.4、§4.6 quality 段）。"""

    model_config = ConfigDict(extra="forbid")

    missing_ratio_max: float = Field(default=0.01, gt=0, le=1)
    aggtrades_missing_ratio_max: float = Field(default=0.01, gt=0, le=1)
    consistency_error_max: float = Field(default=0.02, gt=0, le=1)


class UniverseConfig(BaseModel):
    """universe.yaml 的 pydantic 模型（03 §4.6）。"""

    model_config = ConfigDict(extra="forbid")

    reviewed: bool = False
    btc_context: str
    rule: RuleConfig
    roles: RolesConfig = Field(default_factory=RolesConfig)
    quality: QualityRuleConfig = Field(default_factory=QualityRuleConfig)


class SnapshotCoin(BaseModel):
    """交易范围快照中的单个入选币（03 §4.2）。"""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    alias: str
    role: str
    rank: int
    daily_quote_median: float
    first_listing_ms: int
    price_median_30d: float | None = None
    tick_rel: float | None = None
    step_notional: float | None = None
    max_leverage: int | None = None


class UniverseSnapshot(BaseModel):
    """某月的交易范围快照（03 §4.2）。"""

    model_config = ConfigDict(extra="forbid")

    month: str
    rebalance_ms: int
    rule_hash: str
    coins: list[SnapshotCoin] = Field(default_factory=list)
    excluded_counts: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class SymbolMeta:
    """单个合约用于选币的分类与交易规则（03 §4.2 第 2、5 步）。"""

    symbol: str
    base_asset: str = ""
    quote_asset: str = "USDT"
    contract_type: str = "PERPETUAL"
    underlying_type: str = ""
    is_stable: bool = False
    excluded: bool = False
    tick_size: float | None = None
    step_size: float | None = None
    min_notional: float | None = None
    max_leverage: int | None = None


def load_universe_config(path: Path) -> UniverseConfig:
    """加载并校验 universe.yaml（03 §4.6）。"""
    try:
        return load_config(UniverseConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载 universe 配置 {path}: {error}") from error


def rule_hash(cfg: UniverseConfig) -> str:
    """对规则相关配置段求 SHA256（不含 reviewed 与 btc_context，03 §4.6）。"""
    payload: dict[str, Any] = {
        "rule": cfg.rule.model_dump(),
        "roles": cfg.roles.model_dump(),
        "quality": cfg.quality.model_dump(),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def role_for_symbol(symbol: str, roles: RolesConfig) -> str:
    """哈希角色：sha256(symbol) 前 8 字节取模（03 §4.5）。"""
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16)
    return "holdout" if value % roles.holdout_mod == roles.holdout_rem else "train"


def _next_alias(aliases: dict[str, str]) -> str:
    """分配下一个未使用别名 C001…，不复用（03 §4.5）。"""
    used = set(aliases.values())
    index = 1
    while f"C{index:03d}" in used:
        index += 1
    return f"C{index:03d}"


def load_aliases(path: Path) -> dict[str, str]:
    """读取 data/universe/aliases.json（只增不改、不复用，03 §4.5）。"""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"aliases.json 必须是字符串键映射：{path}")
    return {str(key): str(value) for key, value in data.items()}


def save_aliases(path: Path, aliases: dict[str, str]) -> None:
    """原子写入 aliases.json（03 §4.5）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(aliases, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def assign_aliases(
    snapshot: UniverseSnapshot, aliases: dict[str, str]
) -> tuple[UniverseSnapshot, dict[str, str]]:
    """为快照币分配别名；已存在的不改，新币顺序追加（03 §4.5）。"""
    updated = dict(aliases)
    coins: list[SnapshotCoin] = []
    for coin in snapshot.coins:
        if coin.symbol not in updated:
            updated[coin.symbol] = _next_alias(updated)
        coins.append(coin.model_copy(update={"alias": updated[coin.symbol]}))
    return snapshot.model_copy(update={"coins": coins}), updated


def _is_excluded(meta: SymbolMeta, cfg: UniverseConfig) -> bool:
    """第 2 步排除：TradFi、BTC/ETH、稳定币对、额外排除与合约类型（03 §4.2）。"""
    rule = cfg.rule
    if meta.excluded or meta.symbol in rule.excluded_symbols:
        return True
    if meta.quote_asset != "USDT":
        return True
    if meta.contract_type != "PERPETUAL":
        return True
    if meta.base_asset in rule.exclude_bases:
        return True
    if rule.exclude_stable and meta.is_stable:
        return True
    return bool(rule.exclude_tradfi and meta.underlying_type.upper() in _TRADFI_UNDERLYING)


def _tradable(meta: SymbolMeta, price_7d: float, price_30d: float, rule: RuleConfig) -> bool:
    """第 5 步可交易性：最小下单额、杠杆、step_notional 与 tick_rel（03 §4.2）。"""
    if meta.min_notional is not None and meta.min_notional > rule.min_notional_max:
        return False
    if meta.max_leverage is not None and meta.max_leverage < rule.min_leverage:
        return False
    step_too_large = (
        meta.step_size is not None
        and not pd.isna(price_7d)
        and meta.step_size * price_7d > rule.step_notional_max
    )
    tick_too_wide = (
        meta.tick_size is not None
        and not pd.isna(price_30d)
        and meta.tick_size / price_30d > rule.tick_rel_max
    )
    return not (step_too_large or tick_too_wide)


def _median_last_days(
    before: pd.DataFrame, rebalance_ms: int, window_ms: int, column: str
) -> float:
    """取 R 前 window_ms 内 column 的日值中位数（03 §4.2）。"""
    recent = before[
        (before["open_time"] >= rebalance_ms - window_ms) & (before["open_time"] < rebalance_ms)
    ]
    if recent.empty:
        return float("nan")
    return float(recent[column].median())


def _quality_ok(quality_cache: pd.DataFrame, symbol: str, rebalance_ms: int) -> bool:
    """第 7 步：读取 T0.17 的按窗口质量结论（03 §2.4、§4.2）。"""
    rows = quality_cache[
        (quality_cache["symbol"] == symbol) & (quality_cache["window_end_ms"] == rebalance_ms)
    ]
    if rows.empty:
        return False
    return bool(rows["qualified"].iloc[0])


def _month_key(rebalance_ms: int) -> str:
    day = datetime.fromtimestamp(rebalance_ms // 1000, tz=UTC)
    return f"{day.year:04d}-{day.month:02d}"


def build_universe(
    rebalance_ms: int,
    daily_bars: dict[str, pd.DataFrame],
    rules: dict[str, SymbolMeta],
    cfg: UniverseConfig,
    *,
    quality_cache: pd.DataFrame | None = None,
) -> UniverseSnapshot:
    """按 03 §4.2 第 1~7 步构建某月交易范围快照。

    只使用 open_time < R 的日线（无未来函数，03 §4.2）。

    Args:
        rebalance_ms: 再平衡时点 R（UTC 毫秒，月度 1 日 00:00）。
        daily_bars: 全市场日线，symbol -> DataFrame（至少含 open_time、close、quote_volume）。
        rules: symbol -> SymbolMeta（含已下架合约近似值，03 §4.3）。
        cfg: universe 配置。
        quality_cache: T0.17 的 (symbol, window_end_ms) 质量结论表；None 表示第 7 步全部合格。

    Returns:
        UniverseSnapshot（coin.alias 为空，由 assign_aliases 填充）。

    Raises:
        ValueError: rebalance_ms 未按天对齐。
    """
    if rebalance_ms % _MS_PER_DAY:
        raise ValueError(f"再平衡时点未按天对齐：{rebalance_ms}")
    rule = cfg.rule
    lookback_ms = rule.lookback_days * _MS_PER_DAY
    listing_ms = rule.min_listing_days * _MS_PER_DAY

    excluded_counts: dict[str, int] = {
        "candidates": 0,
        "excluded": 0,
        "listing": 0,
        "liquidity": 0,
        "tradability": 0,
        "quality": 0,
    }
    eligible: list[tuple[str, float]] = []
    details: dict[str, dict[str, Any]] = {}

    for symbol, bars in daily_bars.items():
        before = bars[bars["open_time"] < rebalance_ms]
        if before.empty:
            continue
        excluded_counts["candidates"] += 1
        meta = rules.get(symbol, SymbolMeta(symbol=symbol))
        if _is_excluded(meta, cfg):
            excluded_counts["excluded"] += 1
            continue
        first_listing_ms = int(before["open_time"].min())
        if rebalance_ms - first_listing_ms < listing_ms:
            excluded_counts["listing"] += 1
            continue
        daily_quote_median = _median_last_days(before, rebalance_ms, lookback_ms, "quote_volume")
        if pd.isna(daily_quote_median) or daily_quote_median < rule.min_daily_quote_median:
            excluded_counts["liquidity"] += 1
            continue
        price_7d = _median_last_days(before, rebalance_ms, _DAYS_7, "close")
        price_30d = _median_last_days(before, rebalance_ms, lookback_ms, "close")
        if not _tradable(meta, price_7d, price_30d, rule):
            excluded_counts["tradability"] += 1
            continue
        eligible.append((symbol, daily_quote_median))
        details[symbol] = {
            "first_listing_ms": first_listing_ms,
            "price_median_30d": price_30d,
            "tick_rel": (
                meta.tick_size / price_30d
                if meta.tick_size is not None and not pd.isna(price_30d)
                else None
            ),
            "step_notional": (
                meta.step_size * price_7d
                if meta.step_size is not None and not pd.isna(price_7d)
                else None
            ),
            "max_leverage": meta.max_leverage,
        }

    eligible.sort(key=lambda item: item[1], reverse=True)
    coins: list[SnapshotCoin] = []
    for rank, (symbol, daily_quote_median) in enumerate(eligible[: rule.top_n], start=1):
        if quality_cache is not None and not _quality_ok(quality_cache, symbol, rebalance_ms):
            excluded_counts["quality"] += 1
            continue  # 不找替补（03 §4.2 第 7 步）
        detail = details[symbol]
        coins.append(
            SnapshotCoin(
                symbol=symbol,
                alias="",
                role=role_for_symbol(symbol, cfg.roles),
                rank=rank,
                daily_quote_median=daily_quote_median,
                first_listing_ms=detail["first_listing_ms"],
                price_median_30d=detail["price_median_30d"],
                tick_rel=detail["tick_rel"],
                step_notional=detail["step_notional"],
                max_leverage=detail["max_leverage"],
            )
        )

    return UniverseSnapshot(
        month=_month_key(rebalance_ms),
        rebalance_ms=rebalance_ms,
        rule_hash=rule_hash(cfg),
        coins=coins,
        excluded_counts=excluded_counts,
    )


def write_universe_snapshot(path: Path, snapshot: UniverseSnapshot) -> None:
    """原子写入 data/universe/{YYYY-MM}.json（03 §4.2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_universe_snapshot(path: Path) -> UniverseSnapshot:
    """读取某月交易范围快照（03 §4.2）。"""
    if not path.is_file():
        raise FileNotFoundError(f"未找到快照：{path}")
    return UniverseSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
