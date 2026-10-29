"""预过滤测试。

规格：docs/04-特征与标签.md §6
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trade.labels import is_sample_mask, prefilter_mask


def _bars(n: int, *, close: float = 100.0, high: float = 100.5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": (np.arange(n) * 60_000).astype("int64"),
            "high": np.full(n, high),
            "low": np.full(n, 99.5),
            "close": np.full(n, close),
            "quote_volume": np.full(n, 1000.0),
            "is_filled": np.zeros(n, dtype="int64"),
        }
    )


def test_prefilter_constant_no_trigger() -> None:
    bars = _bars(80)
    mask = prefilter_mask(bars, 1.0, 2.0, 0.0)
    # 常量序列无突破、无收益、无成交量异常 → 全不触发
    assert not mask[60:].any()


def test_prefilter_break_triggers() -> None:
    bars = _bars(80)
    bars.loc[70, "high"] = 103.0
    bars.loc[70, "close"] = 103.0
    mask = prefilter_mask(bars, 1.0, 2.0, 0.0)
    assert mask[70]  # brk_60 > 0
    assert not mask[69]


def test_prefilter_volume_spike_triggers() -> None:
    bars = _bars(80)
    bars.loc[70, "quote_volume"] = 1_000_000.0
    mask = prefilter_mask(bars, 1.0, 2.0, 0.0)
    assert mask[70]  # vol_z_60 ≥ 1.0


def test_is_sample_mask_excludes_filled_and_warmup() -> None:
    bars = _bars(80)
    bars.loc[70, "high"] = 103.0
    bars.loc[70, "close"] = 103.0
    bars.loc[75, "is_filled"] = 1
    bars.loc[75, "high"] = 103.0
    bars.loc[75, "close"] = 103.0
    mask = is_sample_mask(bars, 1.0, 2.0, 0.0, warmup_bars=0)
    assert mask[70]
    assert not mask[75]  # 补齐分钟不产生样本


def test_is_sample_mask_warmup_gate() -> None:
    bars = _bars(80)
    bars.loc[20, "high"] = 103.0
    bars.loc[20, "close"] = 103.0
    mask = is_sample_mask(bars, 1.0, 2.0, 0.0, warmup_bars=50)
    assert not mask[20]  # 预热期内不产出样本
