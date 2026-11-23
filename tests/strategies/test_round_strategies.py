"""R1~R4 策略打分与选择测试（docs/13 §5）。"""

from __future__ import annotations

from tests.research.helpers import START_MS, make_market
from trade.research.data import DAY_MS
from trade.research.view import View
from trade.strategies.funding_carry_ls import FundingCarryLs
from trade.strategies.ts_trend import TsTrend
from trade.strategies.xs_momentum_ls import XsMomentumLs
from trade.strategies.xs_reversal_ls import XsReversalLs


def test_动量多空选择最强与最弱且不因上线天数漏掉多头() -> None:
    market = make_market(
        n=220,
        drift_by_symbol={
            "AAAUSDT": 0.002,
            "BBBUSDT": 0.001,
            "CCCUSDT": -0.001,
            "DDDUSDT": -0.002,
        },
    )
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = XsMomentumLs(lookback_days=3, top_k=1, rebalance_hours=24, min_listing_days=0)
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    shorts = [target.symbol for target in decision.targets if target.side < 0]
    assert longs == ["AAAUSDT"]
    assert shorts == ["DDDUSDT"]
    assert all(0.0 < target.sl <= 0.15 for target in decision.targets)


def test_动量过滤高资金费的多头() -> None:
    market = make_market(
        n=220,
        drift_by_symbol={"AAAUSDT": 0.002, "BBBUSDT": 0.001},
        funding_by_symbol={"AAAUSDT": 0.001, "BBBUSDT": 0.0, "CCCUSDT": 0.0, "DDDUSDT": 0.0},
    )
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = XsMomentumLs(lookback_days=3, top_k=1, rebalance_hours=24, min_listing_days=0)
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    assert "AAAUSDT" not in longs


def test_资金费策略做空最高费率做多最低() -> None:
    market = make_market(
        n=220,
        funding_by_symbol={
            "AAAUSDT": -0.001,
            "BBBUSDT": -0.0002,
            "CCCUSDT": 0.0002,
            "DDDUSDT": 0.001,
        },
    )
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = FundingCarryLs(lookback_days=1, top_k=1)
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    shorts = [target.symbol for target in decision.targets if target.side < 0]
    assert longs == ["AAAUSDT"]
    assert shorts == ["DDDUSDT"]


def test_反转策略排除缩量币() -> None:
    market = make_market(
        n=220,
        drift_by_symbol={
            "AAAUSDT": 0.001,
            "BBBUSDT": 0.0005,
            "CCCUSDT": -0.001,
            "DDDUSDT": -0.0005,
        },
    )
    market.hourly["CCCUSDT"].quote_volume[:] = 1_000_000.0
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = XsReversalLs(lookback_days=1, top_k=1)
    decision = strategy.decide(view, {})
    assert all(target.symbol != "CCCUSDT" for target in decision.targets)


def test_趋势策略BTC过滤只做同号方向() -> None:
    market = make_market(
        n=220,
        drift_by_symbol={
            "AAAUSDT": -0.001,
            "BBBUSDT": 0.001,
            "CCCUSDT": -0.001,
            "DDDUSDT": 0.001,
            "BTCUSDT": 0.001,
        },
    )
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = TsTrend(lookback_days=2, btc_filter=True)
    decision = strategy.decide(view, {})
    assert decision.targets
    assert all(target.side > 0 for target in decision.targets)
    assert "BTCUSDT" in [target.symbol for target in decision.targets]
