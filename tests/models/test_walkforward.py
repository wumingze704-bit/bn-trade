"""5 折 Walk-Forward 与 signals.parquet 测试。

规格：docs/03-数据与选币.md §6；docs/05-模型与训练.md §6
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade.common.config import load_config
from trade.common.exits import ExitConfig
from trade.data.universe import load_universe_config, rule_hash
from trade.features import enabled_features
from trade.features.registry import FeaturesConfig
from trade.models.config import ModelConfig, WalkForwardConfig
from trade.models.dataset import Sample, WindowDataset, month_start_ms
from trade.models.walkforward import apply_purge_embargo, build_folds, run_fold

_ROOT = Path(__file__).resolve().parents[2]
_MONTHS = [
    "2024-10",
    "2024-11",
    "2024-12",
    "2025-01",
    "2025-02",
    "2025-03",
    "2025-04",
    "2025-05",
    "2025-06",
    "2025-07",
    "2025-08",
    "2025-09",
    "2025-10",
    "2025-11",
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
    "2026-09",
]


def test_build_folds_matches_design() -> None:
    folds = build_folds(_MONTHS, first_test_month="2025-10", n_folds=5, train_start="2024-10")
    assert [fold.fold_id for fold in folds] == ["F1", "F2", "F3", "F4", "F5"]
    assert folds[0].train_months == _MONTHS[:11]
    assert folds[0].val_month == "2025-09"
    assert folds[0].test_months == ["2025-10", "2025-11"]
    assert folds[4].val_month == "2026-05"
    assert folds[4].test_months == ["2026-06", "2026-07"]
    for previous, current in zip(folds, folds[1:], strict=False):
        assert len(current.train_months) > len(previous.train_months)
        assert current.val_month not in current.test_months


def test_build_folds_invalid_month() -> None:
    with pytest.raises(ValueError, match="升序"):
        build_folds(list(reversed(_MONTHS)), first_test_month="2025-10")
    with pytest.raises(ValueError, match="不在月份列表"):
        build_folds(_MONTHS, first_test_month="2023-01")


def _clone_with_samples(dataset: WindowDataset, samples: list[Sample]) -> WindowDataset:
    """复制数据集元信息并替换样本（只用于边界过滤单测）。"""
    clone = object.__new__(WindowDataset)
    for name in (
        "window_L",
        "max_fill_ratio",
        "device",
        "data_placement",
        "symbols",
        "feature_names",
        "months",
        "cache_dir",
        "xs",
    ):
        setattr(clone, name, getattr(dataset, name))
    clone.samples = samples
    return clone


def _empty_dataset() -> WindowDataset:
    dataset = object.__new__(WindowDataset)
    dataset.window_L = 1
    dataset.max_fill_ratio = 0.2
    dataset.device = "cpu"
    dataset.data_placement = "gpu"
    dataset.symbols = ["C01"]
    dataset.feature_names = ["f0"]
    dataset.months = None
    dataset.cache_dir = None
    dataset.xs = []
    dataset.samples = []
    return dataset


def test_apply_purge_embargo() -> None:
    boundary = month_start_ms("2025-03")
    base = _empty_dataset()
    train = _clone_with_samples(
        base,
        [
            Sample(0, 0, 0, 0, 1, boundary - 120 * 60_000),
            Sample(0, 0, 0, 0, 1, boundary - 30 * 60_000),
            Sample(0, 0, 0, 0, 1, boundary - 5 * 60_000),
        ],
    )
    val = _clone_with_samples(
        base,
        [
            Sample(0, 0, 0, 0, 1, boundary + 60 * 60_000),
            Sample(0, 0, 0, 0, 1, boundary + 2 * 86_400_000),
        ],
    )
    filtered_train, filtered_val = apply_purge_embargo(
        train, val, boundary_ms=boundary, purge_bars=60, embargo_days=1
    )
    assert len(filtered_train) == 1  # 末尾 60 分钟内样本被 Purge
    assert len(filtered_val) == 1  # 首日样本被 Embargo
    assert filtered_val.samples[0].open_time >= boundary + 86_400_000


def _make_month_coin(root: Path, alias: str, months: list[str], feature_names: list[str]) -> None:
    features_dir = root / "features" / "run"
    labels_dir = root / "labels" / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(sum(ord(ch) for ch in alias))
    frames = []
    labels = []
    for month in months:
        start = month_start_ms(month)
        times = start + np.arange(1800, dtype="int64") * 60_000
        frame = pd.DataFrame(rng.normal(size=(len(times), len(feature_names))).astype("float16"))
        frame.columns = feature_names
        frame["open_time"] = times
        frame["is_filled"] = 0
        frames.append(frame)
        y = (rng.random(len(times)) > 0.5).astype("int64")
        labels.append(
            pd.DataFrame(
                {
                    "open_time": times,
                    "y": y,
                    "r_mult": np.where(y == 1, 1.0, -1.0),
                    "weight": 1.0,
                }
            )
        )
    pd.concat(frames, ignore_index=True).to_parquet(features_dir / f"{alias}.parquet", index=False)
    pd.concat(labels, ignore_index=True).to_parquet(labels_dir / f"{alias}.parquet", index=False)


@pytest.fixture
def wf_fixture(tmp_path: Path) -> dict[str, object]:
    features_cfg = load_config(FeaturesConfig, _ROOT / "configs" / "features.yaml")
    exits_cfg = load_config(ExitConfig, _ROOT / "configs" / "exits.yaml")
    universe_cfg = load_universe_config(_ROOT / "configs" / "universe.yaml")
    feature_names = [feature.name for feature in enabled_features(features_cfg)]
    months = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"]
    for alias in ("C01", "C02", "C03"):
        _make_month_coin(tmp_path, alias, months, feature_names)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return {
        "root": tmp_path,
        "feature_names": feature_names,
        "features_cfg": features_cfg,
        "exits_cfg": exits_cfg,
        "universe_hash": rule_hash(universe_cfg),
        "manifest": manifest,
    }


def test_run_fold_smoke(wf_fixture: dict[str, object]) -> None:
    root = wf_fixture["root"]
    feature_names = wf_fixture["feature_names"]
    cfg = ModelConfig(
        arch="tcn",
        window_L=10,
        hidden=8,
        blocks=2,
        batch_size=256,
        max_epochs=1,
        warmup_epochs=0,
        bf16=False,
        seeds=[42, 43],
    )
    fold = build_folds(
        ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"],
        first_test_month="2025-04",
        n_folds=1,
        train_start="2025-01",
    )[0]
    assert fold.val_month == "2025-03"

    def evaluator(signals: pd.DataFrame, n_days: float) -> dict[float, dict[str, float]]:
        assert not signals.empty
        assert n_days > 0
        return {
            theta: {"net_return": 1.0, "profit_factor": 1.5, "n_trades": 200}
            for theta in (0.30, 0.40, 0.50)
        }

    result = run_fold(
        fold,
        run_id="wf_smoke",
        features_dir=root / "features" / "run",  # type: ignore[operator]
        labels_dir=root / "labels" / "run",  # type: ignore[operator]
        feature_names=feature_names,  # type: ignore[arg-type]
        train_symbols=["C01", "C02"],
        all_symbols=["C01", "C02", "C03"],
        cfg=cfg,
        walk_cfg=WalkForwardConfig(purge_bars=5, embargo_days=1),
        threshold_evaluator=evaluator,
        features_cfg=wf_fixture["features_cfg"],  # type: ignore[arg-type]
        exit_cfg=wf_fixture["exits_cfg"],  # type: ignore[arg-type]
        universe_rule_hash=str(wf_fixture["universe_hash"]),
        market="usdm",
        models_root=root / "models",  # type: ignore[operator]
        signals_root=root / "signals",  # type: ignore[operator]
        manifest_path=wf_fixture["manifest"],  # type: ignore[arg-type]
    )
    assert result.model_dir.is_dir()
    assert (result.model_dir / "model_meta.json").is_file()
    assert result.signals_path.is_file()
    signals = pd.read_parquet(result.signals_path)
    assert set(signals.columns) >= {"symbol", "open_time", "p", "m"}
    # 测试月交易范围内全部币（含留出组）
    assert set(signals["symbol"]) == {"C01", "C02", "C03"}
    assert result.threshold == pytest.approx(0.30)
    assert 0.0 <= result.val_pr_auc <= 1.0
