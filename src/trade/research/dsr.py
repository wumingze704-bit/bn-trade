"""研究框架的 Deflated Sharpe（docs/13 §7、1 号第五部分 B 第 15 条 l 款）。

与 `trade.backtest.sanity.deflated_sharpe` 公式相同，唯一差别是基准取
SR0 = max(0, trial_sharpes 均值) + trial_sharpes 标准差 × E[max_N]。
放在独立模块里，供引擎（RunResult.deflated）与指标层共用，避免循环导入。
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np

from trade.backtest.sanity import expected_max_sharpe_ratio


def deflated_sharpe_research(
    sharpe: float,
    trial_sharpes: list[float],
    *,
    skew: float,
    kurtosis: float,
    n_obs: int,
) -> float:
    """Deflated Sharpe 概率（研究框架口径）。

    Args:
        sharpe: 当前拼接曲线的日频非年化夏普。
        trial_sharpes: 试验夏普（同为日频非年化）。
        skew: 日收益偏度。
        kurtosis: 日收益峰度（正态为 3）。
        n_obs: 拼接曲线日数。

    Returns:
        DSR 概率（0~1）；样本或试验不足、方差项非正时返回 0.0。
    """
    if n_obs < 3 or not trial_sharpes:
        return 0.0
    variance_factor = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if variance_factor <= 0.0:
        return 0.0
    array = np.asarray(trial_sharpes, dtype="float64")
    mean = float(array.mean())
    std = float(array.std(ddof=1)) if array.size > 1 else 0.0
    # 试验均值为负时不得拉低基准（1 号 B 第 15 条 l 款）
    sr0 = expected_max_sharpe_ratio(max(0.0, mean), std, int(array.size))
    z_score = (sharpe - sr0) * np.sqrt(n_obs - 1) / np.sqrt(variance_factor)
    return float(NormalDist().cdf(float(z_score)))
