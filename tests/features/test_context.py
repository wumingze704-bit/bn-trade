"""市场环境类特征手算样例测试（#28~#33）。

规格：docs/04-特征与标签.md §4.3
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from trade.features import context


def test_btc_r_1() -> None:
    close = np.array([100.0, 102.0])
    out = context.btc_r_1(close)
    assert out[1] == pytest.approx(math.log(102.0 / 100.0))


def test_btc_r_15() -> None:
    close = np.arange(1, 17, dtype="float64") * 10.0
    assert context.btc_r_15(close)[15] == pytest.approx(math.log(160.0 / 10.0))


def test_btc_rv_60() -> None:
    close = np.arange(1, 62, dtype="float64") * 10.0
    r = np.log(close[1:] / close[:-1])
    assert context.btc_rv_60(close)[60] == pytest.approx(float(r.std(ddof=0)))


def test_ex_r_15_uncorrelated_beta_zero() -> None:
    # 本币与 BTC 完全无关（本币 r 恒 0）时，cov=0 → β=0 → ex_r_15 = r_15
    n = 1441  # cov_1440 需 1440 个收益 → 1441 根 close
    close = np.ones(n, dtype="float64") * 100.0
    btc = np.linspace(100.0, 120.0, n)
    out = context.ex_r_15(close, btc)
    # 本币 r_15 = ln(1)=0，β=0，ex_r_15=0
    assert out[-1] == pytest.approx(0.0, abs=1e-12)


def test_hour_sin_cos() -> None:
    # 2026-01-01 00:00 UTC 的 open_time：hour = 0
    open_time = np.array([1_767_225_600_000], dtype="int64")
    assert context.hour_sin(open_time)[0] == pytest.approx(0.0, abs=1e-12)
    assert context.hour_cos(open_time)[0] == pytest.approx(1.0, abs=1e-12)
    # 06:00 UTC：hour=6 → sin(2π·6/24)=sin(π/2)=1，cos=0
    open_time = np.array([1_767_225_600_000 + 6 * 3_600_000], dtype="int64")
    assert context.hour_sin(open_time)[0] == pytest.approx(1.0, abs=1e-12)
    assert context.hour_cos(open_time)[0] == pytest.approx(0.0, abs=1e-12)
