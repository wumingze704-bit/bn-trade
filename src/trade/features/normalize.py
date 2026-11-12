"""特征归一化：EWM 指数加权标准化与裁剪。

规格：docs/04-特征与标签.md §5
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from trade.common._numba import njit

EPS = 1e-12

# `ewm_step` 的 `(x - μ) ** 2` 由 CPython 调用 libm pow 计算；LLVM 会把常量指数 2.0
# 折叠成 x * x，少数取值差 1 ulp。numba 内核因此把指数作为运行时参数传入（红线 15：
# 批量结果必须与逐点递推逐位一致）。
_SQUARE_POWER = 2.0


@dataclass(frozen=True)
class EwmState:
    """EWM 递推状态（μ 与 σ²）。

    NOTE(设计决策)：初始状态取 μ=0、σ²=1，为中性初始值。预热期内统计量收敛，
    不产出样本（04 §5），初始值对产出样本的影响可忽略（30 天预热 ≈ 10 个半衰期）。
    """

    mean: float
    var: float


def alpha_from_halflife(halflife_bars: int) -> float:
    """由半衰期换算 EWM 平滑系数 α = 1 - 0.5^(1/halflife_bars)（04 §5）。"""
    return 1.0 - 0.5 ** (1.0 / halflife_bars)


def ewm_step(
    state: EwmState, x: float, alpha: float, clip: float, min_var: float
) -> tuple[float, EwmState]:
    """单步 EWM：用 t-1 时刻统计量标准化 x_t，再更新统计量。

    规格：docs/04-特征与标签.md §5
    """
    z = (x - state.mean) / (math.sqrt(state.var) + min_var)
    z = min(max(z, -clip), clip)
    new_mean = (1.0 - alpha) * state.mean + alpha * x
    new_var = (1.0 - alpha) * state.var + alpha * (x - state.mean) ** 2
    return z, EwmState(new_mean, new_var)


@njit
def _ewm_standardize_kernel(
    x: np.ndarray, alpha: float, clip: float, min_var: float, square_power: float
) -> np.ndarray:
    """EWM 标准化的 numba 批量内核（04 §5）。

    算术顺序与 `ewm_step` 逐点相同：先用旧统计量标准化并裁剪，再更新 mean 与 var；
    不开 fastmath。`square_power` 由调用方传入 2.0，使 libm pow 不被折叠成乘法。
    """
    out = np.empty(x.shape[0], dtype=np.float64)
    mean = 0.0
    var = 1.0
    for i in range(x.shape[0]):
        z = (x[i] - mean) / (np.sqrt(var) + min_var)
        if z > clip:
            z = clip
        elif z < -clip:
            z = -clip
        out[i] = z
        new_mean = (1.0 - alpha) * mean + alpha * x[i]
        var = (1.0 - alpha) * var + alpha * math.pow(x[i] - mean, square_power)
        mean = new_mean
    return out


def ewm_standardize(x: np.ndarray, halflife_bars: int, clip: float, min_var: float) -> np.ndarray:
    """对整列做 EWM 标准化（numba 批量实现，逐点递推，天然无未来信息）。

    Args:
        x: 原始特征序列（NaN/Inf 应由 `sanitize` 先置 0）。
        halflife_bars: EWM 半衰期（根数）。
        clip: z 值裁剪上限（对称 [-clip, clip]）。
        min_var: 防除零 ε。

    Returns:
        与输入等长的 z 序列。
    """
    alpha = alpha_from_halflife(halflife_bars)
    arr = np.asarray(x, dtype="float64")
    return _ewm_standardize_kernel(arr, alpha, clip, min_var, _SQUARE_POWER)


def ewm_standardize_py(
    x: np.ndarray, halflife_bars: int, clip: float, min_var: float
) -> np.ndarray:
    """EWM 标准化的纯 Python 参考实现（04 §5）。

    与 `ewm_standardize` 的 numba 内核逐元素一致，供一致性测试与排查使用。
    """
    alpha = alpha_from_halflife(halflife_bars)
    arr = np.asarray(x, dtype="float64")
    out = np.empty(arr.shape[0], dtype="float64")
    state = EwmState(0.0, 1.0)
    for i in range(arr.shape[0]):
        z, state = ewm_step(state, float(arr[i]), alpha, clip, min_var)
        out[i] = z
    return out


def sanitize(x: np.ndarray) -> np.ndarray:
    """把 NaN/Inf 替换为 0（用于特征头部 lookback 不足与缺失数据的数值化）。"""
    arr = np.asarray(x, dtype="float64")
    return np.where(np.isfinite(arr), arr, 0.0)
