"""研究期交易规则集合的测试（离线 fixture，不访问网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.data.rules_research import (
    SOURCE_EXCHANGE,
    SOURCE_SNAPSHOT,
    SOURCE_SYNTHETIC,
    collect_rules,
    rules_from_exchange_info,
    synthetic_rule,
    tradfi_symbols,
)


def _exchange_info(symbols: list[dict[str, object]]) -> dict[str, object]:
    """构造最小 exchangeInfo 响应。"""
    return {"symbols": symbols}


def _symbol(
    symbol: str,
    *,
    contract_type: str = "PERPETUAL",
    underlying_type: str = "COIN",
    tick: str = "0.01",
    step: str = "0.001",
    notional: str = "5",
    quote: str = "USDT",
    status: str = "TRADING",
) -> dict[str, object]:
    """构造 exchangeInfo 的单个合约。"""
    return {
        "symbol": symbol,
        "contractType": contract_type,
        "quoteAsset": quote,
        "baseAsset": symbol.removesuffix("USDT"),
        "underlyingType": underlying_type,
        "status": status,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick},
            {"filterType": "LOT_SIZE", "stepSize": step},
            {"filterType": "MIN_NOTIONAL", "notional": notional},
        ],
    }


def test_当前快照保留非交易状态与TradFi合约字段() -> None:
    """来源①包含 status 不是 TRADING 的合约与 TradFi 合约，并保留 contractType。"""
    raw = _exchange_info(
        [
            _symbol("AAAUSDT"),
            _symbol("BBBUSDT", status="SETTLING"),
            _symbol("AAPLUSDT", contract_type="TRADIFI_PERPETUAL", underlying_type="EQUITY"),
            _symbol("ETHBTC", quote="BTC"),
        ]
    )
    rules = rules_from_exchange_info(raw)
    assert sorted(rules) == ["AAAUSDT", "AAPLUSDT", "BBBUSDT"]
    assert rules["BBBUSDT"].tick_size == pytest.approx(0.01)
    assert rules["AAPLUSDT"].contract_type == "TRADIFI_PERPETUAL"
    assert rules["AAPLUSDT"].underlying_type == "EQUITY"


def test_合成规则按最小价差与十的整数次幂() -> None:
    """tickSize 向下取到 10 的整数次幂；stepSize 使 1 个 step 名义 ≤ 0.1 USDT。"""
    prices = np.array([2.5, 2.5007, 2.5003, 2.6, 2.4])
    meta = synthetic_rule("XYZUSDT", prices)
    assert meta.tick_size == pytest.approx(0.0001)
    assert meta.step_size == pytest.approx(0.01)
    assert meta.step_size * float(np.median(prices)) <= 0.1
    assert meta.min_notional == pytest.approx(5.0)
    assert meta.contract_type == "PERPETUAL"


def test_合成规则在价格全相同时报错() -> None:
    """没有正价差时抛 ValueError，不得用默认值继续（红线 12）。"""
    with pytest.raises(ValueError, match="没有正价差"):
        synthetic_rule("XYZUSDT", np.array([1.0, 1.0, 1.0]))


def test_规则来源顺序为当前快照_历史快照_合成(tmp_path) -> None:
    """优先当前快照，其次更早快照，最后合成；来源逐币写入。"""
    current = _exchange_info([_symbol("AAAUSDT")])
    older = _exchange_info([_symbol("AAAUSDT", tick="0.5"), _symbol("CCCUSDT")])
    for month in ("2024-01", "2024-02"):
        directory = tmp_path / "klines_1h" / "DDDUSDT"
        directory.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            {
                "open": [1.0, 1.001],
                "high": [1.002, 1.003],
                "low": [0.999, 1.0],
                "close": [1.001, 1.002],
            }
        )
        frame.to_parquet(directory / f"{month}.parquet", index=False)
    collection = collect_rules(
        exchange_infos=[current, older],
        symbols=["AAAUSDT", "CCCUSDT", "DDDUSDT"],
        klines_1h=tmp_path / "klines_1h",
    )
    assert collection.sources == {
        "AAAUSDT": SOURCE_EXCHANGE,
        "CCCUSDT": SOURCE_SNAPSHOT,
        "DDDUSDT": SOURCE_SYNTHETIC,
    }
    assert collection.rules["AAAUSDT"].tick_size == pytest.approx(0.01)
    assert collection.rules["DDDUSDT"].min_notional == pytest.approx(5.0)


def test_缺少交易所快照时直接报错() -> None:
    """没有 exchangeInfo 快照不得继续（红线 12）。"""
    with pytest.raises(ValueError, match="没有 exchangeInfo 快照"):
        collect_rules(exchange_infos=[], symbols=[], klines_1h="")


def test_TradFi名单包含非永续与股票标的() -> None:
    """TradFi 名单由 contractType 与 underlyingType 共同判定。"""
    rules = rules_from_exchange_info(
        _exchange_info(
            [
                _symbol("AAAUSDT"),
                _symbol("AAPLUSDT", contract_type="TRADIFI_PERPETUAL", underlying_type="EQUITY"),
                _symbol("XAUUSDT", underlying_type="COMMODITY"),
            ]
        )
    )
    assert tradfi_symbols(rules) == ["AAPLUSDT", "XAUUSDT"]
