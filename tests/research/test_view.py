"""View 防未来函数与指标口径测试（红线 1）。"""

from __future__ import annotations

import numpy as np
import pytest

from tests.research.helpers import START_MS, make_bars, make_market
from trade.research.data import DAY_MS, HOUR_MS
from trade.research.view import LookaheadError, View


def test_决策时刻只能看到已收盘K线() -> None:
    market = make_market(n=50)
    now = START_MS + 10 * HOUR_MS
    view = View(market, now)
    bars = market.bars("AAAUSDT")
    assert view.close("AAAUSDT") == pytest.approx(float(bars.close[9]))
    assert view.require_closed(now - HOUR_MS) is None
    with pytest.raises(LookaheadError):
        view.require_closed(now)


def test_未来价格变化不影响当前打分() -> None:
    """把决策时刻之后的价格全部改掉，决策时刻的收益与波动必须不变。"""
    base = make_bars("AAAUSDT", n=100, drift=0.001)
    now = START_MS + 60 * HOUR_MS
    altered = base
    altered = type(base)(
        symbol=base.symbol,
        open_time=base.open_time,
        open=base.open.copy(),
        high=base.high.copy(),
        low=base.low.copy(),
        close=base.close.copy(),
        quote_volume=base.quote_volume.copy(),
    )
    cut = 60
    altered.open[cut:] *= 5.0
    altered.high[cut:] *= 5.0
    altered.low[cut:] *= 5.0
    altered.close[cut:] *= 5.0
    market = make_market(n=100)
    market.hourly["AAAUSDT"] = base
    first = View(market, now)
    before = (first.log_return("AAAUSDT", 24), first.hourly_vol("AAAUSDT", 24))
    market.hourly["AAAUSDT"] = altered
    second = View(market, now)
    after = (second.log_return("AAAUSDT", 24), second.hourly_vol("AAAUSDT", 24))
    assert before == pytest.approx(after)


def test_ATR与成交额窗口只用到已收盘数据() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 40 * DAY_MS)
    atr = view.atr_hours("AAAUSDT", period=14, bucket_hours=4)
    assert atr is not None and atr > 0.0
    daily_atr = view.atr_daily("AAAUSDT", period=14)
    assert daily_atr is not None and daily_atr > 0.0
    assert view.mean_quote_volume("AAAUSDT", 24) == pytest.approx(5_000_000.0)


def test_资金费均值按已结算序列() -> None:
    market = make_market(n=100, funding_by_symbol={"AAAUSDT": 0.0002})
    view = View(market, START_MS + 50 * HOUR_MS)
    value = view.funding_hourly_mean("AAAUSDT", 24)
    assert value == pytest.approx(0.0002)
    assert view.funding_hourly_mean("BBBUSDT", 24) is None
    assert not np.isnan(value)
