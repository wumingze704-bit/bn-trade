"""R0 缺陷修复回归测试：EWM 偏差修正、预过滤方向、标签截断、数据层与日志字段。

规格：docs/03 §2.2、§4.3；docs/04 §5~§7；docs/06 §8
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade.backtest.report import append_experiments_log, read_experiments_log
from trade.common.exits import ExitConfig
from trade.data.loader import new_entry_symbols
from trade.data.universe import UniverseConfig, build_universe
from trade.features.normalize import ewm_standardize, ewm_standardize_py
from trade.labels.exit_sim import exit_sim
from trade.labels.prefilter import prefilter_mask
from trade.labels.triple_barrier import triple_barrier
from trade.models.dataset import WindowDataset

_DAY_MS = 86_400_000


def _start_ms() -> int:
    return int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def test_EWM段首偏差修正让小量纲特征不再被压到0() -> None:
    """R0-9：累计权重 W 修正后，段首第 2 个值的 z 值不再接近 0。"""
    x = np.array([0.001, 0.002, 0.002, 0.002], dtype="float64")
    z = ewm_standardize_py(x, halflife_bars=10, clip=10.0, min_var=1e-12)
    assert abs(float(z[1])) > 0.5
    np.testing.assert_array_equal(ewm_standardize(x, 10, 10.0, 1e-12), z)


def test_预过滤方向up剔除下跌事件() -> None:
    """R2-4：direction=up 只保留突破或实体阳线事件。"""
    n = 80
    close = np.full(n, 100.0)
    close[-1] = 98.0
    open_ = np.full(n, 100.0)
    bars = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close),
            "low": np.minimum(open_, close),
            "close": close,
            "quote_volume": np.full(n, 1000.0),
        }
    )
    any_mask = prefilter_mask(bars, 1.0, 2.0, 0.0)
    up_mask = prefilter_mask(bars, 1.0, 2.0, 0.0, direction="up")
    assert bool(any_mask[-1]) is True
    assert bool(up_mask[-1]) is False


def _label_bars(n: int = 60) -> pd.DataFrame:
    open_time = np.arange(n) * 60_000 + _start_ms()
    # 收盘价必须有波动：rv_15 在零波动时为 NaN（缺 lookback 的样本会被丢弃）
    close = 100.0 * (1.0 + 0.001 * np.sin(np.arange(n) / 3.0))
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "quote_volume": np.full(n, 1e6),
            "is_filled": np.zeros(n, dtype="int64"),
            "kline_missing": np.zeros(n, dtype="int64"),
            "aggtrades_missing": np.zeros(n, dtype="int64"),
            "p_1s": close,
            "p_1s_filled": np.zeros(n, dtype="int64"),
            "hi_post_1s": close * 1.001,
            "lo_post_1s": close * 0.999,
        }
    )


@pytest.mark.parametrize("clip", [(0.0, 0.0), (-1.0, 1.0)])
def test_exit_sim的r_mult截断来自配置(clip: tuple[float, float]) -> None:
    """R0-11e：exit_sim 的 r_mult 截断区间由配置传入，不再写死。"""
    labels, _ = exit_sim(
        _label_bars(),
        np.array([20], dtype="int64"),
        k_sl=2.5,
        sl_min=0.01,
        sl_max=0.025,
        H_label=60,
        entry_latency_ms=1000,
        y_min_r=0.25,
        exit_cfg=ExitConfig(type="trail", H_max=20),
        fees_taker=0.0005,
        fees_maker=0.0002,
        half_spread_min=0.0001,
        slip_min=0.0001,
        Y=1.0,
        notional_ref=100.0,
        max_chase=0.01,
        tick_size=0.001,
        r_mult_clip=clip,
    )
    assert not labels.empty
    assert labels["r_mult"].max() <= clip[1] + 1e-12
    assert labels["r_mult"].min() >= clip[0] - 1e-12


def test_三重障碍丢弃陈旧入场价样本() -> None:
    """R0-15f：入场秒无成交（p_1s_filled=1）的样本不产生标签。"""
    params = dict(
        k_sl=2.5,
        rr=2.0,
        sl_min=0.01,
        sl_max=0.025,
        H_label=10,
        entry_latency_ms=1000,
        c_rt=0.0012,
        r_mult_clip=(-1.5, 3.0),
        fill_ratio_max=1.0,
    )
    bars = _label_bars()
    labels_ok, kept_ok = triple_barrier(bars, np.array([20], dtype="int64"), **params)
    assert not labels_ok.empty
    assert kept_ok.size == 1

    bars.loc[21, "p_1s_filled"] = 1
    labels, kept = triple_barrier(bars, np.array([20], dtype="int64"), **params)
    assert labels.empty
    assert kept.size == 0


def _universe_cfg() -> UniverseConfig:
    rule = {
        "rebalance": "monthly",
        "top_n": 2,
        "min_listing_days": 3,
        "lookback_days": 10,
        "min_daily_quote_median": 100.0,
        "step_notional_max": 10.0,
        "min_notional_max": 5.0,
        "min_leverage": 5,
        "tick_rel_max": 0.01,
        "exclude_bases": ["BTC"],
        "exclude_stable": True,
        "exclude_tradfi": True,
        "excluded_symbols": [],
    }
    return UniverseConfig(reviewed=False, btc_context="BTCUSDT", rule=rule)


def test_选币缺交易规则直接报错() -> None:
    """R0-15d：缺交易规则时不得用默认值继续选币。"""
    rebalance = _start_ms()
    open_time = (rebalance - 60 * _DAY_MS) + np.arange(40, dtype="int64") * _DAY_MS
    daily = {
        "SOLUSDT": pd.DataFrame(
            {
                "open_time": open_time,
                "close": np.full(40, 100.0),
                "quote_volume": np.full(40, 5_000_000.0),
            }
        )
    }
    with pytest.raises(ValueError, match="SOLUSDT"):
        build_universe(rebalance, daily, {}, _universe_cfg())


def test_新进入组按每折训练月计算() -> None:
    """R0-15e：新进入组按给定训练段的月份判断，不用固定前 12 个月。"""
    membership = {
        "2025-01": frozenset({"A"}),
        "2025-02": frozenset({"A", "B"}),
        "2025-03": frozenset({"A", "C"}),
    }
    assert new_entry_symbols(membership, ["2025-01", "2025-02"]) == frozenset({"C"})
    assert new_entry_symbols(membership, ["2025-01", "2025-02", "2025-03"]) == frozenset()


def test_特征缺is_filled列时报错(tmp_path: Path) -> None:
    """R0-8：is_filled 缺失时直接报错，不静默返回全 0。"""
    features_dir = tmp_path / "features"
    labels_dir = tmp_path / "labels"
    features_dir.mkdir()
    labels_dir.mkdir()
    open_time = (np.arange(5, dtype="int64") * 60_000).tolist()
    pd.DataFrame({"open_time": open_time, "f1": np.zeros(5)}).to_parquet(
        features_dir / "A.parquet", index=False
    )
    pd.DataFrame(
        {
            "open_time": open_time,
            "y": np.zeros(5, dtype="int64"),
            "r_mult": np.zeros(5),
            "weight": np.ones(5),
            "t_exit": open_time,
        }
    ).to_parquet(labels_dir / "A.parquet", index=False)
    with pytest.raises(ValueError, match="is_filled"):
        WindowDataset(features_dir, labels_dir, ["A"], ["f1"], window_L=2)


def test_试验日志记录kind与失败标记(tmp_path: Path) -> None:
    """R0-6：EXPERIMENTS.log 写入 kind / theta / failed，试验预算只数 tuning。"""
    log_path = tmp_path / "EXPERIMENTS.log"
    append_experiments_log(
        log_path, "t1", "hash", {"net_return_usdt": 1.0}, kind="tuning", theta=0.32
    )
    append_experiments_log(log_path, "t2", "hash", {}, kind="test", failed=True)
    rows = read_experiments_log(log_path)
    assert rows[0]["kind"] == "tuning"
    assert rows[0]["theta"] == 0.32
    assert rows[0]["failed"] is False
    assert rows[1]["kind"] == "test"
    assert rows[1]["failed"] is True
    with pytest.raises(ValueError):
        append_experiments_log(log_path, "t3", "hash", {}, kind="unknown")
