"""特征流水线测试：批量 vs 增量一致性、未来函数测试。

规格：docs/04-特征与标签.md §2、§5；08 §2 P2 验收
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tests.features.helpers import make_bars
from trade.features import FeaturesConfig, IncrementalComputer, compute_features, enabled_features
from trade.features.pipeline import align_btc
from trade.labels import is_sample_mask


def _compare_batch_vs_incremental(bars: pd.DataFrame, btc: pd.DataFrame) -> float:
    cfg = FeaturesConfig()
    features = enabled_features(cfg)
    batch = compute_features(bars, btc, features, cfg)
    aligned = align_btc(bars["open_time"].to_numpy(dtype="int64"), btc)
    computer = IncrementalComputer(features, cfg)
    rows = [
        computer.update(bars.iloc[i].to_dict(), aligned.iloc[i].to_dict()) for i in range(len(bars))
    ]
    incremental = pd.DataFrame(rows)
    max_abs_error = 0.0
    for feature in features:
        diff = np.nanmax(
            np.abs(batch[feature.name].to_numpy() - incremental[feature.name].to_numpy())
        )
        max_abs_error = max(max_abs_error, float(diff))
    return max_abs_error


def test_batch_vs_incremental_two_coins_three_days() -> None:
    # 2 币 × 3 天（4320 分钟），批量与逐分钟增量最大绝对误差 < 1e-6（08 §2 P2）
    n = 4320
    btc = make_bars(n, seed=99)
    for seed in (1, 2):
        bars = make_bars(n, seed=seed)
        error = _compare_batch_vs_incremental(bars, btc)
        assert error < 1e-6


def test_future_function_feature_and_sample_unchanged() -> None:
    # 随机替换 t 之后的原始数据，t 及以前的特征与样本不变（08 §2 P2）
    cfg = FeaturesConfig()
    features = enabled_features(cfg)
    n = 500
    btc = make_bars(n, seed=7)
    bars = make_bars(n, seed=3)
    t = 300

    before_features = compute_features(bars, btc, features, cfg)
    before_sample = is_sample_mask(
        bars,
        cfg.prefilter.vol_z_60,
        cfg.prefilter.r1_sigma,
        cfg.prefilter.brk_60,
        cfg.warmup_bars,
    )

    mutated = bars.copy()
    rng = np.random.default_rng(123)
    value_columns = [col for col in bars.columns if col != "open_time"]
    for col in value_columns:
        if np.issubdtype(mutated[col].dtype, np.integer):
            mutated.loc[t + 1 :, col] = rng.integers(0, 1000, size=n - (t + 1))
        else:
            # 用正的随机值替换，保持价格/成交量等非负，模拟真实原始数据
            mutated.loc[t + 1 :, col] = np.abs(rng.normal(size=n - (t + 1)))

    after_features = compute_features(mutated, btc, features, cfg)
    after_sample = is_sample_mask(
        mutated,
        cfg.prefilter.vol_z_60,
        cfg.prefilter.r1_sigma,
        cfg.prefilter.brk_60,
        cfg.warmup_bars,
    )

    np.testing.assert_allclose(
        before_features.iloc[: t + 1].to_numpy(),
        after_features.iloc[: t + 1].to_numpy(),
        rtol=0.0,
        atol=0.0,
    )
    np.testing.assert_array_equal(before_sample[: t + 1], after_sample[: t + 1])
