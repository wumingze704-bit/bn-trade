"""组合层上限、中性平衡与丢弃统计测试（docs/13 §3）。"""

from __future__ import annotations

import numpy as np
import pytest

from tests.research.helpers import (
    START_MS,
    execution_config,
    make_daily_returns_bars,
    make_market,
    portfolio_config,
)
from trade.common.sizing import SizingConfig
from trade.research.data import HOUR_MS
from trade.research.interface import EXPOSURE_DIRECTIONAL, EXPOSURE_NEUTRAL, Decision, Target
from trade.research.portfolio import plan_positions
from trade.research.view import View


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=100.0,
        mode="fixed_risk",
        position_frac=0.08,
        leverage=5,
        max_positions=10,
        base="equity",
        risk_per_trade=0.0075,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def test_中性策略多空名义相等() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision(
        (
            Target("AAAUSDT", 1, 0.03),
            Target("BBBUSDT", 1, 0.05),
            Target("CCCUSDT", -1, 0.03),
            Target("DDDUSDT", -1, 0.05),
        ),
        EXPOSURE_NEUTRAL,
    )
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned
    assert stats.long_notional == pytest.approx(stats.short_notional, rel=1e-9)
    assert stats.long_notional + stats.short_notional <= 2.0 * 100.0 + 1e-9
    assert (stats.long_notional + stats.short_notional) / 5.0 <= 0.95 * 100.0 + 1e-9


def test_净敞口与总名义上限() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision(
        tuple(Target(symbol, 1, 0.02) for symbol in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")),
        EXPOSURE_DIRECTIONAL,
    )
    small = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(net_exposure_cap=0.1, total_notional_cap=2.0),
        fee_taker=0.0005,
        blocked=set(),
    )[1]
    assert small.long_notional <= 0.1 * 100.0 + 1e-6


def test_冷却中的币被丢弃() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision((Target("AAAUSDT", 1, 0.03),), EXPOSURE_NEUTRAL)
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked={"AAAUSDT"},
    )
    assert planned == []
    assert stats.dropped == 1


def test_止损距离超过上限被拒绝() -> None:
    market = make_market(n=120)
    view = View(market, START_MS + 100 * HOUR_MS)
    decision = Decision((Target("AAAUSDT", 1, 0.30),), EXPOSURE_NEUTRAL)
    planned, stats = plan_positions(
        decision,
        view=view,
        equity=100.0,
        available_margin=95.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned == []
    assert stats.dropped == 1


def _returns(seed: int, sigma: float, n: int = 60) -> np.ndarray:
    """指定样本标准差的日对数收益（手算模式 B 权重用）。"""
    rng = np.random.default_rng(seed)
    values = rng.normal(0.0, sigma, n)
    values -= values.mean()
    values *= sigma / float(values.std(ddof=1))
    return values


def _install_daily(market, series: dict[str, np.ndarray]) -> None:
    for symbol, values in series.items():
        market.daily[symbol] = make_daily_returns_bars(symbol, values)


def _view(market) -> View:  # type: ignore[no-untyped-def]
    return View(market, START_MS + 100 * HOUR_MS)


def test_模式B按波动率倒数权重并缩放到目标波动() -> None:
    """h 款：模式 B 的单仓名义 = 权益 × 缩放 × (1/σ)，缩放使组合事前年化波动 = target_vol。"""
    symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
    series = {symbol: _returns(index, 0.05) for index, symbol in enumerate(symbols)}
    series["BTCUSDT"] = _returns(99, 0.03)
    market = make_market(symbols=symbols, n=120)
    _install_daily(market, series)
    decision = Decision(tuple(Target(symbol, 1, 0.03) for symbol in symbols), EXPOSURE_DIRECTIONAL)
    sizing = _sizing().model_copy(update={"sizing_mode": "vol_target", "target_vol": 0.20})
    planned, stats = plan_positions(
        decision,
        view=_view(market),
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=sizing,
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    ordered = sorted(symbols)
    sigma = np.asarray([np.std(series[symbol][-30:], ddof=1) for symbol in ordered])
    weights = 1.0 / sigma
    matrix = np.column_stack([series[symbol][-60:] for symbol in ordered])
    covariance = np.cov(matrix, rowvar=False, ddof=1)
    variance = float(weights @ covariance @ weights)
    scale = 0.20 / (np.sqrt(365.0) * np.sqrt(variance))
    expected = {
        symbol: 10_000.0 * scale * weight for symbol, weight in zip(ordered, weights, strict=True)
    }
    risk_cap = 10_000.0 * 0.025 / 0.15
    assert all(notional < risk_cap for notional in expected.values())
    assert len(planned) == len(symbols)
    assert stats.attempted == len(symbols)
    for position in planned:
        assert position.sl == pytest.approx(0.15)
        assert position.notional == pytest.approx(expected[position.symbol], rel=1e-9)
    assert stats.long_notional == pytest.approx(sum(expected.values()), rel=1e-9)


def test_模式B单仓上限生效() -> None:
    """h 款：目标波动较大时每个仓位被“权益 × max_risk_per_trade ÷ stop_max”截断。"""
    symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
    series = {symbol: _returns(index + 10, 0.005) for index, symbol in enumerate(symbols)}
    series["BTCUSDT"] = _returns(98, 0.03)
    market = make_market(symbols=symbols, n=120)
    _install_daily(market, series)
    decision = Decision(tuple(Target(symbol, 1, 0.03) for symbol in symbols), EXPOSURE_DIRECTIONAL)
    sizing = _sizing().model_copy(update={"sizing_mode": "vol_target", "target_vol": 0.20})
    planned, stats = plan_positions(
        decision,
        view=_view(market),
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=sizing,
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    risk_cap = 10_000.0 * 0.025 / 0.15
    assert planned
    for position in planned:
        assert position.notional == pytest.approx(risk_cap, rel=1e-9)
    assert stats.capped["risk_cap"] == len(planned)


def test_中性策略按β调整多空名义并满足约束() -> None:
    """h 款：β 调整后组合事前 |β| ≤ 0.10，多空名义比在 [0.5, 2.0] 内。"""
    btc = _returns(7, 0.03)
    betas = {"AAAUSDT": 0.5, "BBBUSDT": 1.5, "CCCUSDT": 1.5, "DDDUSDT": 1.5}
    series = {symbol: beta * btc for symbol, beta in betas.items()}
    series["BTCUSDT"] = btc
    market = make_market(symbols=("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"), n=120)
    _install_daily(market, series)
    decision = Decision(
        (
            Target("AAAUSDT", 1, 0.03),
            Target("BBBUSDT", 1, 0.03),
            Target("CCCUSDT", -1, 0.03),
            Target("DDDUSDT", -1, 0.03),
        ),
        EXPOSURE_NEUTRAL,
    )
    planned, stats = plan_positions(
        decision,
        view=_view(market),
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    notional = 10_000.0 * 0.0075 / (0.03 + 2 * 0.0001 + 0.0001 + 2 * 0.0005)
    by_symbol = {position.symbol: position for position in planned}
    assert by_symbol["AAAUSDT"].notional == pytest.approx(notional, rel=1e-9)
    assert by_symbol["BBBUSDT"].notional == pytest.approx(notional, rel=1e-9)
    # 空头 β 之和 3n > 多头 2n：空头按 2/3 缩小后两边 β 名义相等
    assert by_symbol["CCCUSDT"].notional == pytest.approx(notional * 2.0 / 3.0, rel=1e-9)
    assert by_symbol["DDDUSDT"].notional == pytest.approx(notional * 2.0 / 3.0, rel=1e-9)
    beta_exposure = sum(
        position.side * position.notional * betas[position.symbol] for position in planned
    )
    assert abs(beta_exposure) / 10_000.0 <= 0.10 + 1e-12
    assert 0.5 <= stats.long_notional / stats.short_notional <= 2.0
    assert stats.beta_default == 0


def test_β样本不足30天时取1并计数() -> None:
    """h 款：日线样本不足 30 天的币 β 取 1.0，并计入 beta_default。"""
    btc = _returns(13, 0.03)
    market = make_market(symbols=("AAAUSDT", "BBBUSDT"), n=120)
    _install_daily(
        market,
        {
            "AAAUSDT": _returns(1, 0.03, n=10),
            "BBBUSDT": _returns(2, 0.03, n=10),
            "BTCUSDT": btc,
        },
    )
    decision = Decision((Target("AAAUSDT", 1, 0.03), Target("BBBUSDT", -1, 0.03)), EXPOSURE_NEUTRAL)
    planned, stats = plan_positions(
        decision,
        view=_view(market),
        equity=10_000.0,
        available_margin=9_500.0,
        sizing_cfg=_sizing(),
        exec_cfg=execution_config(),
        portfolio_cfg=portfolio_config(),
        fee_taker=0.0005,
        blocked=set(),
    )
    assert planned
    assert stats.beta_default == 2
    assert stats.long_notional == pytest.approx(stats.short_notional, rel=1e-9)
