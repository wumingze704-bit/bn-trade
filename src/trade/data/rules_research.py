"""研究期交易规则集合：来源①当前 exchangeInfo、②历史快照、③合成。

规格：docs/13-研究框架.md §1、docs/03-数据与选币.md §4.3

防幸存者偏差：已下架合约必须和在架合约一样参与选币，不得因为当前 exchangeInfo 里没有
交易规则而排除（下架币多数是跌下来的，排除它们会让多头偏好、空头偏差）。规则按固定顺序取得：

1. `exchange`：当前 exchangeInfo 快照（含 status 不是 TRADING 的合约）；
2. `snapshot`：`artifacts/exchange_info/` 下更早的历史快照；
3. `synthetic`：都没有时按 1h K 线合成（tickSize / stepSize / 最小下单额 5 USDT）。

TradFi 合约（contractType 不是 PERPETUAL，或 underlyingType 属于股票 / 商品 / 指数等）
保留真实字段，由选币的 `exclude_tradfi` 排除，不进入合成分支。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from trade.data.snapshot import _DEFAULT_STABLE_BASES
from trade.data.universe import _TRADFI_UNDERLYING, SymbolMeta

SOURCE_EXCHANGE = "exchange"
SOURCE_SNAPSHOT = "snapshot"
SOURCE_SYNTHETIC = "synthetic"

# 合成规则的最小下单额（docs/13 §1；1 号第五部分 A 第 2 条固定 5 USDT）。
SYNTHETIC_MIN_NOTIONAL = 5.0
# 合成规则时 1 个 step 的名义上限（docs/13 §1：≤ 0.1 USDT）。
SYNTHETIC_STEP_NOTIONAL_MAX = 0.1
# 合成规则时每个币取样的月份数（第一、中间、最后一月，控制 IO）。
_SYNTHETIC_SAMPLE_MONTHS = 3


@dataclass(frozen=True)
class RuleCollection:
    """研究期全部币的交易规则与来源。"""

    rules: dict[str, SymbolMeta]
    sources: dict[str, str]


def _is_stable(base_asset: str, stable_bases: frozenset[str]) -> bool:
    """判断基础资产是否为稳定币（docs/03 §4.2 第 2 步）。"""
    return base_asset.upper() in stable_bases


def _filter_value(item: Mapping[str, Any], filter_type: str, key: str) -> float | None:
    """从 exchangeInfo 的过滤器数组提取数值字段（缺失返回 None）。"""
    filters = item.get("filters")
    if not isinstance(filters, list):
        return None
    for entry in filters:
        if isinstance(entry, dict) and entry.get("filterType") == filter_type:
            value = entry.get(key)
            return float(value) if value is not None else None
    return None


def rules_from_exchange_info(
    raw: Mapping[str, Any], *, stable_bases: frozenset[str] = _DEFAULT_STABLE_BASES
) -> dict[str, SymbolMeta]:
    """把 exchangeInfo 快照转换为全部 USDT 合约的规则与分类（docs/13 §1）。

    与 `trade.data.snapshot.build_rules` 的区别：不区分 contractType，保留 `TRADIFI_PERPETUAL`
    等非永续合约的字段，让选币按 `exclude_tradfi` 排除；也保留 status 不是 TRADING 的合约。

    Args:
        raw: exchangeInfo 的 JSON 响应。
        stable_bases: 稳定币基础资产集合。

    Returns:
        `symbol -> SymbolMeta`。

    Raises:
        KeyError: 响应缺少 `symbols` 数组。
    """
    symbols = raw.get("symbols")
    if not isinstance(symbols, list):
        raise KeyError("exchangeInfo 响应缺少 symbols 数组")
    rules: dict[str, SymbolMeta] = {}
    for item in symbols:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or item.get("quoteAsset") != "USDT":
            continue
        base_asset = str(item.get("baseAsset") or symbol.removesuffix("USDT"))
        rules[symbol] = SymbolMeta(
            symbol=symbol,
            base_asset=base_asset,
            quote_asset="USDT",
            contract_type=str(item.get("contractType") or ""),
            underlying_type=str(item.get("underlyingType") or ""),
            is_stable=_is_stable(base_asset, stable_bases),
            excluded=False,
            tick_size=_filter_value(item, "PRICE_FILTER", "tickSize"),
            step_size=_filter_value(item, "LOT_SIZE", "stepSize"),
            min_notional=_filter_value(item, "MIN_NOTIONAL", "notional"),
            max_leverage=None,
        )
    return rules


def _power_of_ten_floor(value: float) -> float:
    """把正数向下取到 10 的整数次幂（docs/13 §1：tickSize 合成规则）。"""
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"无法把非正数取到 10 的整数次幂：{value}")
    return 10.0 ** math.floor(math.log10(value))


def _min_positive_diff(values: np.ndarray) -> float:
    """有序去重后的最小正价差（docs/13 §1：tickSize 合成规则）。"""
    unique = np.unique(np.round(values.astype("float64"), 12))
    diffs = np.diff(unique)
    positive = diffs[diffs > 0.0]
    if positive.size == 0:
        raise ValueError("1h 价格没有正价差，无法合成 tickSize（数据不足或价格全相同）")
    return float(positive.min())


def synthetic_rule(
    symbol: str, prices: np.ndarray, *, min_notional_value: float = SYNTHETIC_MIN_NOTIONAL
) -> SymbolMeta:
    """按 1h 价格合成已下架合约的交易规则（docs/13 §1）。

    - tickSize：取价格中出现的最小正价差，向下取到 10 的整数次幂；
    - stepSize：取 10 的整数次幂中使 1 个 step 的名义 ≤ 0.1 USDT 的最大值；
    - 最小下单额：`min_notional_value`（默认 5 USDT）。

    Args:
        symbol: 合约 symbol。
        prices: 该币 1h 价格样本（正数）。
        min_notional_value: 最小下单额，USDT。

    Returns:
        合成出的 SymbolMeta（contract_type=PERPETUAL，max_leverage=None）。

    Raises:
        ValueError: 价格样本为空或没有正价差。
    """
    finite = prices[np.isfinite(prices) & (prices > 0.0)]
    if finite.size == 0:
        raise ValueError(f"{symbol} 没有可用的 1h 正价格样本，无法合成交易规则")
    tick = _power_of_ten_floor(_min_positive_diff(finite))
    price_ref = float(np.median(finite))
    step = 10.0 ** math.floor(math.log10(SYNTHETIC_STEP_NOTIONAL_MAX / price_ref))
    return SymbolMeta(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        contract_type="PERPETUAL",
        underlying_type="",
        is_stable=False,
        excluded=False,
        tick_size=tick,
        step_size=step,
        min_notional=min_notional_value,
        max_leverage=None,
    )


def sample_hourly_prices(
    klines_1h: Path, symbol: str, *, months: int = _SYNTHETIC_SAMPLE_MONTHS
) -> np.ndarray:
    """读取某币若干月份的 1h OHLC 价格样本（第一、中间、最后一月）。

    Args:
        klines_1h: `data/klines_1h` 根目录。
        symbol: 合约 symbol。
        months: 取样月份数。

    Returns:
        一维价格数组。

    Raises:
        ValueError: 该币没有任何 1h 月度文件。
    """
    files = sorted((klines_1h / symbol).glob("*.parquet"))
    if not files:
        raise ValueError(f"{symbol} 没有 1h 月度数据，无法合成交易规则：{klines_1h / symbol}")
    picks = sorted({files[0], files[len(files) // 2], files[-1]})[:months]
    frames = [
        pq.read_table(path, columns=["open", "high", "low", "close"]).to_pandas() for path in picks
    ]
    return np.concatenate([frame.to_numpy(dtype="float64").ravel() for frame in frames])


def collect_rules(
    *,
    exchange_infos: Sequence[Mapping[str, Any]],
    symbols: Sequence[str],
    klines_1h: Path,
    stable_bases: frozenset[str] = _DEFAULT_STABLE_BASES,
    min_notional_value: float = SYNTHETIC_MIN_NOTIONAL,
) -> RuleCollection:
    """按固定顺序为全部币取得交易规则（docs/13 §1、docs/03 §4.3）。

    Args:
        exchange_infos: exchangeInfo 快照，最新在前（第 1 个为当前快照）。
        symbols: 需要规则的 symbol（通常为 `data/klines_1d` 下的全部目录）。
        klines_1h: `data/klines_1h` 根目录（合成规则用）。
        stable_bases: 稳定币基础资产集合。
        min_notional_value: 合成规则的最小下单额，USDT。

    Returns:
        RuleCollection（rules 含 `symbols` 的全部币；sources 记录每个币的来源）。

    Raises:
        ValueError: exchangeInfo 快照为空，或某个币无法合成规则。
    """
    if not exchange_infos:
        raise ValueError("没有 exchangeInfo 快照，无法取得交易规则（docs/13 §1）")
    rules: dict[str, SymbolMeta] = {}
    sources: dict[str, str] = {}
    for index, raw in enumerate(exchange_infos):
        source = SOURCE_EXCHANGE if index == 0 else SOURCE_SNAPSHOT
        for symbol, meta in rules_from_exchange_info(raw, stable_bases=stable_bases).items():
            if symbol in rules:
                continue
            rules[symbol] = meta
            sources[symbol] = source
    for symbol in symbols:
        if symbol in rules:
            continue
        prices = sample_hourly_prices(klines_1h, symbol)
        rules[symbol] = synthetic_rule(symbol, prices, min_notional_value=min_notional_value)
        sources[symbol] = SOURCE_SYNTHETIC
    missing = sorted(symbol for symbol in symbols if symbol not in rules)
    if missing:
        raise ValueError(f"以下币仍缺交易规则：{missing[:5]}…（红线 12）")
    return RuleCollection(rules=rules, sources=sources)


def tradfi_symbols(rules: Mapping[str, SymbolMeta]) -> list[str]:
    """列出非加密资产合约（TradFi）：contractType 非 PERPETUAL 或 underlyingType 属于股票等。

    Args:
        rules: symbol -> SymbolMeta。

    Returns:
        排序后的 TradFi symbol 列表（写入 `data/universe_hist/summary.json`）。
    """
    return sorted(
        symbol
        for symbol, meta in rules.items()
        if (meta.contract_type and meta.contract_type != "PERPETUAL")
        or meta.underlying_type.upper() in _TRADFI_UNDERLYING
    )
