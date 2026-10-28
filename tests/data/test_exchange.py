"""exchangeInfo 解析与可交易性校验测试。

规格：docs/03-数据与选币.md §4.2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade.data.exchange import (
    CheckLevel,
    SymbolInfo,
    check_contract,
    check_history,
    check_leverage,
    check_liquidity,
    check_min_notional,
    check_not_excluded,
    check_step_notional,
    min_notional,
    parse_exchange_info,
    step_size,
    tick_size,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _symbol(**kwargs: object) -> SymbolInfo:
    defaults: dict[str, object] = {
        "symbol": "SOLUSDT",
        "contractType": "PERPETUAL",
        "quoteAsset": "USDT",
        "status": "TRADING",
        "onboardDate": 1597021200000,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.1"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }
    defaults.update(kwargs)
    return SymbolInfo.model_validate(defaults)


def test_parse_exchange_info_extracts_symbols() -> None:
    raw = json.loads((FIXTURES / "exchange_info.json").read_text(encoding="utf-8"))
    symbols = parse_exchange_info(raw)
    assert len(symbols) == 4
    by_name = {symbol.symbol: symbol for symbol in symbols}
    assert by_name["SOLUSDT"].contract_type == "PERPETUAL"
    assert tick_size(by_name["SOLUSDT"]) == 0.01
    assert step_size(by_name["SOLUSDT"]) == 0.1
    assert min_notional(by_name["SOLUSDT"]) == 5.0


def test_parse_exchange_info_missing_symbols_raises() -> None:
    with pytest.raises(KeyError):
        parse_exchange_info({})


def test_check_contract_rejects_non_perpetual() -> None:
    result = check_contract(_symbol(contractType="CURRENT_QUARTER"), "PERPETUAL", "USDT", "TRADING")
    assert result.level is CheckLevel.BLOCK
    assert not result.passed


def test_check_contract_rejects_non_usdt() -> None:
    result = check_contract(_symbol(quoteAsset="BUSD"), "PERPETUAL", "USDT", "TRADING")
    assert not result.passed


def test_check_contract_accepts_valid() -> None:
    assert check_contract(_symbol(), "PERPETUAL", "USDT", "TRADING").passed


def test_check_not_excluded() -> None:
    assert not check_not_excluded(_symbol(symbol="CLUSDT"), {"CLUSDT"}).passed
    assert check_not_excluded(_symbol(), {"CLUSDT"}).passed


def test_check_step_notional() -> None:
    # stepSize 0.1 × 价格中位数 100 = 10 > 0.5 → 阻断
    assert not check_step_notional(_symbol(), 100.0, 0.5).passed
    tiny_step = [{"filterType": "LOT_SIZE", "stepSize": "0.001"}]
    assert check_step_notional(_symbol(filters=tiny_step), 100.0, 0.5).passed


def test_check_min_notional() -> None:
    assert check_min_notional(_symbol(), 5.0).passed
    big_notional = [{"filterType": "MIN_NOTIONAL", "notional": "20"}]
    assert not check_min_notional(_symbol(filters=big_notional), 5.0).passed


def test_check_leverage() -> None:
    assert check_leverage(20, 5).passed
    assert not check_leverage(3, 5).passed


def test_check_history_train_passes_for_old_coin() -> None:
    result = check_history(
        _symbol(onboardDate=1597021200000),
        role="train",
        warmup_days=30,
        train_margin_months=6,
        holdout_min_months=3,
        window_start_ms=1727740800000,
        window_end_ms=1790035200000,
    )
    assert result.passed


def test_check_history_train_rejects_too_new_coin() -> None:
    result = check_history(
        _symbol(onboardDate=1767225600000),  # 2026-01-01，窗口内不足 18 个月
        role="train",
        warmup_days=30,
        train_margin_months=6,
        holdout_min_months=3,
        window_start_ms=1727740800000,
        window_end_ms=1790035200000,
    )
    assert not result.passed


def test_check_liquidity_warns_only() -> None:
    results = check_liquidity(
        10_000_000,
        20_000_000,
        0.0003,
        daily_min=50_000_000,
        monthly_min=10_000_000,
        tick_rel_max=0.0002,
    )
    assert all(result.level is CheckLevel.WARN for result in results)
    assert not results[0].passed  # liq 不达标
    assert results[1].passed  # liq_min 达标
    assert not results[2].passed  # tick_rel 不达标
