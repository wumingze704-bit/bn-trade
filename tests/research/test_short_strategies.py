"""X0~X4、R11、R12 的选币规则与数据源测试（docs/13 §5、§10；1 号第八部分）。

用 tests/fixtures 的合成数据，不依赖真实下载数据。
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from tests.research.helpers import START_MS, make_bars, make_daily_returns_bars, make_market
from trade.data.universe import SnapshotCoin, UniverseSnapshot
from trade.research.data import DAY_MS, HOUR_MS, Bars, FlowBars, MarkBars, UniverseHistory
from trade.research.view import View
from trade.strategies.alt_short_btc_long import beta_to_btc, daily_sigma
from trade.strategies.registry import build_strategy
from trade.strategies.short_basket_baseline import ShortBasketBaseline
from trade.strategies.short_basket_btc_regime import ShortBasketBtcRegime
from trade.strategies.short_crowded_pump import ShortCrowdedPump
from trade.strategies.short_premium_rich import ShortPremiumRich
from trade.strategies.short_taker_exhaustion import ShortTakerExhaustion

_STOP_MAX = 0.15
_NOW_MS = START_MS + 10 * HOUR_MS
_HISTORY_DAYS = 60


def _daily_bars(symbol: str, closes: list[float]) -> MarkBars:
    """构造日线收盘序列（最后一根在 `START_MS` 之前结束）。"""
    values = np.asarray(closes, dtype="float64")
    times = START_MS - values.size * DAY_MS + np.arange(values.size, dtype=np.int64) * DAY_MS
    return MarkBars(
        symbol=symbol,
        open_time=times,
        open=values,
        high=values * 1.001,
        low=values * 0.999,
        close=values,
    )


def _with_daily(market, symbol: str, closes: list[float]):
    """把某个币的日线替换成给定收盘序列（用于 BTC 均线与 R12 指数）。"""
    mark = _daily_bars(symbol, closes)
    market.daily[symbol] = Bars(
        symbol=symbol,
        open_time=mark.open_time,
        open=mark.open,
        high=mark.high,
        low=mark.low,
        close=mark.close,
        quote_volume=np.full(mark.close.size, 1e8, dtype="float64"),
    )
    return market


def _universe_with_ages(symbols: dict[str, float]) -> UniverseHistory:
    """按 `{symbol: 上市天数}` 构造选币快照（覆盖测试用到的月份）。"""
    coins = [
        SnapshotCoin(
            symbol=symbol,
            alias=f"C{index:03d}",
            role="train",
            rank=index,
            daily_quote_median=1e9,
            first_listing_ms=START_MS - int(age * DAY_MS),
        )
        for index, (symbol, age) in enumerate(symbols.items(), start=1)
    ]
    snapshots = {}
    for month_index in range(18):
        year = 2024 + month_index // 12
        month = 1 + month_index % 12
        key = f"{year:04d}-{month:02d}"
        snapshots[key] = UniverseSnapshot(
            month=key,
            rebalance_ms=int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000),
            rule_hash="test",
            coins=coins,
            excluded_counts={},
        )
    return UniverseHistory(snapshots)


def _market_with_premium(values: dict[str, float]):
    """给每个币写入溢价指数 K 线（收盘恒为给定值）。"""
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    for symbol, value in values.items():
        bars = market.bars(symbol)
        market.premium[symbol] = MarkBars(
            symbol=symbol,
            open_time=bars.open_time,
            open=np.full(bars.open_time.size, value),
            high=np.full(bars.open_time.size, value),
            low=np.full(bars.open_time.size, value),
            close=np.full(bars.open_time.size, value),
        )
    return market


def _market_with_flow(ratios: dict[str, float]):
    """按给定主动买入占比写入 `klines_1h_flow`（quote_volume 由 helper 固定）。"""
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    for symbol, ratio in ratios.items():
        bars = market.bars(symbol)
        market.flow[symbol] = FlowBars(
            symbol=symbol,
            open_time=bars.open_time,
            taker_buy_volume=bars.quote_volume * ratio,
            taker_buy_quote_volume=bars.quote_volume * ratio,
        )
    return market


def test_X0按成交额取前N个山寨且不含主流币() -> None:
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    view = View(market, _NOW_MS)
    strategy = ShortBasketBaseline(basket_n=2, stop_max=_STOP_MAX)
    decision = strategy.decide(view, {})

    assert len(decision.targets) == 2
    assert all(target.side == -1 for target in decision.targets)
    assert all(target.sl == pytest.approx(_STOP_MAX) for target in decision.targets)
    assert not {"BTCUSDT", "ETHUSDT"} & {target.symbol for target in decision.targets}


def test_X0剔除资金费拥挤与上线不足的币() -> None:
    market = make_market(
        n=240,
        history_days=_HISTORY_DAYS,
        funding_by_symbol={"AAAUSDT": 0.0002, "BBBUSDT": -0.0009},
    )
    market.universe = _universe_with_ages(
        {"AAAUSDT": 400.0, "BBBUSDT": 400.0, "CCCUSDT": 400.0, "DDDUSDT": 10.0}
    )
    view = View(market, _NOW_MS)
    strategy = ShortBasketBaseline(basket_n=4, stop_max=_STOP_MAX)
    picked = {target.symbol for target in strategy.decide(view, {}).targets}
    assert "BBBUSDT" not in picked
    assert "DDDUSDT" not in picked
    assert picked == {"AAAUSDT", "CCCUSDT"}


def test_X1只在BTC跌破均线时持仓() -> None:
    falling = [100.0 - index for index in range(120)]
    rising = [100.0 + index for index in range(120)]
    market = _with_daily(make_market(n=240, history_days=_HISTORY_DAYS), "BTCUSDT", falling)
    view = View(market, _NOW_MS)
    strategy = ShortBasketBtcRegime(basket_n=2, ma_days=50, stop_max=_STOP_MAX)
    assert len(strategy.decide(view, {}).targets) == 2

    market = _with_daily(make_market(n=240, history_days=_HISTORY_DAYS), "BTCUSDT", rising)
    view = View(market, _NOW_MS)
    assert strategy.decide(view, {}).targets == ()


def test_X1日线不足时抛出异常而不是当作牛市() -> None:
    market = _with_daily(make_market(n=240, history_days=_HISTORY_DAYS), "BTCUSDT", [100.0] * 30)
    view = View(market, _NOW_MS)
    strategy = ShortBasketBtcRegime(basket_n=2, ma_days=50, stop_max=_STOP_MAX)
    with pytest.raises(ValueError, match="日线不足"):
        strategy.decide(view, {})


def test_X2按资金费从高到低做空暴涨币() -> None:
    market = make_market(
        n=240,
        history_days=_HISTORY_DAYS,
        drift_by_symbol={"AAAUSDT": 0.002, "BBBUSDT": 0.002, "CCCUSDT": -0.001},
        funding_by_symbol={
            "AAAUSDT": 0.0005,
            "BBBUSDT": 0.0001,
            "CCCUSDT": 0.0002,
            "DDDUSDT": 0.0001,
        },
    )
    view = View(market, _NOW_MS)
    strategy = ShortCrowdedPump(lookback_days=3, top_k=1, stop_max=_STOP_MAX)
    decision = strategy.decide(view, {})
    assert [target.symbol for target in decision.targets] == ["AAAUSDT"]


def test_X3只做空溢价为正且最高的币() -> None:
    market = _market_with_premium({"AAAUSDT": 0.0008, "BBBUSDT": 0.0002, "CCCUSDT": -0.0003})
    view = View(market, _NOW_MS)
    strategy = ShortPremiumRich(window_hours=24, top_k=2, stop_max=_STOP_MAX)
    picked = [target.symbol for target in strategy.decide(view, {}).targets]
    assert picked == ["AAAUSDT", "BBBUSDT"]
    assert "DDDUSDT" not in picked


def test_X4按主动买入占比与涨幅的截面z分做空() -> None:
    market = _market_with_flow({"BBBUSDT": 0.45, "CCCUSDT": 0.3})
    bars = make_bars(
        "AAAUSDT",
        n=240 + _HISTORY_DAYS * 24,
        start_ms=START_MS - _HISTORY_DAYS * DAY_MS,
        drift=0.002,
    )
    market.hourly["AAAUSDT"] = bars
    market.flow["AAAUSDT"] = FlowBars(
        symbol="AAAUSDT",
        open_time=bars.open_time,
        taker_buy_volume=bars.quote_volume * 0.85,
        taker_buy_quote_volume=bars.quote_volume * 0.85,
    )
    view = View(market, _NOW_MS)
    strategy = ShortTakerExhaustion(window_days=1, top_k=1, stop_max=_STOP_MAX)
    decision = strategy.decide(view, {})
    assert [target.symbol for target in decision.targets] == ["AAAUSDT"]
    assert "DDDUSDT" not in {target.symbol for target in decision.targets}


def test_X4主动买入与K线时间轴不一致时报错() -> None:
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    bars = market.bars("AAAUSDT")
    # 去掉决策窗口内最后 2 根已收盘 K 线，模拟 klines_1h_flow 缺行（docs/13 §1）
    count = bars.closed_count(_NOW_MS)
    times = np.concatenate([bars.open_time[: count - 2], bars.open_time[count:]])
    market.flow["AAAUSDT"] = FlowBars(
        symbol="AAAUSDT",
        open_time=times,
        taker_buy_volume=np.full(times.size, 1.0),
        taker_buy_quote_volume=np.full(times.size, 1.0),
    )
    view = View(market, _NOW_MS)
    with pytest.raises(ValueError, match="时间轴不一致"):
        view.taker_buy_ratio("AAAUSDT", 1)


def test_R11按上线天数从小到大取并受BTC均线过滤() -> None:
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    market.universe = _universe_with_ages(
        {"AAAUSDT": 100.0, "BBBUSDT": 150.0, "CCCUSDT": 400.0, "DDDUSDT": 30.0}
    )
    market = _with_daily(market, "BTCUSDT", [100.0 - index for index in range(220)])
    view = View(market, _NOW_MS)
    strategy = build_strategy(
        "short_seasoned_listing",
        {"age_max": 120, "top_k": 2, "btc_ma_filter": 1},
        stop_max=_STOP_MAX,
    )
    decision = strategy.decide(view, {})
    assert [target.symbol for target in decision.targets] == ["AAAUSDT"]

    market = _with_daily(market, "BTCUSDT", [100.0 + index for index in range(220)])
    view = View(market, _NOW_MS)
    assert strategy.decide(view, {}).targets == ()


def test_R12的BTC腿权重使事前β为0() -> None:
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    market.universe = _universe_with_ages(
        {"AAAUSDT": 400.0, "BBBUSDT": 400.0, "CCCUSDT": 400.0, "DDDUSDT": 400.0}
    )
    rng = np.random.default_rng(7)
    btc_returns = rng.normal(0.001, 0.01, 90)
    market.daily["BTCUSDT"] = make_daily_returns_bars("BTCUSDT", btc_returns)
    for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        alt_returns = 2.0 * btc_returns + rng.normal(0.0, 0.0005, 90)
        market.daily[symbol] = make_daily_returns_bars(symbol, alt_returns)
    view = View(market, _NOW_MS)
    strategy = build_strategy(
        "alt_short_btc_long",
        {"short_n": 2, "drop_negative_funding": 0, "alt_index_filter": 0},
        stop_max=_STOP_MAX,
    )
    decision = strategy.decide(view, {})
    weights = {target.symbol: (target.side, target.weight) for target in decision.targets}
    assert weights["BTCUSDT"][0] == 1
    shorts = [symbol for symbol, (side, _w) in weights.items() if side < 0]
    assert len(shorts) == 2

    # 模式 B 的名义 ∝ 权重 ÷ σ；BTC 腿的权重使两边的事前 β 相等（组合 β ≈ 0）
    short_beta = sum(beta_to_btc(view, symbol) / daily_sigma(view, symbol) for symbol in shorts)
    btc_beta = weights["BTCUSDT"][1] / daily_sigma(view, "BTCUSDT")
    assert btc_beta == pytest.approx(short_beta, rel=1e-9)


def test_R12在山寨季时空仓() -> None:
    market = make_market(n=240, history_days=_HISTORY_DAYS)
    market.universe = _universe_with_ages(
        {"AAAUSDT": 400.0, "BBBUSDT": 400.0, "CCCUSDT": 400.0, "DDDUSDT": 400.0}
    )
    rng = np.random.default_rng(11)
    market.daily["BTCUSDT"] = make_daily_returns_bars("BTCUSDT", rng.normal(0.0, 0.01, 130))
    for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        alt_returns = 0.03 + rng.normal(0.0, 0.005, 130)
        market.daily[symbol] = make_daily_returns_bars(symbol, alt_returns)
    view = View(market, _NOW_MS)
    strategy = build_strategy(
        "alt_short_btc_long",
        {"short_n": 2, "drop_negative_funding": 0, "alt_index_filter": 1},
        stop_max=_STOP_MAX,
    )
    assert strategy.decide(view, {}).targets == ()


def test_新方向的注册表条目与暴露方向一致() -> None:
    cases = {
        "short_basket_baseline": ("directional", {"basket_n": 10}),
        "short_basket_btc_regime": ("directional", {"basket_n": 10, "ma_days": 50}),
        "short_crowded_pump": ("directional", {"lookback_days": 3, "top_k": 3}),
        "short_premium_rich": ("directional", {"window_hours": 24, "top_k": 3}),
        "short_taker_exhaustion": ("directional", {"window_days": 1, "top_k": 3}),
        "short_seasoned_listing": ("directional", {"age_max": 120, "top_k": 3, "btc_ma_filter": 0}),
        "alt_short_btc_long": (
            "neutral",
            {"short_n": 5, "drop_negative_funding": 0, "alt_index_filter": 0},
        ),
    }
    for family, (exposure, params) in cases.items():
        strategy = build_strategy(family, params, stop_max=_STOP_MAX)
        assert strategy.exposure == exposure
