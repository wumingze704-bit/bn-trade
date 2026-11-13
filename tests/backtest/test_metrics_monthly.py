"""月收益统计与 G2-U 闸门测试（08 §3、06 §6）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.backtest.metrics import monthly_return_stats, monthly_returns


def _curve(points: list[tuple[int, float]]) -> pd.DataFrame:
    """构造盯市权益曲线（ts_ms, equity, drawdown）。"""
    frame = pd.DataFrame({"ts_ms": [p[0] for p in points], "equity": [p[1] for p in points]})
    frame["drawdown"] = 0.0
    return frame


_JAN = 1_735_689_600_000  # 2025-01-01T00:00Z
_FEB = 1_735_689_600_000 + 31 * 86_400_000


def test_月收益按自然月首尾权益计算() -> None:
    curve = _curve(
        [
            (_JAN, 100.0),
            (_JAN + 86_400_000, 105.0),
            (_FEB, 105.0),
            (_FEB + 86_400_000, 99.0),
        ]
    )
    returns = monthly_returns(curve, 100.0)
    assert returns["2025-01"] == pytest.approx(0.05)
    assert returns["2025-02"] == pytest.approx(99.0 / 105.0 - 1.0)
    stats = monthly_return_stats(curve, 100.0)
    assert stats["months"] == 2.0
    assert stats["losing_months"] == 1.0
    assert stats["worst_monthly_return"] == pytest.approx(99.0 / 105.0 - 1.0)
    assert stats["median_monthly_return"] == pytest.approx(
        float(np.median([0.05, 99.0 / 105.0 - 1.0]))
    )


def test_空曲线返回nan且不报错() -> None:
    stats = monthly_return_stats(pd.DataFrame(), 100.0)
    assert stats["months"] == 0.0
    assert np.isnan(stats["median_monthly_return"])
