"""三重障碍标签边界测试。

规格：docs/04-特征与标签.md §7；08 §2 P2 验收
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.labels import triple_barrier

_KW = dict(
    k_sl=1.0,
    rr=2.0,
    sl_min=0.005,
    sl_max=0.015,
    H_label=3,
    entry_latency_ms=1000,
    c_rt=0.0012,
    r_mult_clip=(-1.5, 3.0),
    fill_ratio_max=0.10,
)


def _base_bars(n: int = 20) -> pd.DataFrame:
    """TR 恒为 1 → atr_pct = 0.01（close=100）→ sl=0.01, tp=0.02, U=102, D=99。"""
    return pd.DataFrame(
        {
            "open_time": (np.arange(n) * 60_000).astype("int64"),
            "open": np.full(n, 100.0),
            "high": np.full(n, 100.5),
            "low": np.full(n, 99.5),
            "close": np.full(n, 100.0),
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": np.full(n, 100.0),
            "hi_post_1s": np.full(n, 100.5),
            "lo_post_1s": np.full(n, 99.5),
        }
    )


def _run(bars: pd.DataFrame, t: int, **overrides: object) -> tuple[pd.DataFrame, np.ndarray]:
    kw = {**_KW, **overrides}
    return triple_barrier(bars, np.array([t], dtype="int64"), **kw)


def test_entry_and_take_profit() -> None:
    bars = _base_bars()
    bars.loc[15, "high"] = 103.0  # 入场 j=14 后，j=15 触及上障碍 102
    df, kept = _run(bars, t=13)
    assert kept.tolist() == [13]
    row = df.iloc[0]
    assert row["outcome"] == "TP"
    assert row["y"] == 1
    assert row["bars_held"] == 2
    assert row["ret_gross"] == pytest.approx(0.02)


def test_stop_loss() -> None:
    bars = _base_bars()
    bars.loc[15, "low"] = 98.0  # 触及下障碍 99
    df, _ = _run(bars, t=13)
    row = df.iloc[0]
    assert row["outcome"] == "SL"
    assert row["y"] == 0
    assert row["ret_gross"] == pytest.approx(-0.01)


def test_gap_stop_loss_uses_open_price() -> None:
    bars = _base_bars()
    bars.loc[15, "open"] = 98.0  # 跳空低开 < D=99
    bars.loc[15, "low"] = 98.0
    df, _ = _run(bars, t=13)
    row = df.iloc[0]
    assert row["outcome"] == "SL"
    assert row["ret_gross"] == pytest.approx(98.0 / 100.0 - 1.0)


def test_same_bar_stop_loss_and_take_profit_counts_stop() -> None:
    bars = _base_bars()
    # 入场 K 线 j=14 同时触及上障碍与下障碍：保守按止损
    bars.loc[14, "hi_post_1s"] = 103.0
    bars.loc[14, "lo_post_1s"] = 98.0
    df, _ = _run(bars, t=13)
    row = df.iloc[0]
    assert row["outcome"] == "SL"
    assert row["ret_gross"] == pytest.approx(-0.01)


def test_timeout() -> None:
    bars = _base_bars()
    df, _ = _run(bars, t=13)
    row = df.iloc[0]
    assert row["outcome"] == "TO"
    assert row["y"] == 0
    assert row["bars_held"] == 3
    assert row["ret_gross"] == pytest.approx(0.0)


def test_entry_bar_filled_drops_sample() -> None:
    bars = _base_bars()
    bars.loc[14, "is_filled"] = 1  # 入场 K 线为补齐分钟
    df, kept = _run(bars, t=13)
    assert len(df) == 0


def test_fill_ratio_over_threshold_drops_sample() -> None:
    bars = _base_bars(n=30)
    bars.loc[14, "is_filled"] = 0
    # H_label=10：窗口内 2 根补齐 → 2/10 > 0.1 → 丢弃
    bars.loc[16, "is_filled"] = 1
    bars.loc[17, "is_filled"] = 1
    df, _ = _run(bars, t=13, H_label=10)
    assert len(df) == 0


def test_data_end_drops_sample() -> None:
    bars = _base_bars(n=16)  # t=13, H_label=3 → 13+3=16 >= 16 → 丢弃
    df, _ = _run(bars, t=13)
    assert len(df) == 0


def test_numba_matches_python_reference() -> None:
    rng = np.random.default_rng(0)
    n = 200
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    bars = pd.DataFrame(
        {
            "open_time": (np.arange(n) * 60_000).astype("int64"),
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
            "close": close,
            "is_filled": np.zeros(n, dtype="int64"),
            "p_1s": close,
            "hi_post_1s": close * (1 + np.abs(rng.normal(0, 0.001, n))),
            "lo_post_1s": close * (1 - np.abs(rng.normal(0, 0.001, n))),
        }
    )
    sample_idx = np.arange(13, n - 10, dtype="int64")
    df_nb, kept_nb = triple_barrier(bars, sample_idx, use_numba=True, **_KW)
    df_py, kept_py = triple_barrier(bars, sample_idx, use_numba=False, **_KW)
    np.testing.assert_array_equal(kept_nb, kept_py)
    pd.testing.assert_frame_equal(df_nb.reset_index(drop=True), df_py.reset_index(drop=True))
