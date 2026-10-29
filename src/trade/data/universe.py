"""币种名单校验与盲测币抽样。

规格：docs/03-数据与选币.md §4；docs/08-开发计划与验收.md §2 P1
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trade.common.config import ConfigError, load_config
from trade.data.exchange import (
    CheckLevel,
    CheckResult,
    SymbolInfo,
    check_contract,
    check_history,
    check_leverage,
    check_liquidity,
    check_min_notional,
    check_not_excluded,
    check_step_notional,
)


class WindowConfig(BaseModel):
    """数据窗口起止（日期字符串，UTC）。"""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str


class CoinEntry(BaseModel):
    """单个币种条目。"""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    alias: str
    sector: str
    source: str = "user"


class ExcludedEntry(BaseModel):
    """排除币种条目。"""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    reason: str


class ValidationRecord(BaseModel):
    """单个币的校验记录。"""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    role: str
    passed: bool
    blocking_failed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationSection(BaseModel):
    """coins.yaml 的 validation 段。"""

    model_config = ConfigDict(extra="forbid")

    generated_at: str | None = None
    results: list[ValidationRecord] = Field(default_factory=list)


class CoinsConfig(BaseModel):
    """coins.yaml 的 pydantic 模型。"""

    model_config = ConfigDict(extra="forbid")

    reviewed: bool = False
    window: WindowConfig
    btc_context: str
    train: list[CoinEntry]
    holdout: list[CoinEntry]
    blind: list[CoinEntry] = Field(default_factory=list)
    excluded: list[ExcludedEntry] = Field(default_factory=list)
    validation: ValidationSection = Field(default_factory=ValidationSection)


class TradableConfig(BaseModel):
    """可交易性硬性条件。"""

    model_config = ConfigDict(extra="forbid")

    contract_type: str
    quote_asset: str
    status: str
    excluded_symbols: list[str] = Field(default_factory=list)


class HistoryConfig(BaseModel):
    """历史长度门槛。"""

    model_config = ConfigDict(extra="forbid")

    onboard_warmup_days: int
    train_onboard_margin_months: int
    holdout_min_months: int


class FiltersConfig(BaseModel):
    """过滤器门槛。"""

    model_config = ConfigDict(extra="forbid")

    step_notional_max: float
    min_notional_max: float
    min_leverage: int


class LiquidityConfig(BaseModel):
    """流动性门槛（警告级）。"""

    model_config = ConfigDict(extra="forbid")

    daily_quote_median_min: float
    monthly_quote_median_min: float
    tick_rel_max: float


class QualityConfig(BaseModel):
    """数据质量门槛（阻断级）。"""

    model_config = ConfigDict(extra="forbid")

    missing_ratio_max: float
    consistency_error_max: float


class UniverseConfig(BaseModel):
    """universe.yaml 的 pydantic 模型。"""

    model_config = ConfigDict(extra="forbid")

    blind_seed: int
    blind_count: int = Field(ge=1)
    tradable: TradableConfig
    history: HistoryConfig
    filters: FiltersConfig
    liquidity: LiquidityConfig
    quality: QualityConfig


@dataclass(frozen=True)
class SymbolMetrics:
    """一个币的可交易性相关度量（来自下载数据与质量报告）。"""

    price_median_7d: float
    max_leverage: int
    daily_quote_median: float
    monthly_quote_median_min: float
    tick_rel: float
    quality_ok: bool = True


def load_coins(path: Path) -> CoinsConfig:
    """加载并校验 coins.yaml。"""
    try:
        return load_config(CoinsConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载币种名单 {path}: {error}") from error


def load_universe(path: Path) -> UniverseConfig:
    """加载并校验 universe.yaml。"""
    try:
        return load_config(UniverseConfig, path)
    except (ConfigError, ValidationError) as error:
        raise ConfigError(f"无法加载 universe 配置 {path}: {error}") from error


def parse_date_ms(value: str) -> int:
    """把 `YYYY-MM-DD` 解析为 UTC 毫秒时间戳。"""
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def validate_symbol(
    symbol: SymbolInfo,
    role: str,
    universe: UniverseConfig,
    metrics: SymbolMetrics | None,
    window_start_ms: int,
    window_end_ms: int,
) -> list[CheckResult]:
    """对单个合约执行 03 §4.2 的全部校验。

    Args:
        symbol: 合约元数据。
        role: `train` / `holdout` / `blind`。
        universe: universe 配置门槛。
        metrics: 可交易性度量；缺失时只做不依赖度量的检查。
        window_start_ms: 数据窗口起点（毫秒）。
        window_end_ms: 数据窗口终点（毫秒）。

    Returns:
        校验结果列表（阻断与警告混合）。
    """
    results: list[CheckResult] = [
        check_contract(
            symbol,
            universe.tradable.contract_type,
            universe.tradable.quote_asset,
            universe.tradable.status,
        ),
        check_not_excluded(symbol, set(universe.tradable.excluded_symbols)),
        check_history(
            symbol,
            role=role,
            warmup_days=universe.history.onboard_warmup_days,
            train_margin_months=universe.history.train_onboard_margin_months,
            holdout_min_months=universe.history.holdout_min_months,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        ),
    ]
    if metrics is not None:
        results.append(
            check_step_notional(symbol, metrics.price_median_7d, universe.filters.step_notional_max)
        )
        results.append(check_min_notional(symbol, universe.filters.min_notional_max))
        results.append(check_leverage(metrics.max_leverage, universe.filters.min_leverage))
        results.append(
            CheckResult("quality", CheckLevel.BLOCK, metrics.quality_ok, "数据质量（03 §2.4）")
        )
        results.extend(
            check_liquidity(
                metrics.daily_quote_median,
                metrics.monthly_quote_median_min,
                metrics.tick_rel,
                daily_min=universe.liquidity.daily_quote_median_min,
                monthly_min=universe.liquidity.monthly_quote_median_min,
                tick_rel_max=universe.liquidity.tick_rel_max,
            )
        )
    return results


def blocking_failed(results: list[CheckResult]) -> list[str]:
    """返回未通过的阻断项名称。"""
    return [
        result.name for result in results if result.level is CheckLevel.BLOCK and not result.passed
    ]


def warning_names(results: list[CheckResult]) -> list[str]:
    """返回未通过的警告项名称。"""
    return [
        result.name for result in results if result.level is CheckLevel.WARN and not result.passed
    ]


def validate_coins(
    coins: CoinsConfig,
    universe: UniverseConfig,
    symbols_by_name: dict[str, SymbolInfo],
    metrics_by_name: dict[str, SymbolMetrics],
) -> list[ValidationRecord]:
    """对名单中的训练币与留出币执行校验，返回逐币记录。

    Args:
        coins: 币种名单。
        universe: universe 配置。
        symbols_by_name: exchangeInfo 中按 symbol 索引的合约元数据。
        metrics_by_name: 按 symbol 索引的可交易性度量（缺失的币只做基础检查）。

    Returns:
        训练币 + 留出币的校验记录列表。
    """
    window_start_ms = parse_date_ms(coins.window.start)
    window_end_ms = parse_date_ms(coins.window.end)
    records: list[ValidationRecord] = []
    for role, entries in (("train", coins.train), ("holdout", coins.holdout)):
        for entry in entries:
            symbol_info = symbols_by_name.get(entry.symbol)
            if symbol_info is None:
                records.append(
                    ValidationRecord(
                        symbol=entry.symbol,
                        role=role,
                        passed=False,
                        blocking_failed=["missing_exchange_info"],
                    )
                )
                continue
            results = validate_symbol(
                symbol_info,
                role,
                universe,
                metrics_by_name.get(entry.symbol),
                window_start_ms,
                window_end_ms,
            )
            records.append(
                ValidationRecord(
                    symbol=entry.symbol,
                    role=role,
                    passed=not blocking_failed(results),
                    blocking_failed=blocking_failed(results),
                    warnings=warning_names(results),
                )
            )
    return records


def draw_blind(candidates: list[str], seed: int, count: int) -> list[str]:
    """按固定种子从候选币中均匀抽取盲测币（可复现）。

    Args:
        candidates: 合格候选币 symbol 列表。
        seed: 固定随机种子（universe.yaml: blind_seed）。
        count: 抽取数量（universe.yaml: blind_count）。

    Returns:
        抽取出的 symbol 列表（按候选排序后的抽样结果）。

    Raises:
        ValueError: 候选数量不足。
    """
    ordered = sorted(set(candidates))
    if len(ordered) < count:
        raise ValueError(f"候选币不足：{len(ordered)} < {count}")
    rng = np.random.default_rng(seed)
    return [ordered[int(i)] for i in rng.choice(len(ordered), size=count, replace=False)]


def eligible_blind_candidates(
    coins: CoinsConfig,
    universe: UniverseConfig,
    symbols: list[SymbolInfo],
    metrics_by_name: dict[str, SymbolMetrics],
) -> list[str]:
    """筛选盲测币候选：不在名单内、通过全部阻断与警告门槛的合约。"""
    excluded = {entry.symbol for entry in coins.excluded}
    named = {entry.symbol for entry in (*coins.train, *coins.holdout, *coins.blind)}
    window_start_ms = parse_date_ms(coins.window.start)
    window_end_ms = parse_date_ms(coins.window.end)
    candidates: list[str] = []
    for symbol in symbols:
        if symbol.symbol in named or symbol.symbol in excluded:
            continue
        metrics = metrics_by_name.get(symbol.symbol)
        if metrics is None:
            continue
        results = validate_symbol(
            symbol, "blind", universe, metrics, window_start_ms, window_end_ms
        )
        if not blocking_failed(results) and not warning_names(results):
            candidates.append(symbol.symbol)
    return sorted(candidates)


def dump_coins(path: Path, coins: CoinsConfig) -> None:
    """把 coins.yaml 写回磁盘（用于 --draw-blind 后保存盲测结果）。"""
    import yaml

    data: dict[str, Any] = {
        "reviewed": coins.reviewed,
        "window": {"start": coins.window.start, "end": coins.window.end},
        "btc_context": coins.btc_context,
        "train": [entry.model_dump() for entry in coins.train],
        "holdout": [entry.model_dump() for entry in coins.holdout],
        "blind": [entry.model_dump() for entry in coins.blind],
        "excluded": [entry.model_dump() for entry in coins.excluded],
        "validation": coins.validation.model_dump(),
    }
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
