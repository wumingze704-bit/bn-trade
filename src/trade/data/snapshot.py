"""币安 USDⓈ-M 合约交易规则快照：exchangeInfo / leverageBracket 拉取、保存与 rules.json 生成。

规格：docs/03-数据与选币.md §4.2、§4.3；docs/09-币安数据与API.md §3.1、§3.2
本模块只在东京服务器执行真实拉取；本地仅用 fixture 做单元测试。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from trade.data.exchange import (
    min_notional,
    parse_exchange_info,
    step_size,
    tick_size,
)
from trade.data.universe import SymbolMeta

FAPI_BASE = "https://fapi.binance.com"
EXCHANGE_INFO_URL = f"{FAPI_BASE}/fapi/v1/exchangeInfo"
LEVERAGE_BRACKET_URL = f"{FAPI_BASE}/fapi/v1/leverageBracket"

# 稳定币基础资产（03 §4.2 第 2 步：exclude_stable 排除 USDC、FDUSD 等稳定币对）。
_DEFAULT_STABLE_BASES = frozenset({"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "AEUR", "EUR"})


def fetch_json(client: httpx.Client, url: str) -> dict[str, Any]:
    """GET 一个公开 JSON 接口并返回解析后的对象。

    Args:
        client: httpx 客户端（测试可注入 MockTransport）。
        url: 接口地址。

    Returns:
        响应的 JSON 对象。

    Raises:
        httpx.HTTPError: 请求失败或状态码非 2xx。
        ValueError: 响应不是 JSON 对象。
    """
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"接口响应不是 JSON 对象：{url}")
    return payload


def fetch_exchange_info(client: httpx.Client) -> dict[str, Any]:
    """拉取 `GET /fapi/v1/exchangeInfo`（公开，无需签名，09 §3.1）。"""
    return fetch_json(client, EXCHANGE_INFO_URL)


def fetch_leverage_bracket(client: httpx.Client) -> dict[str, Any]:
    """拉取 `GET /fapi/v1/leverageBracket`（09 §3.2：需签名）。

    实测该接口无 API Key 时返回 401（需签名），且响应为数组而非对象。
    字段名以 developers.binance.com 最新文档为准。P1 无 Key 时跳过、`max_leverage`
    视为满足（03 §4.3、§4.2 第 5 步），见 `scripts/snapshot_rules.py`。
    """
    return fetch_json(client, LEVERAGE_BRACKET_URL)


def max_leverage_map(raw: dict[str, Any]) -> dict[str, int]:
    """从 leverageBracket 响应提取每个 symbol 的最大杠杆（各 bracket 的 initialLeverage 最大值）。

    Args:
        raw: `GET /fapi/v1/leverageBracket` 的 JSON 响应（列表）。

    Returns:
        `symbol -> max_leverage` 映射；缺 brackets 的 symbol 不在结果中。

    Raises:
        ValueError: 响应不是列表。
    """
    if not isinstance(raw, list):
        raise ValueError("leverageBracket 响应应为列表")
    result: dict[str, int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        brackets = item.get("brackets")
        if not isinstance(symbol, str) or not isinstance(brackets, list) or not brackets:
            continue
        leverages = [
            int(float(bracket["initialLeverage"]))
            for bracket in brackets
            if isinstance(bracket, dict) and bracket.get("initialLeverage") is not None
        ]
        if leverages:
            result[symbol] = max(leverages)
    return result


def _is_stable(base_asset: str, stable_bases: frozenset[str]) -> bool:
    """判断基础资产是否为稳定币（03 §4.2 第 2 步）。"""
    return base_asset.upper() in stable_bases


def build_rules(
    exchange_info_raw: dict[str, Any],
    leverage_bracket_raw: dict[str, Any],
    *,
    stable_bases: frozenset[str] = _DEFAULT_STABLE_BASES,
    excluded_symbols: frozenset[str] = frozenset(),
) -> dict[str, SymbolMeta]:
    """把 exchangeInfo 与 leverageBracket 合并为逐币交易规则与分类。

    只保留 USDⓈ-M USDT 永续（03 §4.2 第 1、2 步的候选口径）；其余合约（如 BUSD 报价、
    交割合约 CURRENT_QUARTER、TradFi）不产出规则。`excluded` 仅由 `excluded_symbols`
    显式指定，稳定币与 TradFi 的排除由 `universe.yaml` 的 `exclude_stable/exclude_tradfi`
    结合 `is_stable/underlying_type` 字段在选币时判定（03 §4.2 第 2 步）。

    Args:
        exchange_info_raw: `GET /fapi/v1/exchangeInfo` 的 JSON 响应。
        leverage_bracket_raw: `GET /fapi/v1/leverageBracket` 的 JSON 响应。
        stable_bases: 稳定币基础资产集合。
        excluded_symbols: 显式排除的 symbol 集合。

    Returns:
        `symbol -> SymbolMeta` 映射（不含 `symbol` 字段本身，写盘时由键提供）。
    """
    leverages = max_leverage_map(leverage_bracket_raw)
    symbols = parse_exchange_info(exchange_info_raw)
    rules: dict[str, SymbolMeta] = {}
    for symbol in symbols:
        if symbol.quote_asset != "USDT" or symbol.contract_type != "PERPETUAL":
            continue
        base_asset = symbol.base_asset or symbol.symbol.removesuffix("USDT")
        rules[symbol.symbol] = SymbolMeta(
            symbol=symbol.symbol,
            base_asset=base_asset,
            quote_asset="USDT",
            contract_type="PERPETUAL",
            underlying_type=symbol.underlying_type,
            is_stable=_is_stable(base_asset, stable_bases),
            excluded=symbol.symbol in excluded_symbols,
            tick_size=tick_size(symbol),
            step_size=step_size(symbol),
            min_notional=min_notional(symbol),
            max_leverage=leverages.get(symbol.symbol),
        )
    return rules


def _min_positive_diff(values: Sequence[float]) -> float | None:
    """有序去重后的最小正相邻差，用于从逐笔推断 tickSize/stepSize（03 §4.3）。"""
    unique = sorted({round(float(value), 12) for value in values})
    diffs = [round(right - left, 12) for left, right in pairwise(unique) if right > left]
    return min(diffs) if diffs else None


def infer_delisted_rules(
    symbol: str,
    prices: Sequence[float],
    quantities: Sequence[float],
    *,
    min_notional_value: float = 5.0,
) -> SymbolMeta:
    """按 03 §4.3 从已下架合约的 aggTrades 推断交易规则。

    已下架合约不在当前 exchangeInfo 中，无法得知历史 tickSize/stepSize 与杠杆，
    只能：tickSize 取成交价的最小正价差、stepSize 取成交量的最小正增量、
    minNotional 固定取 5 USDT、max_leverage 视为满足（置 None）。

    Args:
        symbol: 合约 symbol。
        prices: 该月 aggTrades 成交价序列。
        quantities: 该月 aggTrades 成交量序列。
        min_notional_value: 最小下单额（03 §4.3 固定 5 USDT）。

    Returns:
        推断出的 SymbolMeta（base_asset 由 symbol 去 USDT 后缀推导）。
    """
    return SymbolMeta(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        contract_type="PERPETUAL",
        tick_size=_min_positive_diff(prices),
        step_size=_min_positive_diff(quantities),
        min_notional=min_notional_value,
        max_leverage=None,
    )


def rules_to_payload(rules: dict[str, SymbolMeta]) -> dict[str, dict[str, Any]]:
    """把 SymbolMeta 映射序列化为可写 JSON 的字典（值不含 symbol，键为 symbol）。"""
    payload: dict[str, dict[str, Any]] = {}
    for symbol, meta in rules.items():
        fields = asdict(meta)
        fields.pop("symbol", None)
        payload[symbol] = fields
    return payload


def write_rules_json(rules: dict[str, SymbolMeta], path: Path) -> None:
    """原子写入 rules.json（键为 symbol，03 §4.6）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(rules_to_payload(rules), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def save_timestamped_snapshot(
    raw: dict[str, Any],
    out_dir: Path,
    kind: str,
    *,
    timestamp: str | None = None,
) -> Path:
    """保存带时间戳的原始快照 JSON（08 §2 P1：快照文件带时间戳）。

    Args:
        raw: 原始接口响应。
        out_dir: 输出目录。
        kind: 快照类型名（如 `exchangeInfo` / `leverageBracket`）。
        timestamp: 时间戳字符串；缺省取当前 UTC 时间（`%Y%m%dT%H%M%SZ`）。

    Returns:
        写入的文件路径。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{kind}-{stamp}.json"
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path
