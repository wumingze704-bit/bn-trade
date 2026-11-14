"""T4-3 第 1 步试验登记测试（05 §6.1、06 §8）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

from scripts.tuning_exits import (
    _BACKFILL_TRIALS,
    _backfill_log,
    _load_disk_features,
    _prepare_data,
    _scan_theta,
    _trial_run_id,
)
from trade.backtest.engine import ExecutedTrade, PortfolioResult, PrecomputedTrade
from trade.backtest.metrics import compute_metrics
from trade.common.config import BaseConfig, load_config
from trade.data.loader import rebalance_ms_of, universe_months
from trade.features import FeaturesConfig, enabled_features, feature_hash
from trade.models.calibration import select_threshold

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_temp_base(tmp_path: Path) -> Path:
    """复制 configs/base.yaml，把 artifact_root 指向临时目录（避免写真实 artifacts）。"""
    values = yaml.safe_load((_REPO_ROOT / "configs" / "base.yaml").read_text(encoding="utf-8"))
    values["paths"] = {
        "data_root": str(_REPO_ROOT / "data"),
        "artifact_root": str(tmp_path / "artifacts"),
    }
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
    return path


def test_补登日志写入18组且重复执行不重复登记(tmp_path):
    base_path = _write_temp_base(tmp_path)
    args = argparse.Namespace(base=str(base_path))
    _backfill_log(tmp_path, args)
    log_path = tmp_path / "artifacts" / "reports" / "EXPERIMENTS.log"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == len(_BACKFILL_TRIALS) == 18
    assert all(row["n_trades"] is None for row in rows)
    assert all(row["sharpe"] is None for row in rows)

    _backfill_log(tmp_path, args)
    rows_again = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(rows_again) == 18


def test_补登run_id唯一且标注结果缺失():
    run_ids = [_trial_run_id(trial) for trial in _BACKFILL_TRIALS]
    assert len(set(run_ids)) == 18
    assert all(run_id.endswith("_n_missing") for run_id in run_ids)
    assert sum("valM11-12" in run_id for run_id in run_ids) == 12
    assert sum("valM10-12" in run_id for run_id in run_ids) == 6


def test_数据集feature_hash不一致时报错(tmp_path: Path) -> None:
    features_dir = tmp_path / "ds"
    features_dir.mkdir()
    features_cfg = load_config(FeaturesConfig, _REPO_ROOT / "configs" / "features.yaml")
    names = [feature.name for feature in enabled_features(features_cfg)]
    manifest = {
        "feature_hash": "deadbeef",
        "features": names,
        "window": {"start_ms": 0, "end_ms": 10**15},
    }
    (features_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="feature_hash"):
        _load_disk_features(features_dir, features_cfg, names, ["C01"], 0, 1)


def test_数据集覆盖区间不足时报错(tmp_path: Path) -> None:
    features_dir = tmp_path / "ds"
    features_dir.mkdir()
    features_cfg = load_config(FeaturesConfig, _REPO_ROOT / "configs" / "features.yaml")
    features = enabled_features(features_cfg)
    names = [feature.name for feature in features]
    manifest = {
        "feature_hash": feature_hash(features),
        "features": names,
        "window": {"start_ms": 1_000, "end_ms": 2_000},
    }
    (features_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="不包含所需区间"):
        _load_disk_features(features_dir, features_cfg, names, ["C01"], 0, 3_000)


def _nested_args(base_path: Path) -> argparse.Namespace:
    """构造 `_prepare_data` 需要的命令行参数（只处理 1 个币）。"""
    return argparse.Namespace(
        base=str(base_path),
        universe=str(_REPO_ROOT / "configs" / "universe.yaml"),
        features=str(_REPO_ROOT / "configs" / "features.yaml"),
        exits=str(_REPO_ROOT / "configs" / "exits.yaml"),
        sizing=str(_REPO_ROOT / "configs" / "sizing.yaml"),
        backtest=str(_REPO_ROOT / "configs" / "backtest.yaml"),
        features_dir=None,
        limit=1,
    )


def test_嵌套确认只读取调参期月份(tmp_path: Path, monkeypatch) -> None:
    """`tuning_exits` 加载 bars 的终点不超过 M13 起点（05 §6.1、红线 3）。"""
    import scripts.tuning_exits as tuning_exits

    calls: list[tuple[int, int]] = []

    def fake_load_bars(data_root, alias, start_ms, end_ms, **kwargs):  # noqa: ANN001, ANN202
        calls.append((int(start_ms), int(end_ms)))
        raise RuntimeError("stop-after-record")

    monkeypatch.setattr(tuning_exits, "load_bars", fake_load_bars)
    base_path = _write_temp_base(tmp_path)
    base_cfg = load_config(BaseConfig, base_path)
    months = universe_months(base_cfg)
    tuning_end = rebalance_ms_of(months[base_cfg.data_window.tuning_months])
    with pytest.raises(RuntimeError, match="stop-after-record"):
        _prepare_data(_REPO_ROOT, _nested_args(base_path))
    assert calls, "未发起任何 bars 加载"
    assert all(end_ms <= tuning_end for _, end_ms in calls)


def _executed(p: float, net_pnl: float) -> ExecutedTrade:
    """构造一笔已成交流水（只用于阈值扫描的指标口径测试）。"""
    return ExecutedTrade(
        symbol="A",
        group="train",
        signal_open_time=0,
        entry_open_time=0,
        exit_open_time=60_000,
        p=p,
        m=0.0,
        p_fill=100.0,
        sl=0.01,
        exit_price=100.0,
        exit_type="tp",
        exit_type_code=3,
        notional=100.0,
        qty=1.0,
        margin=20.0,
        bars_held=1,
        stop_updates=0,
        mfe_r=0.0,
        mae_r=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        capped_by=None,
    )


def test_scan_theta与select_threshold结果一致(monkeypatch) -> None:
    """`_scan_theta` 直接调用 `select_threshold`，结果与手工按 05 §7.2 计算一致。"""
    import scripts.tuning_exits as tuning_exits

    net_by_p = {0.30 + 0.02 * i: (1.0 if i % 2 == 0 else -0.5) for i in range(26)}
    executed = [_executed(p, net) for p, net in net_by_p.items()]

    def fake_simulate(data, trades, skipped, equity):  # noqa: ANN001, ANN202
        result = PortfolioResult()
        result.trades = [_executed(trade.p, net_by_p[trade.p]) for trade in trades]
        return result

    monkeypatch.setattr(tuning_exits, "_simulate", fake_simulate)
    trades = [
        PrecomputedTrade(
            symbol="A",
            group="train",
            signal_open_time=0,
            entry_open_time=0,
            p=p,
            m=0.0,
            p_fill=100.0,
            sl=0.01,
            loss_rate=0.01,
            mean_qv_60=1e6,
            exit_open_time=60_000,
            exit_price=100.0,
            exit_type="tp",
            exit_type_code=3,
            bars_held=1,
            stop_updates=0,
            mfe_r=0.0,
            mae_r=0.0,
            gross_return=0.0,
            exit_fee_rate=0.0,
        )
        for p in net_by_p
    ]
    data = argparse.Namespace(val_start_ms=0, val_end_ms=86_400_000)
    got = _scan_theta(data, trades, [], 100.0)  # type: ignore[arg-type]

    manual: dict[float, dict[str, float]] = {}
    for theta in net_by_p:
        result = PortfolioResult()
        result.trades = [trade for trade in executed if trade.p >= theta]
        metrics = compute_metrics(result, 100.0)
        manual[theta] = {
            "net_return": float(metrics["net_return_usdt"]),
            "profit_factor": float(metrics["profit_factor"]),
            "n_trades": float(metrics["n_trades"]),
        }
    expected = select_threshold(manual, n_days=1.0)
    assert got["theta"] == pytest.approx(expected.theta)
    assert got["pf"] == pytest.approx(expected.profit_factor)
    assert got["n_trades"] == pytest.approx(float(expected.n_trades))
