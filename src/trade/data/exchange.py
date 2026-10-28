"""币安 USDⓈ-M 合约 exchangeInfo 解析与合约可交易性校验。

规格：docs/03-数据与选币.md §4.2；docs/09-币安数据与API.md §3.1
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SymbolInfo(BaseModel):
    """exchangeInfo 中单个合约的可交易元数据。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    contract_type: str = Field(alias="contractType")
    quote_asset: str = Field(alias="quoteAsset")
    status: str
    onboard_date: int | None = Field(default=None, alias="onboardDate")
    filters: list[dict[str, Any]] = Field(default_factory=list)


class CheckLevel(str, Enum):
    """校验结果级别：阻断或警告。"""

    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True)
class CheckResult:
    """单条校验结果。"""

    name: str
    level: CheckLevel
    passed: bool
    detail: str


def parse_exchange_info(raw: dict[str, Any]) -> list[SymbolInfo]:
    """把 exchangeInfo 原始响应解析为合约元数据列表。

    Args:
        raw: `GET /fapi/v1/exchangeInfo` 的 JSON 响应。

    Returns:
        按出现顺序返回的合约元数据列表。

    Raises:
        KeyError: 响应缺少 `symbols` 数组。
    """
    symbols = raw.get("symbols")
    if not isinstance(symbols, list):
        raise KeyError("exchangeInfo 响应缺少 symbols 数组")
    return [SymbolInfo.model_validate(item) for item in symbols if isinstance(item, dict)]


def _filter_value(symbol: SymbolInfo, filter_type: str, key: str) -> float | None:
    """从指定过滤器提取数值字段，缺失时返回 None。"""
    for item in symbol.filters:
        if item.get("filterType") == filter_type:
            value = item.get(key)
            return float(value) if value is not None else None
    return None


def tick_size(symbol: SymbolInfo) -> float | None:
    """从 PRICE_FILTER 提取 tickSize。"""
    return _filter_value(symbol, "PRICE_FILTER", "tickSize")


def step_size(symbol: SymbolInfo) -> float | None:
    """从 LOT_SIZE 提取 stepSize。"""
    return _filter_value(symbol, "LOT_SIZE", "stepSize")


def min_notional(symbol: SymbolInfo) -> float | None:
    """从 MIN_NOTIONAL 提取最小下单名义价值。"""
    return _filter_value(symbol, "MIN_NOTIONAL", "notional")


def check_contract(symbol: SymbolInfo, contract_type: str, quote_asset: str, status: str) -> CheckResult:
    """校验合约类型、报价资产与交易状态（阻断级）。"""
    if symbol.contract_type != contract_type:
        return CheckResult("contract_type", CheckLevel.BLOCK, False, f"{symbol.contract_type} != {contract_type}")
    if symbol.quote_asset != quote_asset:
        return CheckResult("quote_asset", CheckLevel.BLOCK, False, f"{symbol.quote_asset} != {quote_asset}")
    if symbol.status != status:
        return CheckResult("status", CheckLevel.BLOCK, False, f"{symbol.status} != {status}")
    return CheckResult("contract", CheckLevel.BLOCK, True, "合约类型/报价资产/交易状态合格")


def check_not_excluded(symbol: SymbolInfo, excluded_symbols: set[str]) -> CheckResult:
    """校验合约不在 TradFi 等排除名单（阻断级）。"""
    if symbol.symbol in excluded_symbols:
        return CheckResult("not_excluded", CheckLevel.BLOCK, False, f"{symbol.symbol} 在排除名单（TradFi 等）")
    return CheckResult("not_excluded", CheckLevel.BLOCK, True, "不在排除名单")


def check_step_notional(symbol: SymbolInfo, price_median_7d: float, max_value: float) -> CheckResult:
    """校验数量步长价值 stepSize × 7 天价格中位数 ≤ 上限（阻断级）。"""
    step = step_size(symbol)
    if step is None:
        return CheckResult("step_notional", CheckLevel.BLOCK, False, "缺少 LOT_SIZE stepSize")
    step_notional = step * price_median_7d
    if step_notional > max_value:
        return CheckResult(
            "step_notional",
            CheckLevel.BLOCK,
            False,
            f"stepSize × 价格中位数 = {step_notional:.4f} > {max_value} USDT",
        )
    return CheckResult("step_notional", CheckLevel.BLOCK, True, f"{step_notional:.4f} ≤ {max_value} USDT")


def check_min_notional(symbol: SymbolInfo, max_value: float) -> CheckResult:
    """校验最小下单额 ≤ 上限（阻断级）。"""
    notional = min_notional(symbol)
    if notional is None:
        return CheckResult("min_notional", CheckLevel.BLOCK, False, "缺少 MIN_NOTIONAL notional")
    if notional > max_value:
        return CheckResult("min_notional", CheckLevel.BLOCK, False, f"{notional} > {max_value} USDT")
    return CheckResult("min_notional", CheckLevel.BLOCK, True, f"{notional} ≤ {max_value} USDT")


def check_leverage(max_leverage: int, min_leverage: int) -> CheckResult:
    """校验杠杆分层允许达到目标杠杆（阻断级）。"""
    if max_leverage < min_leverage:
        return CheckResult("max_leverage", CheckLevel.BLOCK, False, f"最大杠杆 {max_leverage}x < {min_leverage}x")
    return CheckResult("max_leverage", CheckLevel.BLOCK, True, f"最大杠杆 {max_leverage}x ≥ {min_leverage}x")


def check_history(
    symbol: SymbolInfo,
    *,
    role: str,
    warmup_days: int,
    train_margin_months: int,
    holdout_min_months: int,
    window_start_ms: int,
    window_end_ms: int,
) -> CheckResult:
    """校验历史长度是否满足训练/留出角色的要求（阻断级）。

    Args:
        symbol: 合约元数据。
        role: `train` / `holdout` / `blind`。
        warmup_days: 上线后前 N 天数据不用。
        train_margin_months: 训练币要求 `onboardDate + warmup ≤ T_start + N 个月`。
        holdout_min_months: 留出币要求测试段 + 封存集内至少 N 个月有效数据。
        window_start_ms: 数据窗口起点（毫秒）。
        window_end_ms: 数据窗口终点（毫秒）。

    Returns:
        校验结果。
    """
    onboard = symbol.onboard_date
    if onboard is None:
        return CheckResult("history", CheckLevel.BLOCK, False, "缺少 onboardDate")
    warmup_ms = warmup_days * 86_400_000
    month_ms = 30 * 86_400_000
    if role == "train":
        threshold = window_start_ms + train_margin_months * month_ms
        ok = onboard + warmup_ms <= threshold
        detail = f"onboardDate + {warmup_days}d ≤ T_start + {train_margin_months}个月"
    elif role == "holdout":
        ok = window_end_ms - onboard >= holdout_min_months * month_ms
        detail = f"测试段 + 封存集内至少 {holdout_min_months} 个月有效数据"
    else:  # blind：onboardDate + 30 天 ≤ 第一个测试月 M13 起点
        first_test_month_start = window_start_ms + 12 * month_ms
        ok = onboard + warmup_ms <= first_test_month_start
        detail = f"onboardDate + {warmup_days}d ≤ 第一个测试月 M13 起点"
    return CheckResult("history", CheckLevel.BLOCK, ok, detail)


def check_liquidity(
    daily_quote_median: float,
    monthly_quote_median_min: float,
    tick_rel: float,
    *,
    daily_min: float,
    monthly_min: float,
    tick_rel_max: float,
) -> list[CheckResult]:
    """校验流动性类门槛（警告级，不阻断）。"""
    return [
        CheckResult(
            "liq",
            CheckLevel.WARN,
            daily_quote_median >= daily_min,
            f"日成交额中位数 {daily_quote_median:.0f} vs {daily_min:.0f}",
        ),
        CheckResult(
            "liq_min",
            CheckLevel.WARN,
            monthly_quote_median_min >= monthly_min,
            f"最差月日成交额中位数 {monthly_quote_median_min:.0f} vs {monthly_min:.0f}",
        ),
        CheckResult(
            "tick_rel",
            CheckLevel.WARN,
            tick_rel <= tick_rel_max,
            f"tick_rel {tick_rel:.6f} vs {tick_rel_max:.6f}",
        ),
    ]

