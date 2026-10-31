"""出场模拟标签 exit_sim 测试（T0.19，04 §7.5）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tests.features.helpers import make_bars
from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    CostConfig,
    RiskConfig,
    TradeRule,
    precompute_trades,
)
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.labels.exit_sim import exit_sim

_EXIT_CFG = ExitConfig(type="trail")
_TAKER = 0.0005
_MAKER = 0.0002


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=10,
        mode="fixed_fraction",
        position_frac=0.2,
        leverage=5,
        max_positions=3,
        base="equity",
        risk_per_trade=0.005,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def _kwargs() -> dict:
    return dict(
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        entry_latency_ms=1000,
        y_min_r=0.25,
        exit_cfg=_EXIT_CFG,
        fees_taker=_TAKER,
        fees_maker=_MAKER,
        half_spread_min=0.0001,
        slip_min=0.0001,
        Y=1.0,
        notional_ref=10.0,
        max_chase=0.003,
        tick_size=0.01,
    )


def test_exit_sim与回测逐笔一致() -> None:
    """标签复用回测同一入场价、成本与 simulate_exit，逐笔一致（04 §7.5）。"""
    n = 300
    t = 100
    bars = make_bars(n, seed=1)
    for i in (120, 150, 180):
        bars.loc[i, "close"] *= 1.05
        bars.loc[i, "high"] *= 1.05
        bars.loc[i, "p_1s"] = bars.loc[i, "close"]
        bars.loc[i, "hi_post_1s"] = bars.loc[i, "close"]

    # 回测预计算
    signals = pd.DataFrame(
        {"open_time": [bars.loc[t, "open_time"]], "p": [1.0], "m": [0.0], "is_sample": [1]}
    )
    trades, _ = precompute_trades(
        bars,
        signals,
        symbol="A",
        group="train",
        rule=TradeRule(tick_size=0.01, step_size=0.01, min_notional=5.0),
        scen=scenario("base"),
        fees=FeeSchedule(maker=_MAKER, taker=_TAKER),
        backtest_cfg=BacktestConfig(costs=CostConfig(), risk=RiskConfig(max_chase=0.003)),
        exit_cfg=_EXIT_CFG,
        sizing_cfg=_sizing(),
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
    )
    assert len(trades) == 1
    tr = trades[0]

    # 标签
    frame, kept = exit_sim(bars, np.array([t]), **_kwargs())
    assert kept.tolist() == [t]
    row = frame.iloc[0]

    assert row["sl"] == pytest.approx(tr.sl)
    assert row["bars_held"] == tr.bars_held
    assert row["t_exit"] == tr.exit_open_time
    assert row["exit_type"] == tr.exit_type
    # ret_net = gross_return - 开仓费率 - 出场费率 - 资金费率（本例无 funding）
    expected_ret_net = tr.gross_return - _TAKER - tr.exit_fee_rate - tr.funding_rate_sum
    assert row["ret_net"] == pytest.approx(expected_ret_net)


def test_y_min_r标签边界() -> None:
    """y = 1 当且仅当 r_net ≥ y_min_r（默认 0.25，04 §7.5）。"""
    n = 300
    bars = make_bars(n, seed=3)
    for i in (100, 140, 180):
        bars.loc[i, "close"] *= 1.03
        bars.loc[i, "high"] *= 1.03
        bars.loc[i, "p_1s"] = bars.loc[i, "close"]
        bars.loc[i, "hi_post_1s"] = bars.loc[i, "close"]
    sample_idx = np.array([60, 100, 140, 180])
    frame, _ = exit_sim(bars, sample_idx, **_kwargs())
    r_net = (frame["ret_net"] / frame["sl"]).to_numpy(dtype="float64")
    assert (frame["y"].to_numpy() == (r_net >= 0.25).astype("int64")).all()
    # 至少有一个正样本和一个负样本，保证测试覆盖了边界两侧
    assert frame["y"].nunique() == 2


def test_入场价与止损无未来函数() -> None:
    """sl 与 p_fill 只用 ≤ t+1 的数据，修改未来（t+2 之后）不影响（04 §2 第 7 条）。"""
    n = 300
    t = 100
    bars = make_bars(n, seed=5)
    before, _ = exit_sim(bars, np.array([t]), **_kwargs())

    mutated = bars.copy()
    mutated.loc[t + 2 :, ["open", "high", "low", "close"]] *= 1.5
    after, _ = exit_sim(mutated, np.array([t]), **_kwargs())

    # 止损与入场价不变（出场结果允许不同，因未来行情已变）
    assert before.iloc[0]["sl"] == pytest.approx(after.iloc[0]["sl"])
