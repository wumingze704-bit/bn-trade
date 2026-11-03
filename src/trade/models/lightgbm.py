"""基线模型 M0：LightGBM（表格化特征）。

规格：docs/05-模型与训练.md §2
"""

from __future__ import annotations

import lightgbm as lgb  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt


def tabular_features_matrix(
    x: npt.NDArray[np.float16], row_idxs: npt.NDArray[np.int64]
) -> npt.NDArray[np.float32]:
    """按行索引构造 M0 表格化特征矩阵（05 §2）。

    每个样本 t 的特征 = 拼接 [X[t], X[t]-X[t-1], X[t]-X[t-5], X[t]-X[t-15]]，
    形状 (n_samples, 4F)。lag 越界（t-lag < 0）时差分为 0。

    Args:
        x: 单币特征矩阵 (N, F)。
        row_idxs: 样本行索引数组 (n_samples,)。

    Returns:
        (n_samples, 4F) 的 float32 特征矩阵。
    """
    row_idxs = np.asarray(row_idxs, dtype="int64")
    x = x.astype(np.float32)
    last = x[row_idxs]
    parts = [last]
    for lag in (1, 5, 15):
        valid = row_idxs - lag >= 0
        diff = np.zeros_like(last)
        diff[valid] = last[valid] - x[row_idxs[valid] - lag]
        parts.append(diff)
    return np.concatenate(parts, axis=1)


def train_lgbm(
    x_train: npt.NDArray[np.float32],
    y_train: npt.NDArray[np.int64],
    w_train: npt.NDArray[np.float64] | None,
    x_val: npt.NDArray[np.float32],
    y_val: npt.NDArray[np.int64],
    w_val: npt.NDArray[np.float64] | None,
    *,
    num_boost_round: int = 500,
    early_stopping_rounds: int = 100,
    seed: int = 42,
) -> lgb.Booster:
    """训练 LightGBM 分类器（05 §6 第 3 步，CPU）。

    Args:
        x_train/x_val: 训练/验证特征矩阵。
        y_train/y_val: 训练/验证标签（0/1）。
        w_train/w_val: 训练/验证样本权重（独特性 × 币种均衡）。
        num_boost_round: 最大树数。
        early_stopping_rounds: 验证 PR-AUC 早停轮数。
        seed: 随机种子。

    Returns:
        训练好的 Booster。
    """
    params = {
        "objective": "binary",
        "metric": "average_precision",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 200,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "verbosity": -1,
        "seed": seed,
    }
    train = lgb.Dataset(x_train, label=y_train, weight=w_train)
    valid = lgb.Dataset(x_val, label=y_val, weight=w_val, reference=train)
    booster = lgb.train(
        params,
        train,
        num_boost_round=num_boost_round,
        valid_sets=[valid],
        callbacks=[lgb.early_stopping(early_stopping_rounds), lgb.log_evaluation(0)],
    )
    return booster


def predict_proba(model: lgb.Booster, x: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
    """输出正类概率 p（05 §1）。"""
    return model.predict(x, num_iteration=model.best_iteration)
