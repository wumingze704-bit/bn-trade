"""`tuning_exits` 流式低内存模式的等价比对（05 §6.1）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.tuning_exits import CoinData, SampleSet, TuningData, _build_samples
from tests.features.helpers import make_bars
from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import BacktestConfig, TradeRule
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.features.registry import FeaturesConfig, enabled_features

_ROOT = Path(__file__).resolve().parents[1]
_ALIASES = ("C001", "C002")


def _tuning_data(tmp_path: Path, *, low_memory: bool) -> TuningData:
    """构造两个币的最小 `TuningData`（特征落盘，样本位置固定）。"""
    base = load_config(BaseConfig, _ROOT / "configs" / "base.yaml")
    features_cfg = load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml")
    exit_cfg = load_config(ExitConfig, _ROOT / "configs" / "exits.yaml")
    sizing = load_config(SizingConfig, _ROOT / "configs" / "sizing.yaml")
    backtest = load_config(BacktestConfig, _ROOT / "configs" / "backtest.yaml")
    names = [feature.name for feature in enabled_features(features_cfg)]
    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    per_coin: dict[str, CoinData] = {}
    rng = np.random.default_rng(7)
    for index, alias in enumerate(_ALIASES):
        bars = make_bars(600, seed=11 + index)
        times = bars["open_time"].to_numpy(dtype="int64")
        values = rng.normal(0.0, 1.0, size=(len(bars), len(names))).astype("float16")
        frame = pd.DataFrame(values, columns=names)
        frame.insert(0, "open_time", times)
        frame.to_parquet(features_dir / f"{alias}.parquet", index=False)
        positions = np.arange(120, 480)
        per_coin[alias] = CoinData(
            symbol=f"SYM{index}",
            bars=bars,
            segments=np.zeros(len(bars), dtype="int64"),
            x=None if low_memory else values,
            open_time=times,
            sample_positions=positions,
            features_path=features_dir / f"{alias}.parquet",
        )
    return TuningData(
        base_cfg=base,
        features_cfg=features_cfg,
        exits_cfg=exit_cfg,
        sizing_cfg=sizing,
        backtest_cfg=backtest,
        months=["2024-10", "2024-11"],
        coins=[],
        per_coin=per_coin,
        bars_mtm={},
        prepared={},
        rules={alias: TradeRule(0.01, 0.01, 5.0) for alias in _ALIASES},
        groups={alias: "train" for alias in _ALIASES},
        fees=FeeSchedule(maker=0.0002, taker=0.0005),
        feature_names=names,
        val_start_ms=0,
        val_end_ms=10**15,
        log_path=tmp_path / "EXPERIMENTS.log",
        config_inputs={},
        low_memory=low_memory,
        data_root=tmp_path,
        features_dir=features_dir,
        bars_start_ms=0,
        bars_end_ms=10**15,
        holdout_start_ms=10**15,
    )


def test_流式模式与内存模式样本逐字段一致(tmp_path: Path) -> None:
    """`--low-memory` 只是改变数据搬运方式，样本数组必须与内存模式完全相同（红线 15 口径）。"""
    memory = _tuning_data(tmp_path, low_memory=False)
    streaming = _tuning_data(tmp_path, low_memory=True)
    exit_cfg = memory.exits_cfg
    expected = _build_samples(memory, exit_cfg, k_sl=2.5, sl_min=0.010, sl_max=0.025)
    got = _build_samples(streaming, exit_cfg, k_sl=2.5, sl_min=0.010, sl_max=0.025)
    assert expected is not None and got is not None
    assert np.array_equal(got.x, expected.x)
    assert np.array_equal(got.y, expected.y)
    assert np.array_equal(got.w, expected.w)
    assert np.array_equal(got.m, expected.m)
    assert np.array_equal(got.open_time, expected.open_time)
    assert np.array_equal(got.symbol, expected.symbol)
    assert np.array_equal(got.t_exit, expected.t_exit)


def test_验证段信号导出只含指定月份(tmp_path: Path) -> None:
    """`--dump-signals` 只导出 m_from~m_to 的样本，列与 signals 契约一致（05 §7.2）。"""
    from scripts.tuning_exits import _dump_validation_signals

    class _StubBooster:
        best_iteration = 0

        def predict(self, x, num_iteration=0):  # noqa: ANN001, ANN202
            del num_iteration
            return np.linspace(0.1, 0.4, len(x))

    count = 12
    samples = SampleSet(
        x=np.zeros((count, 4), dtype=np.float32),
        y=np.zeros(count, dtype=np.int64),
        w=np.ones(count, dtype=np.float64),
        m=np.array([9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 12, 13], dtype=np.int64),
        open_time=np.arange(count, dtype=np.int64) * 60_000,
        symbol=np.array(["C001"] * count, dtype=object),
        t_exit=np.arange(count, dtype=np.int64) * 60_000,
    )
    path = tmp_path / "signals.parquet"
    _dump_validation_signals(
        path, samples, _StubBooster(), 0.34, m_from=10, m_to=12, label="M0-M10..M12"
    )
    frame = pd.read_parquet(path)
    assert len(frame) == 9  # m 在 10~12 的样本（3 + 3 + 3）
    assert list(frame.columns) == [
        "symbol",
        "open_time",
        "p",
        "m",
        "is_sample",
        "fold_id",
        "theta",
    ]
    assert frame["symbol"].unique().tolist() == ["C001"]
    assert frame["theta"].unique().tolist() == [0.34]
    assert frame["is_sample"].unique().tolist() == [1]


def test_流式模式在bars不在内存时按需读取(tmp_path: Path, monkeypatch) -> None:
    """低内存条目的 `bars` 为 None 时，标签与特征块都走按需读取，结果与内存模式一致。"""
    import scripts.tuning_exits as tuning_exits

    data = _tuning_data(tmp_path, low_memory=True)
    bars_by_alias = {alias: info.bars for alias, info in data.per_coin.items()}
    assert all(bars is not None for bars in bars_by_alias.values())

    def fake_load_bars(data_root, alias_name, start_ms, end_ms, **kwargs):  # noqa: ANN001, ANN202
        del data_root, start_ms, end_ms, kwargs
        return bars_by_alias[alias_name]

    monkeypatch.setattr(tuning_exits, "load_bars", fake_load_bars)
    stripped = replace(
        data,
        per_coin={
            alias: replace(info, bars=None, open_time=None) for alias, info in data.per_coin.items()
        },
    )
    from_memory = _build_samples(data, data.exits_cfg, k_sl=2.5, sl_min=0.010, sl_max=0.025)
    from_disk = _build_samples(stripped, data.exits_cfg, k_sl=2.5, sl_min=0.010, sl_max=0.025)
    assert from_memory is not None and from_disk is not None
    assert np.array_equal(from_disk.x, from_memory.x)
    assert np.array_equal(from_disk.open_time, from_memory.open_time)
    assert np.array_equal(from_disk.symbol, from_memory.symbol)
