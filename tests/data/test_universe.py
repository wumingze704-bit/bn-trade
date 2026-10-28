"""币种名单校验与盲测币抽样测试。

规格：docs/03-数据与选币.md §4
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trade.data.exchange import SymbolInfo
from trade.data.universe import (
    CoinEntry,
    CoinsConfig,
    SymbolMetrics,
    UniverseConfig,
    draw_blind,
    load_coins,
    load_universe,
    validate_coins,
)

CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_load_coins_fixed_list() -> None:
    coins = load_coins(CONFIGS / "coins.yaml")
    assert len(coins.train) == 13
    assert len(coins.holdout) == 3
    assert coins.blind == []
    assert {entry.alias for entry in coins.train} == {f"C{i:02d}" for i in range(1, 14)}


def test_load_universe_thresholds() -> None:
    universe = load_universe(CONFIGS / "universe.yaml")
    assert universe.blind_seed == 2026
    assert universe.blind_count == 5
    assert universe.filters.step_notional_max == 0.5


def test_draw_blind_is_reproducible() -> None:
    candidates = ["A", "B", "C", "D", "E", "F", "G", "H"]
    first = draw_blind(candidates, seed=2026, count=5)
    second = draw_blind(candidates, seed=2026, count=5)
    assert first == second
    assert len(first) == 5


def test_draw_blind_ignores_input_order() -> None:
    # 内部先排序再抽样，因此输入顺序不影响结果
    a = draw_blind(["H", "G", "F", "E", "D", "C", "B", "A"], seed=2026, count=5)
    b = draw_blind(["A", "B", "C", "D", "E", "F", "G", "H"], seed=2026, count=5)
    assert a == b


def test_draw_blind_rejects_insufficient_candidates() -> None:
    with pytest.raises(ValueError):
        draw_blind(["A", "B"], seed=2026, count=5)


def _symbol(name: str, onboard: int = 1597021200000) -> SymbolInfo:
    return SymbolInfo.model_validate(
        {
            "symbol": name,
            "contractType": "PERPETUAL",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "onboardDate": onboard,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.01"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }
    )


def _metrics() -> SymbolMetrics:
    return SymbolMetrics(
        price_median_7d=10.0,
        max_leverage=20,
        daily_quote_median=100_000_000,
        monthly_quote_median_min=20_000_000,
        tick_rel=0.0001,
        quality_ok=True,
    )


def _universe() -> UniverseConfig:
    return load_universe(CONFIGS / "universe.yaml")


def test_validate_coins_accepts_valid_train_coin() -> None:
    coins = load_coins(CONFIGS / "coins.yaml")
    universe = _universe()
    entries = [*coins.train, *coins.holdout]
    symbols = {entry.symbol: _symbol(entry.symbol) for entry in entries}
    metrics = {entry.symbol: _metrics() for entry in entries}
    records = validate_coins(coins, universe, symbols, metrics)
    assert all(record.passed for record in records)


def test_validate_coins_reports_missing_exchange_info() -> None:
    coins = load_coins(CONFIGS / "coins.yaml")
    universe = _universe()
    records = validate_coins(coins, universe, {}, {})
    assert records[0].blocking_failed == ["missing_exchange_info"]
    assert not records[0].passed
