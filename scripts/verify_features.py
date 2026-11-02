"""T2-8 真实数据特征一致性 + 未来函数验证（G1 要求）。

在真实 bars（调参期）上复跑 08 §2 P2 的一致性测试（批量 vs 增量 < 1e-6）与未来函数测试。
只用调参期数据，不碰测试段/封存集（红线 2、3）。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from trade.common.config import BaseConfig, load_config  # noqa: E402
from trade.data.loader import load_bars, window_bounds  # noqa: E402
from trade.features import (  # noqa: E402
    FeaturesConfig,
    IncrementalComputer,
    compute_features,
    enabled_features,
)
from trade.features.pipeline import align_btc  # noqa: E402
from trade.labels import is_sample_mask  # noqa: E402


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def compare_batch_vs_incremental(bars: pd.DataFrame, btc: pd.DataFrame) -> float:
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


def future_function_ok(bars: pd.DataFrame, btc: pd.DataFrame, t: int) -> bool:
    cfg = FeaturesConfig()
    features = enabled_features(cfg)
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
            mutated.loc[t + 1 :, col] = rng.integers(0, 1000, size=len(bars) - (t + 1))
        else:
            mutated.loc[t + 1 :, col] = np.abs(rng.normal(size=len(bars) - (t + 1)))

    after_features = compute_features(mutated, btc, features, cfg)
    after_sample = is_sample_mask(
        mutated,
        cfg.prefilter.vol_z_60,
        cfg.prefilter.r1_sigma,
        cfg.prefilter.brk_60,
        cfg.warmup_bars,
    )

    features_unchanged = bool(
        np.allclose(
            before_features.iloc[: t + 1].to_numpy(dtype="float64"),
            after_features.iloc[: t + 1].to_numpy(dtype="float64"),
            rtol=0.0,
            atol=0.0,
        )
    )
    sample_unchanged = bool(np.array_equal(before_sample[: t + 1], after_sample[: t + 1]))
    return features_unchanged and sample_unchanged


def main() -> int:
    base = load_config(BaseConfig, repo_root / "configs" / "base.yaml")
    wb = window_bounds(base)
    data_root = repo_root / "data"
    # 调参期 3 天（2024-12-01 ~ 2024-12-04），end_ms 远小于 holdout_start_ms（守卫通过）
    start_ms = _ms(2024, 12, 1)
    end_ms = _ms(2024, 12, 4)

    btc = load_bars(data_root, "BTCUSDT", start_ms, end_ms, holdout_start_ms=wb.holdout_start_ms)
    print(f"BTCUSDT：{len(btc)} 分钟")

    failed = False
    for alias in ("C001", "C005", "C023"):
        bars = load_bars(data_root, alias, start_ms, end_ms, holdout_start_ms=wb.holdout_start_ms)
        err = compare_batch_vs_incremental(bars, btc)
        ok = err < 1e-6
        status = "通过" if ok else "失败"
        print(f"{alias}：批量 vs 增量最大误差 = {err:.3e}（阈值 1e-6）→ {status}")
        if not ok:
            failed = True

    # 未来函数测试：只对第一个币跑（足矣验证特征实现无未来函数）
    bars = load_bars(data_root, "C001", start_ms, end_ms, holdout_start_ms=wb.holdout_start_ms)
    t = 2000
    ok = future_function_ok(bars, btc, t)
    print(f"未来函数测试（C001，t={t}）：t 及以前特征/样本不变 → {'通过' if ok else '失败'}")
    if not ok:
        failed = True

    print("T2-8 结果：", "全部通过" if not failed else "存在失败")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
