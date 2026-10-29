"""样本独特性权重测试。

规格：docs/04-特征与标签.md §7.3；08 §2 P2 验收
"""

from __future__ import annotations

import numpy as np

from trade.labels import uniqueness_weights


def test_non_overlapping_weight_is_one() -> None:
    sample_idx = np.array([0, 100], dtype="int64")
    bars_held = np.array([10, 10], dtype="int64")
    weights = uniqueness_weights(sample_idx, bars_held)
    np.testing.assert_allclose(weights, [1.0, 1.0])


def test_overlapping_weight_hand_calculated() -> None:
    sample_idx = np.array([0, 1], dtype="int64")
    bars_held = np.array([5, 5], dtype="int64")
    weights = uniqueness_weights(sample_idx, bars_held)
    # 窗口 [1..5] 与 [2..6]：条 2~5 并发数 2，其余并发数 1
    expected = [(1 + 4 * 0.5) / 5, (4 * 0.5 + 1) / 5]
    np.testing.assert_allclose(weights, expected)


def test_empty_returns_empty() -> None:
    weights = uniqueness_weights(np.array([], dtype="int64"), np.array([], dtype="int64"))
    assert weights.shape == (0,)
