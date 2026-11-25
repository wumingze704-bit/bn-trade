"""R5 组合的逐折成分参数（1 号第八部分 R5；D22 修复的复现测试）。

修复前：`ensure_groups` 的训练曲线与自身拼接曲线都不接收成分参数，R5 在
`trade.strategies.ensemble.build` 直接抛 `ValueError: R5 的成分参数必须逐折给出`，
整个组合方向无法运行。

规格：docs/13-研究框架.md §5、§6
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from trade.research.pipeline import (
    GroupResult,
    RecordHashes,
    ensure_groups,
    load_group,
    params_key,
    run_path_param,
    save_group,
    select_choices,
)
from trade.research.rolling import Fold, ParamRun

_HASHES = RecordHashes(code_hash="c", data_hash="d", config_hash="f")
_START_MS = int(pd.Timestamp("2022-01-01", tz="UTC").timestamp() * 1000)
_DAY_MS = 86_400_000


def _series(start_day: str, days: int, value: float) -> pd.Series:
    index = pd.date_range(start_day, periods=days, freq="1D", tz="UTC")
    return pd.Series([value] * days, index=index, dtype="float64")


def _wave(start_day: str, days: int, center: float, amplitude: float) -> pd.Series:
    """均值非零、波动非零的日收益序列（保证夏普可比较）。"""
    index = pd.date_range(start_day, periods=days, freq="1D", tz="UTC")
    values = [center + amplitude * (1.0 if i % 2 == 0 else -1.0) for i in range(days)]
    return pd.Series(values, index=index, dtype="float64")


def _run(key: str, daily: pd.Series) -> ParamRun:
    return ParamRun(
        key=key,
        params={"top_k": 3},
        daily=daily,
        trades=[],
        turnover=0.0,
        dropped=0,
        forced=0,
        cost_total=0.0,
        funding_total=0.0,
        long_gross=0.0,
        short_gross=0.0,
        risk_events=0,
        params_meta={},
    )


def test_ensure_groups为组合方向按折传成分参数(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folds = [
        Fold(index=0, start_ms=_START_MS + 30 * _DAY_MS, end_ms=_START_MS + 60 * _DAY_MS),
        Fold(index=1, start_ms=_START_MS + 60 * _DAY_MS, end_ms=_START_MS + 90 * _DAY_MS),
    ]
    components = [
        {"xs_momentum_ls": {"lookback_days": 7}, "funding_carry_ls": {"lookback_days": 1}},
        {"xs_momentum_ls": {"lookback_days": 14}, "funding_carry_ls": {"lookback_days": 3}},
    ]
    captured: dict[str, Any] = {}

    def _fake_run_tasks(tasks, *, payload, workers, market):  # noqa: ANN001
        captured["tasks"] = list(tasks)
        return [_run("k", _series("2022-01-01", 2, 0.0)) for _ in tasks]

    monkeypatch.setattr("trade.research.pipeline._run_tasks", _fake_run_tasks)

    config = SimpleNamespace(equity=SimpleNamespace(signal=10_000.0))
    groups = ensure_groups(
        market=None,
        config=config,  # type: ignore[arg-type]
        sizing=SimpleNamespace(),  # type: ignore[arg-type]
        fee_taker=0.0004,
        risk_limits=SimpleNamespace(),  # type: ignore[arg-type]
        pairs=[("ensemble", {"top_k": 3, "rebalance_hours": 24})],
        payload={},
        workers=1,
        group_dir=tmp_path,
        log_path=tmp_path / "EXPERIMENTS.log",
        hashes=_HASHES,
        round_id="R5",
        folds=folds,
        start_ms=_START_MS,
        end_ms=_START_MS + 90 * _DAY_MS,
        register=False,
        components_by_fold=components,
    )

    tasks = captured["tasks"]
    assert len(tasks) == len(folds) + 1
    for index, fold in enumerate(folds):
        assert tasks[index]["components"] == components[index]
        assert tasks[index]["start_ms"] == _START_MS
        assert tasks[index]["end_ms"] == fold.start_ms
    assert tasks[-1]["kind"] == "path"
    assert tasks[-1]["components_by_fold"] == components

    group = groups[("ensemble", params_key({"top_k": 3, "rebalance_hours": 24}))]
    assert len(group.train_by_fold) == len(folds)
    assert len(group.train_trades_by_fold) == len(folds)


def test_select_choices按折成分参数选参() -> None:
    fold0 = Fold(index=0, start_ms=_START_MS + 10 * _DAY_MS, end_ms=_START_MS + 20 * _DAY_MS)
    fold1 = Fold(index=1, start_ms=_START_MS + 20 * _DAY_MS, end_ms=_START_MS + 30 * _DAY_MS)
    params_a = {"top_k": 3, "rebalance_hours": 24}
    params_b = {"top_k": 5, "rebalance_hours": 24}
    up = _wave("2022-01-01", 9, 0.01, 0.002)
    down = _wave("2022-01-01", 9, -0.01, 0.002)
    groups = {
        ("ensemble", params_key(params_a)): GroupResult(
            family="ensemble",
            params=params_a,
            train_daily=up,
            train_entries=[],
            train_meta={},
            own_daily=up,
            train_by_fold=[up, down],
            train_trades_by_fold=[40, 40],
        ),
        ("ensemble", params_key(params_b)): GroupResult(
            family="ensemble",
            params=params_b,
            train_daily=down,
            train_entries=[],
            train_meta={},
            own_daily=down,
            train_by_fold=[down, up],
            train_trades_by_fold=[40, 40],
        ),
    }

    choices = select_choices(
        groups=groups,
        family="ensemble",
        params_by_key={params_key(params_a): params_a, params_key(params_b): params_b},
        folds=[fold0, fold1],
        start_ms=_START_MS,
        components_by_fold=[{"xs_momentum_ls": {}}, {"xs_momentum_ls": {}}],
    )

    assert choices[0].params_key == params_key(params_a)
    assert choices[1].params_key == params_key(params_b)


def test_run_path_param拒绝折数不一致() -> None:
    fold = Fold(index=0, start_ms=_START_MS, end_ms=_START_MS + _DAY_MS)
    with pytest.raises(ValueError, match="成分参数折数"):
        run_path_param(
            None,
            config=SimpleNamespace(),  # type: ignore[arg-type]
            sizing=SimpleNamespace(),  # type: ignore[arg-type]
            fee_taker=0.0004,
            risk_limits=SimpleNamespace(),  # type: ignore[arg-type]
            family="ensemble",
            params={"top_k": 3},
            folds=[fold],
            components_by_fold=[],
            end_ms=_START_MS + _DAY_MS,
        )


def test_按折训练曲线可落盘复用(tmp_path: Path) -> None:
    group = GroupResult(
        family="ensemble",
        params={"top_k": 3},
        train_daily=_series("2022-01-01", 3, 0.01),
        train_entries=[1, 2],
        train_meta={"train_trades": 2.0},
        own_daily=_series("2022-02-01", 3, 0.02),
        own_meta={"own_trades": 3.0},
        train_by_fold=[_series("2022-01-01", 3, 0.01), _series("2022-01-04", 3, -0.02)],
        train_trades_by_fold=[11, 7],
    )
    path = tmp_path / "ensemble_group.json"
    save_group(path, group, _HASHES)

    loaded = load_group(path, _HASHES)

    assert loaded is not None
    assert len(loaded.train_by_fold) == 2
    assert loaded.train_trades_by_fold == [11, 7]
    assert loaded.train_by_fold[1].iloc[0] == pytest.approx(-0.02)
    run = loaded.as_param_run_for_fold(1)
    assert len(run.trades) == 7
