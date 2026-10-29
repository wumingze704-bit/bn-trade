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
    ewm_step,
    sanitize,
)


def test_ewm_step_hand_calculated() -> None:
    state = EwmState(0.0, 1.0)
    z, next_state = ewm_step(state, 1.0, alpha=0.5, clip=5.0, min_var=1e-12)
    assert z == pytest.approx(1.0 / (1.0 + 1e-12))
    assert next_state.mean == pytest.approx(0.5)
    assert next_state.var == pytest.approx(1.0)


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
