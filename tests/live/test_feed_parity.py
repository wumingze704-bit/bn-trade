"""实时数据视图与研究数据层逐值一致（3 号第八部分第 9 条）。

规格：docs/07-实盘与风控.md §4；docs/13-研究框架.md §1
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from trade.data.universe import read_universe_snapshot
from trade.live.feed import LiveFeed, rules_from_exchange_info
from trade.research.data import DAY_MS, HOUR_MS, UniverseHistory, load_market_data

START = 1_704_067_200_000  # 2024-01-01 00:00 UTC
COUNT = 6
MONTH = "2024-01"


class FakeRestSource:
    """按窗口切片返回预置 K 线的 REST 桩（不访问网络）。"""

    def __init__(
        self,
        *,
        hourly: list[list[object]],
        daily: list[list[object]],
        mark: list[list[object]],
        premium: list[list[object]],
        funding: list[dict[str, object]],
    ) -> None:
        self.hourly = hourly
        self.daily = daily
        self.mark = mark
        self.premium = premium
        self.funding = funding

    @staticmethod
    def _slice(
        rows: list[list[object]], start_time: int | None, end_time: int | None, limit: int
    ) -> list[list[object]]:
        selected = [
            row
            for row in rows
            if (start_time is None or int(row[0]) >= start_time)
            and (end_time is None or int(row[0]) < end_time)
        ]
        return selected[:limit]

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[object]]:
        rows = self.hourly if interval == "1h" else self.daily
        return self._slice(rows, start_time, end_time, limit)

    async def mark_price_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[object]]:
        return self._slice(self.mark, start_time, end_time, limit)

    async def premium_index_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[object]]:
        return self._slice(self.premium, start_time, end_time, limit)

    async def funding_rate(self, symbol: str, *, limit: int = 500) -> list[dict[str, object]]:
        return self.funding[:limit]

    async def book_ticker(self, symbol: str | None = None) -> list[object]:
        return []

    async def exchange_info(self) -> object:
        raise NotImplementedError


def _rows() -> tuple[
    list[list[object]], list[list[object]], list[list[object]], list[dict[str, object]]
]:
    hourly: list[list[object]] = []
    mark: list[list[object]] = []
    premium: list[list[object]] = []
    for index in range(COUNT):
        open_time = START + index * HOUR_MS
        price = 100.0 + index
        hourly.append(
            [
                open_time,
                str(price),
                str(price + 1.0),
                str(price - 1.0),
                str(price + 0.5),
                "10",
                open_time + HOUR_MS - 1,
                "1000",
                "5",
                str(2.0 + index),
                str(200.0 + index),
                "0",
            ]
        )
        mark.append(
            [open_time, str(price), str(price + 0.8), str(price - 0.8), str(price + 0.2), "0"]
        )
        premium.append([open_time, "0.0001", "0.0002", "0.0000", "0.0001", "0"])
    funding = [
        {"fundingTime": str(START + 8 * HOUR_MS * index), "fundingRate": "0.0008"}
        for index in range(3)
    ]
    return hourly, mark, premium, funding


def _write(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _build_dirs(
    root: Path, hourly: list[list[object]], mark: list[list[object]], premium: list[list[object]]
) -> None:
    """把同一批 K 线写成年月分区 parquet，供研究数据层读取。"""
    hourly_frame = pd.DataFrame(
        {
            "open_time": [row[0] for row in hourly],
            "open": [float(row[1]) for row in hourly],
            "high": [float(row[2]) for row in hourly],
            "low": [float(row[3]) for row in hourly],
            "close": [float(row[4]) for row in hourly],
            "volume": [float(row[5]) for row in hourly],
            "quote_volume": [float(row[7]) for row in hourly],
        }
    )
    _write(root / "klines_1h" / "AAAUSDT" / f"{MONTH}.parquet", hourly_frame)
    _write(root / "klines_1d" / "AAAUSDT" / f"{MONTH}.parquet", hourly_frame.copy())
    price_frame = pd.DataFrame(
        {
            "open_time": [row[0] for row in mark],
            "open": [float(row[1]) for row in mark],
            "high": [float(row[2]) for row in mark],
            "low": [float(row[3]) for row in mark],
            "close": [float(row[4]) for row in mark],
        }
    )
    _write(root / "mark_1h" / "AAAUSDT" / f"{MONTH}.parquet", price_frame)
    premium_frame = pd.DataFrame(
        {
            "open_time": [row[0] for row in premium],
            "open": [float(row[1]) for row in premium],
            "high": [float(row[2]) for row in premium],
            "low": [float(row[3]) for row in premium],
            "close": [float(row[4]) for row in premium],
        }
    )
    _write(root / "premium_1h" / "AAAUSDT" / f"{MONTH}.parquet", premium_frame)

    _write(
        root / "klines_1h_flow" / "AAAUSDT" / f"{MONTH}.parquet",
        pd.DataFrame(
            {
                "open_time": [row[0] for row in hourly],
                "taker_buy_volume": [float(row[9]) for row in hourly],
                "taker_buy_quote_volume": [float(row[10]) for row in hourly],
            }
        ),
    )
    _write(
        root / "funding" / "AAAUSDT" / f"{MONTH}.parquet",
        pd.DataFrame(
            {
                "calc_time": [START + 8 * HOUR_MS * index for index in range(3)],
                "hourly_rate": [0.0001, 0.0001, 0.0001],
                "interval_hours": [8.0, 8.0, 8.0],
            }
        ),
    )
    (root / "universe_hist").mkdir(parents=True, exist_ok=True)
    (root / "universe_hist" / f"{MONTH}.json").write_text(
        json.dumps(
            {
                "month": MONTH,
                "rebalance_ms": START,
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
    (root / "rules.json").write_text(
        json.dumps({"AAAUSDT": {"tick_size": 0.1, "step_size": 0.01, "min_notional": 5.0}}),
        encoding="utf-8",
    )


def _daily_rows() -> list[list[object]]:
    """1d 回补用的日线（只满足 LiveFeed 的必需数据，不参与逐值比较）。"""
    rows: list[list[object]] = []
    for index in range(6):
        open_time = START - (6 - index) * DAY_MS
        rows.append(
            [
                open_time,
                "100",
                "101",
                "99",
                "100",
                "1",
                open_time + DAY_MS - 1,
                "1000",
                "0",
                "0",
                "0",
                "0",
            ]
        )
    return rows


def test_实时视图与研究数据层逐值一致(tmp_path: Path) -> None:
    """REST 拼出的 1h / 标记价格 / 主动买入 / 溢价指数与研究数据层逐值相等。"""
    hourly, mark, premium, funding = _rows()
    _build_dirs(tmp_path, hourly, mark, premium)

    research = load_market_data(
        klines_1h=tmp_path / "klines_1h",
        klines_1d=tmp_path / "klines_1d",
        funding=tmp_path / "funding",
        universe_hist=tmp_path / "universe_hist",
        rules_path=tmp_path / "rules.json",
        start_ms=START,
        end_ms=START + COUNT * HOUR_MS,
        start_month=MONTH,
        end_month=MONTH,
        mark_1h=tmp_path / "mark_1h",
        flow_1h=tmp_path / "klines_1h_flow",
        premium_1h=tmp_path / "premium_1h",
        extra_symbols=(),
    )
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
    universe = UniverseHistory(
        {MONTH: read_universe_snapshot(tmp_path / "universe_hist" / f"{MONTH}.json")}
    )
    feed = LiveFeed(
        source=FakeRestSource(
            hourly=hourly,
            daily=_daily_rows(),
            mark=mark,
            premium=premium,
            funding=funding,
        ),
        rules=rules_from_exchange_info(info),
        universe=universe,
        history_days_hourly=5,
        history_days_daily=5,
        funding_limit=100,
        with_premium=True,
    )
    asyncio.run(feed.warmup({"AAAUSDT"}, START + COUNT * HOUR_MS))
    live = feed.market()

    research_bars = research.hourly["AAAUSDT"]
    live_bars = live.hourly["AAAUSDT"]
    for field in ("open_time", "open", "high", "low", "close", "quote_volume"):
        assert np.array_equal(getattr(live_bars, field), getattr(research_bars, field)), (
            f"1h {field} 不一致"
        )
    live_flow = live.flow_bars("AAAUSDT")
    research_flow = research.flow_bars("AAAUSDT")
    assert live_flow is not None and research_flow is not None
    assert np.array_equal(live_flow.open_time, research_flow.open_time)
    assert np.array_equal(live_flow.taker_buy_volume, research_flow.taker_buy_volume)
    assert np.array_equal(live_flow.taker_buy_quote_volume, research_flow.taker_buy_quote_volume)
    live_mark = live.mark_bars("AAAUSDT")
    research_mark = research.mark_bars("AAAUSDT")
    assert live_mark is not None and research_mark is not None
    for field in ("open_time", "high", "low", "close"):
        assert np.array_equal(getattr(live_mark, field), getattr(research_mark, field))
    live_premium = live.premium_bars("AAAUSDT")
    research_premium = research.premium_bars("AAAUSDT")
    assert live_premium is not None and research_premium is not None
    assert np.array_equal(live_premium.close, research_premium.close)
    live_funding = live.funding_series("AAAUSDT")
    research_funding = research.funding_series("AAAUSDT")
    assert live_funding is not None and research_funding is not None
    assert np.array_equal(live_funding.calc_time, research_funding.calc_time)
    assert np.array_equal(live_funding.hourly_rate, research_funding.hourly_rate)
