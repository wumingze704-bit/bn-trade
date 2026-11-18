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
    """EWM 递推状态（μ、σ² 与累计权重和 W）。

    累计权重和 `weight = Σ α(1−α)^i = 1 − (1−α)^n`（n 为已更新次数）用于偏差修正：
    递推得到的 μ、σ² 是 `W × 真值` 的未归一化估计，段首 W 很小，直接标准化会把
    小量纲特征的 z 值压到 0 附近（04 §5）。标准化时用 `μ/W`、`σ²/W`。
    """

    mean: float
    var: float
    weight: float = 0.0
    # (1−α)^n：初始先验（μ=0、σ²=1）在累计统计里所占权重，标准化时扣除
    decay: float = 1.0


def alpha_from_halflife(halflife_bars: int) -> float:
    """由半衰期换算 EWM 平滑系数 α = 1 - 0.5^(1/halflife_bars)（04 §5）。"""
    return 1.0 - 0.5 ** (1.0 / halflife_bars)


def ewm_step(
    state: EwmState, x: float, alpha: float, clip: float, min_var: float
) -> tuple[float, EwmState]:
    """单步 EWM：用 t-1 时刻的偏差修正统计量标准化 x_t，再更新统计量。

    规格：docs/04-特征与标签.md §5
    """
    z = _standardize(x, state, min_var, clip)
    new_mean = (1.0 - alpha) * state.mean + alpha * x
    new_var = (1.0 - alpha) * state.var + alpha * (x - state.mean) ** 2
    # W' = α + (1−α)·W，与 1 − (1−α)^n 逐位等价但不引入幂运算
    new_weight = alpha + (1.0 - alpha) * state.weight
    return z, EwmState(new_mean, new_var, new_weight, (1.0 - alpha) * state.decay)


def _standardize(x: float, state: EwmState, min_var: float, clip: float) -> float:
    """用偏差修正后的均值与方差标准化并列裁剪（04 §5）。

    递推得到的 μ、σ² 含初始先验（μ=0、σ²=1）的贡献：其权重为 `decay = (1−α)^n`，
    扣除后再除以累计权重 `W = 1 − (1−α)^n`。累计权重为 0（还没有任何历史）时输出 0。
    """
    if state.weight <= 0.0:
        return 0.0
    mean = state.mean / state.weight
    var = (state.var - state.decay) / state.weight
    if var < 0.0:
        var = 0.0
    z = (x - mean) / (math.sqrt(var) + min_var)
    return min(max(z, -clip), clip)


@njit
def _ewm_standardize_kernel(
    x: np.ndarray, alpha: float, clip: float, min_var: float, square_power: float
) -> np.ndarray:
    """EWM 标准化的 numba 批量内核（04 §5）。

    算术顺序与 `ewm_step` 逐点相同：先用旧统计量标准化并裁剪，再更新 mean、var 与
    累计权重；不开 fastmath。`square_power` 由调用方传入 2.0，使 libm pow 不被折叠成乘法。
    """
    out = np.empty(x.shape[0], dtype=np.float64)
    mean = 0.0
    var = 1.0
    weight = 0.0
    decay = 1.0
    for i in range(x.shape[0]):
        if weight > 0.0:
            center = mean / weight
            variance = (var - decay) / weight
            if variance < 0.0:
                variance = 0.0
            z = (x[i] - center) / (np.sqrt(variance) + min_var)
            if z > clip:
                z = clip
            elif z < -clip:
                z = -clip
        else:
            z = 0.0
        out[i] = z
        new_mean = (1.0 - alpha) * mean + alpha * x[i]
        var = (1.0 - alpha) * var + alpha * math.pow(x[i] - mean, square_power)
        mean = new_mean
        weight = alpha + (1.0 - alpha) * weight
        decay = (1.0 - alpha) * decay
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
