"""基线模型 M0（LightGBM 表格化特征）测试。

规格：docs/05-模型与训练.md §2
"""

from __future__ import annotations

import numpy as np

from trade.models.lightgbm import tabular_features_matrix, train_lgbm


def test_tabular_features_matrix() -> None:
    """表格化特征：最后根 + t-1/t-5/t-15 差分，lag 越界差分 0。"""
    x = np.arange(20, dtype="float16").reshape(10, 2)
    row_idxs = np.array([5, 9], dtype="int64")
    m = tabular_features_matrix(x, row_idxs)
    assert m.shape == (2, 8)
    assert m.dtype == np.float32
    # 样本 t=5：X[5]=[10,11]，差分 X[5]-X[4]=[2,2]，X[5]-X[0]=[10,10]，X[5]-X[-5] 越界=0
    assert m[0, 0] == 10 and m[0, 1] == 11
    assert m[0, 2] == 2 and m[0, 3] == 2
    assert m[0, 4] == 10 and m[0, 5] == 10
    assert m[0, 6] == 0 and m[0, 7] == 0
    # 样本 t=9：X[9]=[18,19]，X[9]-X[8]=[2,2]，X[9]-X[4]=[10,10]，X[9]-X[-6] 越界=0
    assert m[1, 0] == 18 and m[1, 1] == 19
    assert m[1, 4] == 10 and m[1, 5] == 10


def test_train_lgbm_predicts() -> None:
    """小数据上训练 + 预测，输出概率形状正确。"""
    rng = np.random.default_rng(0)
    x_train = rng.normal(size=(300, 8)).astype("float32")
    y_train = (rng.random(300) > 0.5).astype("int64")
    w_train = np.ones(300)
    x_val = rng.normal(size=(60, 8)).astype("float32")
    y_val = (rng.random(60) > 0.5).astype("int64")
    w_val = np.ones(60)
    booster = train_lgbm(x_train, y_train, w_train, x_val, y_val, w_val, num_boost_round=30)
    p = booster.predict(x_val, num_iteration=booster.best_iteration)
    assert p.shape == (60,)
    assert np.all((p >= 0) & (p <= 1))
