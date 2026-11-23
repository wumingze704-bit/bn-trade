"""实盘行情组装测试（docs/07 §6.2、docs/13 §1）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trade.data.universe import read_universe_snapshot
from trade.live.feed import (
    bars_from_funding,
    bars_from_rows,
    merge_bars,
    rules_from_exchange_info,
    statuses_from_exchange_info,
)
from trade.research.data import HOUR_MS, MarketData, UniverseHistory
from trade.research.view import View


def _rows(start: int, prices: list[float]) -> list[list[object]]:
    rows = []
    for index, price in enumerate(prices):
        open_time = start + index * HOUR_MS
        rows.append(
            [
                open_time,
                str(price),
                str(price + 1),
                str(price - 1),
                str(price + 0.5),
                "10",
                open_time + HOUR_MS - 1,
                "1000",
                "5",
            ]
        )
    return rows


def _snapshot_file(tmp_path: Path) -> Path:
    path = tmp_path / "2023-11.json"
    path.write_text(
        json.dumps(
            {
                "month": "2023-11",
                "rebalance_ms": 1_700_000_000_000,
                "rule_hash": "h",
                "coins": [
                    {
                        "symbol": "AAAUSDT",
                        "alias": "AAA",
                        "role": "train",
                        "rank": 1,
                        "daily_quote_median": 1e7,
                        "first_listing_ms": 1_600_000_000_000,
                    }
                ],
                "excluded_counts": {},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_K线转换与合并() -> None:
    start = 1_700_000_000_000
    bars = bars_from_rows("AAAUSDT", _rows(start, [100.0, 101.0]))
    assert bars is not None
    assert bars.open_time.tolist() == [start, start + HOUR_MS]
    merged = merge_bars(bars, bars_from_rows("AAAUSDT", _rows(start + HOUR_MS, [102.0])))
    assert merged is not None
    assert merged.open_time.tolist() == [start, start + HOUR_MS]
    assert merged.close.tolist()[-1] == pytest.approx(102.5)


def test_资金费按相邻结算间隔推断() -> None:
    rows = [
        {"fundingTime": str(index * 8 * HOUR_MS), "fundingRate": "0.0008"} for index in range(3)
    ]
    series = bars_from_funding("AAAUSDT", rows)
    assert series is not None
    assert series.interval_hours.tolist() == pytest.approx([8.0, 8.0, 8.0])
    assert series.hourly_rate.tolist() == pytest.approx([0.0001, 0.0001, 0.0001])


def test_交易所规则与状态提取() -> None:
    info = SimpleNamespace(
        symbols=[
            {
                "symbol": "AAAUSDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    )
    rules = rules_from_exchange_info(info)
    assert rules["AAAUSDT"].tick_size == 0.1
    assert rules["AAAUSDT"].step_size == 0.01
    assert statuses_from_exchange_info(info) == {"AAAUSDT": "TRADING"}


def test_拼出的数据视图与研究数据层一致(tmp_path: Path) -> None:
    start = 1_700_000_000_000
    bars = bars_from_rows("AAAUSDT", _rows(start, [100.0, 101.0]))
    assert bars is not None
    universe = UniverseHistory({"2023-11": read_universe_snapshot(_snapshot_file(tmp_path))})
    info = SimpleNamespace(
        symbols=[
            {
                "symbol": "AAAUSDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    )
    market = MarketData(
        hourly={"AAAUSDT": bars},
        daily={},
        funding={},
        universe=universe,
        rules=rules_from_exchange_info(info),
    )
    view = View(market, start + 2 * HOUR_MS)
    assert view.close("AAAUSDT") == pytest.approx(101.5)
    assert view.universe_symbols() == frozenset({"AAAUSDT"})
