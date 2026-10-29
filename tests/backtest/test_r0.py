"""R0 规则策略信号测试（05 §2）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from trade.backtest.r0 import R0Config, r0_signals_for_symbol
from trade.features.kline import brk_60, tbr_5, vol_z_60
from trade.labels.prefilter import prefilter_mask

_T0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
_MIN = 60_000


def _bars() -> pd.DataFrame:
    n = 120
    open_time = np.arange(n) * _MIN + _T0
    close = np.full(n, 100.0)
    high = np.full(n, 100.0)
    low = np.full(n, 99.0)
    qv = np.full(n, 1000.0)
    tbq = np.full(n, 300.0)  # tbr = 0.3
    # 第 100 根：突破 + 放量 + 主动买占比高 → 应触发 R0
    close[100] = 110.0
    high[100] = 110.0
    low[100] = 109.0
    qv[100] = 1e5
    tbq[96:100] = 800.0
    tbq[100] = 6e4  # tbr_5 ≈ 63200/104000 ≈ 0.61 ≥ 0.6
    return pd.DataFrame(
        {
            "open_time": open_time,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": qv,
            "taker_buy_quote_volume": tbq,
            "is_filled": np.zeros(n, dtype="int64"),
        }
    )


def test_r0信号可手工复核():
    bars = _bars()
    out = r0_signals_for_symbol(
        bars,
        R0Config(),
        vol_z_60_thr=1.0,
        r1_sigma=2.0,
        brk_60_thr=0.0,
        warmup_bars=60,
        symbol="A",
    )
    by_time = {t: p for t, p in zip(out["open_time"], out["p"], strict=True)}
    # 第 100 根应触发 R0（p=1）
    assert by_time[_T0 + 100 * _MIN] == 1.0
    # 只有第 100 根触发；其余样本根（brk_60=0 或 tbr_5/vol_z 不足）p=0
    assert int((out["p"] == 1.0).sum()) == 1


def test_r0与手算规则一致():
    rng = np.random.default_rng(11)
    n = 400
    open_time = np.arange(n) * _MIN + _T0
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    high = np.maximum(close, close * 1.001)
    low = np.minimum(close, close * 0.999)
    qv = rng.lognormal(10, 1, n)
    tbq = qv * rng.uniform(0.2, 0.8, n)
    bars = pd.DataFrame(
        {
            "open_time": open_time,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": qv,
            "taker_buy_quote_volume": tbq,
            "is_filled": np.zeros(n, dtype="int64"),
        }
    )
    out = r0_signals_for_symbol(
        bars,
        R0Config(),
        vol_z_60_thr=1.0,
        r1_sigma=2.0,
        brk_60_thr=0.0,
        warmup_bars=60,
        symbol="A",
    )
    pre = prefilter_mask(bars, 1.0, 2.0, 0.0)
    warmed = np.arange(n) >= 60
    rule = (brk_60(high, close) > 0) & (tbr_5(tbq, qv) >= 0.6) & (vol_z_60(qv) >= 2.0)
    expected = pre & warmed & rule
    # 取输出中 open_time 对应的预期 p
    out_p = dict(zip(out["open_time"], out["p"], strict=True))
    for i in np.nonzero(pre & warmed)[0]:
        assert out_p[open_time[i]] == (1.0 if expected[i] else 0.0)
