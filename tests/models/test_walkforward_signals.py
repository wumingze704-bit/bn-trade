"""五折按行阈值拼接的校验测试（06 §8、05 §7.2）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from trade.models.walkforward import load_walk_forward_signals

_MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01"]


def _month_start_ms(month: str) -> int:
    year, month_num = (int(part) for part in month.split("-"))
    return int(datetime(year, month_num, 1, tzinfo=UTC).timestamp() * 1000)


def _write_fold(
    root: Path,
    *,
    fold_id: str,
    test_months: list[str],
    theta: float,
    feature_hash: str = "abc123",
    rows: list[tuple[str, int]] | None = None,
) -> dict[str, object]:
    """写一折的最小 model_meta.json 与 signals.parquet，返回 summary 条目。"""
    model_dir = root / f"model_{fold_id}"
    model_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "model_id": f"m_{fold_id}",
        "feature_hash": feature_hash,
        "exit_policy": {"type": "trail"},
        "label": {"mode": "exit_sim"},
        "universe_rule_hash": "u1",
        "market": "usdm",
        "threshold": theta,
        "temperature": 1.0,
    }
    (model_dir / "model_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if rows is None:
        rows = [("A", _month_start_ms(month)) for month in test_months]
    frame = pd.DataFrame(
        {
            "symbol": [symbol for symbol, _ in rows],
            "open_time": [ts for _, ts in rows],
            "p": [0.6] * len(rows),
            "m": [0.0] * len(rows),
            "is_sample": [1] * len(rows),
            "fold_id": [fold_id] * len(rows),
            "theta": [theta] * len(rows),
        }
    )
    signals_path = root / f"signals_{fold_id}.parquet"
    frame.to_parquet(signals_path, index=False)
    return {
        "fold_id": fold_id,
        "test_months": test_months,
        "model_dir": str(model_dir),
        "signals": str(signals_path),
        "threshold": theta,
        "temperature": 1.0,
        "val_pr_auc": 0.5,
        "n_test_signals": len(rows),
    }


def _summary(root: Path, folds: list[dict[str, object]]) -> Path:
    path = root / "walk_forward_summary.json"
    path.write_text(json.dumps({"folds": folds}, ensure_ascii=False), encoding="utf-8")
    return path


def test_两折拼接成连续测试段(tmp_path: Path) -> None:
    """两折各一月、θ 不同：拼接后每行保留本折 θ（06 §8）。"""
    folds = [
        _write_fold(tmp_path, fold_id="F1", test_months=[_MONTHS[0]], theta=0.45),
        _write_fold(tmp_path, fold_id="F2", test_months=[_MONTHS[1]], theta=0.55),
    ]
    signals, metas = load_walk_forward_signals(
        _summary(tmp_path, folds), expected_months=_MONTHS[:2]
    )
    assert list(signals["theta"]) == [0.45, 0.55]
    assert [meta["fold_id"] for meta in metas] == ["F1", "F2"]


def test_折测试月重叠时报错(tmp_path: Path) -> None:
    folds = [
        _write_fold(tmp_path, fold_id="F1", test_months=[_MONTHS[0], _MONTHS[1]], theta=0.5),
        _write_fold(tmp_path, fold_id="F2", test_months=[_MONTHS[1]], theta=0.5),
    ]
    with pytest.raises(ValueError, match="重叠"):
        load_walk_forward_signals(_summary(tmp_path, folds), expected_months=_MONTHS[:2])


def test_折测试月不连续时报错(tmp_path: Path) -> None:
    folds = [
        _write_fold(tmp_path, fold_id="F1", test_months=[_MONTHS[0]], theta=0.5),
        _write_fold(tmp_path, fold_id="F2", test_months=[_MONTHS[2]], theta=0.5),
    ]
    with pytest.raises(ValueError, match="不一致"):
        load_walk_forward_signals(_summary(tmp_path, folds), expected_months=_MONTHS[:3])


def test_折特征哈希不一致时报错(tmp_path: Path) -> None:
    folds = [
        _write_fold(tmp_path, fold_id="F1", test_months=[_MONTHS[0]], theta=0.5),
        _write_fold(
            tmp_path, fold_id="F2", test_months=[_MONTHS[1]], theta=0.5, feature_hash="other"
        ),
    ]
    with pytest.raises(ValueError, match="特征|feature_hash"):
        load_walk_forward_signals(_summary(tmp_path, folds), expected_months=_MONTHS[:2])


def test_信号落在本折测试月之外时报错(tmp_path: Path) -> None:
    folds = [_write_fold(tmp_path, fold_id="F1", test_months=[_MONTHS[0]], theta=0.5)]
    # 把信号时间挪到下一个月
    signals_path = Path(str(folds[0]["signals"]))
    frame = pd.read_parquet(signals_path)
    frame.loc[0, "open_time"] = _month_start_ms(_MONTHS[1])
    frame.to_parquet(signals_path, index=False)
    with pytest.raises(ValueError, match="之外"):
        load_walk_forward_signals(_summary(tmp_path, folds), expected_months=_MONTHS[:1])
