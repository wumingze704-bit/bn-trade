"""EWM 标准化手算与无未来信息测试。

规格：docs/04-特征与标签.md §5；08 §2 P2 验收
"""

from __future__ import annotations

import numpy as np
import pytest

from trade.features.normalize import (
    EwmState,
    alpha_from_halflife,
    ewm_standardize,
    ewm_standardize_py,
    ewm_step,
    sanitize,
)


def test_ewm_step_hand_calculated() -> None:
    """偏差修正后：第 1 个值没有历史（W=0）输出 0；第 2 个值用 μ/W、σ²/W 标准化。"""
    state = EwmState(0.0, 1.0)
    z, next_state = ewm_step(state, 1.0, alpha=0.5, clip=5.0, min_var=1e-12)
    assert z == 0.0
    assert next_state.mean == pytest.approx(0.5)
    assert next_state.var == pytest.approx(1.0)
    assert next_state.weight == pytest.approx(0.5)
    assert next_state.decay == pytest.approx(0.5)

    # μ/W = 0.5/0.5 = 1.0；(σ² − 先验权重)/W = (1.0 − 0.5)/0.5 = 1.0 → z = (2 − 1)/1 = 1
    z2, state2 = ewm_step(next_state, 2.0, alpha=0.5, clip=5.0, min_var=1e-12)
    assert z2 == pytest.approx(1.0, rel=1e-9)
    assert state2.mean == pytest.approx(1.25)
    assert state2.var == pytest.approx(1.625)
    assert state2.weight == pytest.approx(0.75)


def test_alpha_from_halflife() -> None:
    assert alpha_from_halflife(4320) == pytest.approx(1.0 - 0.5 ** (1.0 / 4320.0))


def test_ewm_standardize_no_future_information() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    z1 = ewm_standardize(x, halflife_bars=4320, clip=5.0, min_var=1e-12)
    x2 = x.copy()
    rng = np.random.default_rng(0)
    x2[3:] = rng.normal(size=2)
    z2 = ewm_standardize(x2, halflife_bars=4320, clip=5.0, min_var=1e-12)
    # 修改 t+1 之后的数据，不影响 t 及之前（前 3 个点）
    np.testing.assert_allclose(z1[:3], z2[:3], rtol=0.0, atol=0.0)


def test_sanitize_replaces_nonfinite() -> None:
    out = sanitize(np.array([1.0, np.nan, np.inf, -np.inf, 2.0]))
    assert out.tolist() == [1.0, 0.0, 0.0, 0.0, 2.0]


def test_ewm_numba与python参考实现逐元素完全相等() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=200_000) * 3.0
    x[50:120] = 1.25  # 常数段
    x[200:260] = np.nan  # 缺失段（sanitize 后为 0）
    x[300] = np.inf
    x[1000:2000] *= 100.0  # 触发裁剪
    cleaned = sanitize(x)
    got = ewm_standardize(cleaned, halflife_bars=4320, clip=5.0, min_var=1e-12)
    want = ewm_standardize_py(cleaned, halflife_bars=4320, clip=5.0, min_var=1e-12)
    np.testing.assert_array_equal(got, want)
