"""数据段特征隔离测试（T0.15，03 §6）。

修改段 A 的数据，段 B 的特征不变：滚动窗口与 EWM 状态不得跨越段间空档。
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from tests.features.helpers import make_bars
from trade.data.loader import segment_ids
from trade.features import FeaturesConfig, compute_features, enabled_features


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def test_修改段A数据段B特征不变() -> None:
    base_ms = _ms(2026, 1, 1)
    bars_a = make_bars(120, seed=1, base_ms=base_ms)
    bars_b = make_bars(120, seed=2, base_ms=base_ms + 5 * 86_400_000)  # 5 天后 → 段界
    bars = pd.concat([bars_a, bars_b], ignore_index=True)
    btc = make_bars(240, seed=99, base_ms=base_ms)

    features = enabled_features(FeaturesConfig())
    cfg = FeaturesConfig()
    segments = segment_ids(bars["open_time"].to_numpy(dtype="int64"))
    assert segments[0] == 0 and segments[119] == 0
    assert segments[120] == 1 and segments[239] == 1

    original = compute_features(bars, btc, features, cfg, segments)
    seg_b_idx = np.nonzero(segments == 1)[0]

    # 修改段 A 的 K 线（close/high/low/open），段 B 保持不变
    mutated = bars.copy()
    mutated.loc[:119, ["open", "high", "low", "close"]] *= 2.0
    recomputed = compute_features(mutated, btc, features, cfg, segments)

    feature_cols = [f.name for f in features]
    np.testing.assert_allclose(
        recomputed.loc[seg_b_idx, feature_cols].to_numpy(dtype="float64"),
        original.loc[seg_b_idx, feature_cols].to_numpy(dtype="float64"),
        rtol=1e-6,
        atol=1e-9,
    )
    # 段 A 的特征应发生变化（说明确实在按段独立计算）
    seg_a_idx = np.nonzero(segments == 0)[0]
    assert not np.allclose(
        recomputed.loc[seg_a_idx, feature_cols].to_numpy(dtype="float64"),
        original.loc[seg_a_idx, feature_cols].to_numpy(dtype="float64"),
    )
