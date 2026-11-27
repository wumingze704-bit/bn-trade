"""View 的溢价指数、主动买入占比与日线访问器（docs/13 §1、§5、§10）。

全部只用 `open_time + 1h ≤ now_ms` 的数据（红线 1），缺少数据返回 None 而不是 0（红线 12）。
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.research.helpers import START_MS, make_market
from trade.research.data import DAY_MS, HOUR_MS, FlowBars, MarkBars
from trade.research.view import View

_NOW_MS = START_MS + 100 * HOUR_MS


def _market_with_premium(symbol: str, closes: np.ndarray):
    market = make_market(n=240)
    bars = market.bars(symbol)
    market.premium[symbol] = MarkBars(
        symbol=symbol,
        open_time=bars.open_time,
        open=closes,
        high=closes,
        low=closes,
        close=closes,
    )
    return market


def test_溢价指数均值只用已收盘数据() -> None:
    closes = np.full(240, 0.0004, dtype="float64")
    market = _market_with_premium("AAAUSDT", closes)
    view = View(market, _NOW_MS)
    assert view.premium_mean("AAAUSDT", 24) == pytest.approx(0.0004)

    # 把决策时刻之后（含当前未收盘那根）的溢价改写，均值必须不变
    closes = closes.copy()
    closes[100:] = 0.5
    market = _market_with_premium("AAAUSDT", closes)
    view = View(market, _NOW_MS)
    assert view.premium_mean("AAAUSDT", 24) == pytest.approx(0.0004)


def test_没有溢价数据的币返回None() -> None:
    market = make_market(n=240)
    view = View(market, _NOW_MS)
    assert view.premium_mean("AAAUSDT", 24) is None


def test_主动买入占比按成交额之和计算() -> None:
    market = make_market(n=240)
    bars = market.bars("AAAUSDT")
    market.flow["AAAUSDT"] = FlowBars(
        symbol="AAAUSDT",
        open_time=bars.open_time,
        taker_buy_volume=bars.quote_volume * 0.4,
        taker_buy_quote_volume=bars.quote_volume * 0.25,
    )
    view = View(market, _NOW_MS)
    assert view.taker_buy_ratio("AAAUSDT", 2) == pytest.approx(0.25)
    assert view.taker_buy_ratio("BBBUSDT", 2) is None


def test_日线收盘序列与均线判定只用已收盘日线() -> None:
    market = make_market(n=240, history_days=60)
    falling = [200.0 - index for index in range(100)]
    bars = market.daily["AAAUSDT"]
    market.daily["AAAUSDT"] = type(bars)(
        symbol="AAAUSDT",
        open_time=bars.open_time,
        open=np.asarray(falling, dtype="float64"),
        high=np.asarray(falling, dtype="float64"),
        low=np.asarray(falling, dtype="float64"),
        close=np.asarray(falling, dtype="float64"),
        quote_volume=bars.quote_volume,
    )
    view = View(market, START_MS + 5 * DAY_MS)
    data = view.daily_closes("AAAUSDT", 10)
    assert data is not None
    times, closes = data
    assert times[-1] < view.now_ms - DAY_MS + HOUR_MS
    assert closes.size == 10
    assert view.daily_close_below_ma("AAAUSDT", 10) is True


def test_日线不足时返回None而不是False() -> None:
    market = make_market(n=240)
    view = View(market, START_MS + HOUR_MS)
    assert View(market, START_MS + HOUR_MS).daily_close_below_ma("AAAUSDT", 400) is None
    assert view.daily_closes("AAAUSDT", 400) is None
