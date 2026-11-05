"""仓位比例敏感性扫描与 PNG 测试（T3-8）。

规格：docs/06-回测引擎.md §5.5、§8
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule
from trade.backtest.sweep import plot_sizing_sweep, sizing_sweep, write_sizing_sweep
from trade.common.config import load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.features.registry import FeaturesConfig

_ROOT = Path(__file__).resolve().parents[2]
_T0 = 1_767_225_600_000  # 2026-01-01T00:00Z
_MIN = 60_000


def _bars(n: int = 300, *, rise_after: int = 20) -> pd.DataFrame:
    close = np.full(n, 100.0)
    close[rise_after:] = np.linspace(100.0, 104.0, n - rise_after)
    high = close + 0.3
    low = close - 0.3
    open_time = _T0 + np.arange(n, dtype="int64") * _MIN
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
            "quote_volume": 100_000.0,
            "is_filled": 0,
            "p_1s": close,
            "hi_post_1s": high,
            "lo_post_1s": low,
            "p_3s": close,
            "hi_post_3s": high,
            "lo_post_3s": low,
        }
    )


def _configs() -> tuple[BacktestConfig, ExitConfig, SizingConfig, FeaturesConfig, FeeSchedule]:
    return (
        load_config(BacktestConfig, _ROOT / "configs" / "backtest.yaml"),
        load_config(ExitConfig, _ROOT / "configs" / "exits.yaml"),
        load_config(SizingConfig, _ROOT / "configs" / "sizing.yaml"),
        load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml"),
        FeeSchedule(maker=0.0002, taker=0.0005),
    )


def test_sizing_sweep_rows_and_files(tmp_path: Path) -> None:
    bars = {"C01": _bars()}
    signals = pd.DataFrame(
        {
            "symbol": ["C01"],
            "open_time": [_T0 + 20 * _MIN],
            "p": [0.9],
            "m": [0.5],
            "is_sample": [1],
        }
    )
    backtest_cfg, exit_cfg, sizing_cfg, features_cfg, fees = _configs()
    frame = sizing_sweep(
        bars,
        signals,
        {"C01": TradeRule(tick_size=0.01, step_size=0.01, min_notional=5.0)},
        {"C01": "train"},
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        features_cfg=features_cfg,
        fractions=[0.1, 0.2],
        initial_equity=10.0,
    )
    assert list(frame["position_frac"]) == [0.1, 0.2]
    assert list(frame["max_positions"]) == [3, 3]
    assert len(frame) == 2
    assert frame["final_equity"].notna().all()

    write_sizing_sweep(tmp_path, frame)
    plot_sizing_sweep(frame, tmp_path / "sizing_sweep.png")
    assert (tmp_path / "sizing_sweep.md").is_file()
    assert (tmp_path / "sizing_sweep.parquet").is_file()
    assert (tmp_path / "sizing_sweep.png").is_file()
    assert (tmp_path / "sizing_sweep.png").stat().st_size > 0


def test_sizing_sweep_max_positions_cap() -> None:
    """position_frac=0.3 → max_positions = min(3, floor(0.95/0.3)) = 3。"""
    bars = {"C01": _bars()}
    signals = pd.DataFrame(
        {
            "symbol": ["C01"],
            "open_time": [_T0 + 20 * _MIN],
            "p": [0.9],
            "m": [0.0],
            "is_sample": [1],
        }
    )
    backtest_cfg, exit_cfg, sizing_cfg, features_cfg, fees = _configs()
    frame = sizing_sweep(
        bars,
        signals,
        {"C01": TradeRule(tick_size=0.01, step_size=0.01, min_notional=5.0)},
        {"C01": "train"},
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        features_cfg=features_cfg,
        fractions=[0.3],
        initial_equity=10.0,
    )
    assert int(frame.loc[0, "max_positions"]) == 3
