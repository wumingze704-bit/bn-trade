"""R1~R10 策略打分与选择测试（docs/13 §5）。"""

from __future__ import annotations

import numpy as np

from tests.research.helpers import START_MS, make_market
from trade.research.data import DAY_MS, HOUR_MS, Bars
from trade.research.view import View
from trade.strategies.btc_hedged_momentum import BtcHedgedMomentum
from trade.strategies.funding_adj_momentum import FundingAdjMomentum
from trade.strategies.funding_carry_ls import FundingCarryLs
from trade.strategies.liquidity_tiered_momentum import LiquidityTieredMomentum
from trade.strategies.ts_trend import TsTrend
from trade.strategies.vol_timed_momentum import VolTimedMomentum
from trade.strategies.xs_low_vol_ls import XsLowVolLs
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


def _funding_adjusted_market():
    return make_market(
        n=220,
        drift_by_symbol={
            "AAAUSDT": 0.002,
            "BBBUSDT": 0.001,
            "CCCUSDT": -0.001,
            "DDDUSDT": -0.002,
        },
        funding_by_symbol={
            "AAAUSDT": 0.001,
            "BBBUSDT": 0.0,
            "CCCUSDT": 0.0,
            "DDDUSDT": -0.001,
        },
    )


def test_资金费调整动量_无惩罚时选最强多头与最弱空头() -> None:
    view = View(_funding_adjusted_market(), START_MS + 45 * DAY_MS)
    strategy = FundingAdjMomentum(
        lookback_days=3, funding_days=1, funding_weight=0.0, top_k=1, rebalance_hours=168
    )
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    shorts = [target.symbol for target in decision.targets if target.side < 0]
    assert longs == ["AAAUSDT"]
    assert shorts == ["DDDUSDT"]


def test_资金费调整动量_高资金费削弱多头_负资金费削弱空头() -> None:
    view = View(_funding_adjusted_market(), START_MS + 45 * DAY_MS)
    strategy = FundingAdjMomentum(
        lookback_days=3, funding_days=1, funding_weight=5.0, top_k=1, rebalance_hours=168
    )
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    shorts = [target.symbol for target in decision.targets if target.side < 0]
    assert "AAAUSDT" not in longs
    assert "DDDUSDT" not in shorts


def test_资金费调整动量_资金费缺失的币不参与打分() -> None:
    market = _funding_adjusted_market()
    market.funding.pop("AAAUSDT")
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = FundingAdjMomentum(
        lookback_days=3, funding_days=1, funding_weight=0.0, top_k=1, rebalance_hours=168
    )
    decision = strategy.decide(view, {})
    assert all(target.symbol != "AAAUSDT" for target in decision.targets)


def test_低波动多空_做多最低波动做空最高波动() -> None:
    market = make_market(n=220)
    for symbol, vol in (
        ("AAAUSDT", 0.002),
        ("BBBUSDT", 0.010),
        ("CCCUSDT", 0.020),
        ("DDDUSDT", 0.050),
    ):
        market.hourly[symbol] = _variable_vol_bars(symbol, vol=vol)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = XsLowVolLs(vol_days=3, top_k=1)
    decision = strategy.decide(view, {})
    longs = [target.symbol for target in decision.targets if target.side > 0]
    shorts = [target.symbol for target in decision.targets if target.side < 0]
    assert longs == ["AAAUSDT"]
    assert shorts == ["DDDUSDT"]


def test_低波动多空_排除缩量币() -> None:
    market = make_market(n=220)
    for symbol, vol in (
        ("AAAUSDT", 0.002),
        ("BBBUSDT", 0.010),
        ("CCCUSDT", 0.020),
        ("DDDUSDT", 0.050),
    ):
        market.hourly[symbol] = _variable_vol_bars(symbol, vol=vol)
    market.hourly["AAAUSDT"].quote_volume[:] = 1_000.0
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = XsLowVolLs(vol_days=3, top_k=1)
    decision = strategy.decide(view, {})
    assert all(target.symbol != "AAAUSDT" for target in decision.targets)


def _variable_vol_bars(symbol: str, *, vol: float, n: int = 220) -> Bars:
    """构造逐小时 ±vol 交替的对数收益，使 1h 收益标准差等于 vol。"""
    times = START_MS + np.arange(n, dtype=np.int64) * HOUR_MS
    returns = np.asarray([vol if index % 2 == 0 else -vol for index in range(n)], dtype="float64")
    closes = 100.0 * np.exp(np.cumsum(returns))
    return Bars(
        symbol=symbol,
        open_time=times,
        open=closes,
        high=closes * (1.0 + vol),
        low=closes * (1.0 - vol),
        close=closes,
        quote_volume=np.full(n, 5_000_000.0, dtype="float64"),
    )


def test_波动率择时_高波动时按比例缩小暴露() -> None:
    market = make_market(n=220)
    for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        market.hourly[symbol] = _variable_vol_bars(symbol, vol=0.05)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = VolTimedMomentum(lookback_days=3, target_annual_vol=0.5)
    decision = strategy.decide(view, {})
    assert decision.targets
    # 年化组合波动估计约 0.05 × √(24×365) ≈ 4.68，目标 0.5 → 权重约 0.11。
    assert all(target.weight < 0.2 for target in decision.targets)


def test_波动率择时_低波动时保持满仓() -> None:
    market = make_market(n=220)
    for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        market.hourly[symbol] = _variable_vol_bars(symbol, vol=0.001)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = VolTimedMomentum(lookback_days=3, target_annual_vol=1.2)
    decision = strategy.decide(view, {})
    assert decision.targets
    assert all(target.weight == 1.0 for target in decision.targets)


def _trend_vol_bars(symbol: str, *, drift: float, vol: float, n: int = 220) -> Bars:
    """构造带漂移与逐小时波动的 K 线（收益 = drift ± vol 交替）。"""
    times = START_MS + np.arange(n, dtype=np.int64) * HOUR_MS
    returns = np.asarray(
        [drift + (vol if index % 2 == 0 else -vol) for index in range(n)], dtype="float64"
    )
    closes = 100.0 * np.exp(np.cumsum(returns))
    return Bars(
        symbol=symbol,
        open_time=times,
        open=closes,
        high=closes * (1.0 + vol),
        low=closes * (1.0 - vol),
        close=closes,
        quote_volume=np.full(n, 5_000_000.0, dtype="float64"),
    )


def test_BTC对冲单边动量_BTC上涨做多强势组并做空BTC() -> None:
    market = make_market(n=220)
    for symbol, drift in (
        ("AAAUSDT", 0.0008),
        ("BBBUSDT", 0.0004),
        ("CCCUSDT", -0.0004),
        ("DDDUSDT", -0.0008),
        ("BTCUSDT", 0.0002),
    ):
        market.hourly[symbol] = _trend_vol_bars(symbol, drift=drift, vol=0.01)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = BtcHedgedMomentum(lookback_days=3, trend_days=2, top_k=1)
    decision = strategy.decide(view, {})
    symbols = {target.symbol for target in decision.targets}
    assert symbols == {"AAAUSDT", "BTCUSDT"}
    btc = next(target for target in decision.targets if target.symbol == "BTCUSDT")
    assert btc.side == -1


def test_BTC对冲单边动量_BTC下跌做空弱势组并做多BTC() -> None:
    market = make_market(n=220)
    for symbol, drift in (
        ("AAAUSDT", 0.0008),
        ("BBBUSDT", 0.0004),
        ("CCCUSDT", -0.0004),
        ("DDDUSDT", -0.0008),
        ("BTCUSDT", -0.0002),
    ):
        market.hourly[symbol] = _trend_vol_bars(symbol, drift=drift, vol=0.01)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = BtcHedgedMomentum(lookback_days=3, trend_days=2, top_k=1)
    decision = strategy.decide(view, {})
    symbols = {target.symbol for target in decision.targets}
    assert symbols == {"DDDUSDT", "BTCUSDT"}
    btc = next(target for target in decision.targets if target.symbol == "BTCUSDT")
    assert btc.side == 1


def test_流动性分层动量_层内z分数均值为零() -> None:
    symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT")
    market = make_market(symbols=symbols, n=220)
    for index, symbol in enumerate(symbols):
        market.hourly[symbol] = _trend_vol_bars(symbol, drift=0.0002 * (index + 1), vol=0.01)
    for symbol, value in zip(symbols, (6e8, 5e8, 4e8, 3e8, 2e8, 1e8), strict=True):
        market.daily[symbol].quote_volume[:] = value
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = LiquidityTieredMomentum(lookback_days=3, top_k=1)
    scores = strategy.scores_for(view)
    assert len(scores) == 6
    for tier in (symbols[0:2], symbols[2:4], symbols[4:6]):
        assert abs(sum(scores[symbol] for symbol in tier)) < 1e-9


def test_流动性分层动量_层内不足两个币时该层不参与() -> None:
    market = make_market(n=220)
    for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        market.hourly[symbol] = _trend_vol_bars(symbol, drift=0.0004, vol=0.01)
    view = View(market, START_MS + 45 * DAY_MS)
    strategy = LiquidityTieredMomentum(lookback_days=3, top_k=1)
    scores = strategy.scores_for(view)
    # 4 个币分成 3 层 → [2, 1, 1]，只有第一层参与。
    assert len(scores) == 2
