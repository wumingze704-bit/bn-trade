"""exchangeInfo / leverageBracket 快照与 rules.json 生成测试。

规格：docs/03-数据与选币.md §4.2、§4.3；docs/09-币安数据与API.md §3.1、§3.2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade.data.snapshot import (
    build_rules,
    infer_delisted_rules,
    max_leverage_map,
    rules_to_payload,
    save_timestamped_snapshot,
    write_rules_json,
)
from trade.data.universe import SymbolMeta

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_max_leverage_map_extracts_max() -> None:
    brackets = _load("leverage_bracket.json")
    leverages = max_leverage_map(brackets)
    assert leverages["SOLUSDT"] == 20
    assert leverages["CLUSDT"] == 50
    assert leverages["USDCUSDT"] == 75


def test_max_leverage_map_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        max_leverage_map({"symbols": []})


def test_build_rules_keeps_only_usdt_perpetual() -> None:
    rules = build_rules(_load("exchange_info_rules.json"), _load("leverage_bracket.json"))
    assert set(rules) == {"SOLUSDT", "CLUSDT", "USDCUSDT", "AAPLUSDT"}
    # CURRENT_QUARTER 与 BUSD 报价合约不产出规则（03 §4.2 第 1、2 步）
    assert "BTCUSDT_210925" not in rules
    assert "ETHUSDT" not in rules


def test_build_rules_trading_rules_from_filters_and_brackets() -> None:
    rules = build_rules(_load("exchange_info_rules.json"), _load("leverage_bracket.json"))
    sol = rules["SOLUSDT"]
    assert sol.tick_size == 0.01
    assert sol.step_size == 0.1
    assert sol.min_notional == 5.0
    assert sol.max_leverage == 20


def test_build_rules_marks_stable() -> None:
    rules = build_rules(_load("exchange_info_rules.json"), _load("leverage_bracket.json"))
    assert rules["USDCUSDT"].is_stable is True
    assert rules["SOLUSDT"].is_stable is False


def test_build_rules_marks_tradfi_underlying() -> None:
    rules = build_rules(_load("exchange_info_rules.json"), _load("leverage_bracket.json"))
    assert rules["AAPLUSDT"].underlying_type == "STOCK"
    assert rules["SOLUSDT"].underlying_type == "COIN"


def test_build_rules_excluded_symbols() -> None:
    rules = build_rules(
        _load("exchange_info_rules.json"),
        _load("leverage_bracket.json"),
        excluded_symbols=frozenset({"CLUSDT"}),
    )
    assert rules["CLUSDT"].excluded is True
    assert rules["SOLUSDT"].excluded is False


def test_infer_delisted_rules_from_aggtrades() -> None:
    meta = infer_delisted_rules("LUNAUSDT", [100.0, 100.01, 100.02, 100.0], [1.0, 1.5, 2.5])
    assert meta.tick_size == 0.01
    assert meta.step_size == 0.5
    assert meta.min_notional == 5.0
    assert meta.max_leverage is None
    assert meta.base_asset == "LUNA"


def test_write_rules_json_roundtrip(tmp_path: Path) -> None:
    rules = build_rules(_load("exchange_info_rules.json"), _load("leverage_bracket.json"))
    path = tmp_path / "rules.json"
    write_rules_json(rules, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == set(rules)
    for symbol, fields in raw.items():
        assert "symbol" not in fields
        assert SymbolMeta(symbol=symbol, **fields) == rules[symbol]


def test_rules_to_payload_drops_symbol_key() -> None:
    payload = rules_to_payload({"SOLUSDT": SymbolMeta(symbol="SOLUSDT", base_asset="SOL")})
    assert "symbol" not in payload["SOLUSDT"]
    assert payload["SOLUSDT"]["base_asset"] == "SOL"
    assert payload["SOLUSDT"]["quote_asset"] == "USDT"


def test_save_timestamped_snapshot(tmp_path: Path) -> None:
    path = save_timestamped_snapshot(
        {"symbols": []}, tmp_path, "exchangeInfo", timestamp="20261031T000000Z"
    )
    assert path.name == "exchangeInfo-20261031T000000Z.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"symbols": []}
