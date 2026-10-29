"""S9 事件驱动对拍测试（06 §3.4、§7）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule, run_backtest
from trade.backtest.event_sim import event_sim
from trade.backtest.sanity import s9_compare
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=10000,
        mode="fixed_fraction",
        position_frac=0.2,
        leverage=5,
        max_positions=3,
        base="initial",
        risk_per_trade=0.005,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def _fixture(seed: int):
    rng = np.random.default_rng(seed)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    signals_frames = []
    for sym in ("A", "B"):
        n = 300
        open_time = np.arange(n) * _MIN + _T0
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
        open_ = np.concatenate([[100.0], close[:-1]])
        high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.001, n))
        low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.001, n))
        bars_by_symbol[sym] = pd.DataFrame(
            {
                "open_time": open_time,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "quote_volume": np.full(n, 5e6),
                "is_filled": np.zeros(n, dtype="int64"),
                "p_1s": close,
                "hi_post_1s": high,
                "lo_post_1s": low,
            }
        )
        # 每 5 根一个信号，避免持仓重叠过多
        mask = (np.arange(n) % 5) == 0
        signals_frames.append(
            pd.DataFrame(
                {
                    "symbol": [sym] * int(mask.sum()),
                    "open_time": open_time[mask],
                    "p": np.ones(int(mask.sum())),
                    "m": np.zeros(int(mask.sum())),
                    "is_sample": np.ones(int(mask.sum()), dtype="int64"),
                }
            )
        )
    signals = pd.concat(signals_frames, ignore_index=True)
    rules = {"A": TradeRule(0.01, 0.01, 5.0), "B": TradeRule(0.01, 0.01, 5.0)}
    groups = {"A": "train", "B": "holdout"}
    return bars_by_symbol, signals, rules, groups


def test_S9事件驱动对拍():
    bars_by_symbol, signals, rules, groups = _fixture(seed=5)
    fees = FeeSchedule(0.0002, 0.0005)
    backtest_cfg = BacktestConfig(threshold=0.5)
    exit_cfg = ExitConfig(type="trail")
    sizing_cfg = _sizing()
    common = dict(
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        H_label=60,
        initial_equity=10000.0,
    )
    fast, _ = run_backtest(bars_by_symbol, signals, rules, groups, scen_name="base", **common)
    event = event_sim(bars_by_symbol, signals, rules, groups, scen_name="base", **common)
    assert s9_compare(fast, event) is True
