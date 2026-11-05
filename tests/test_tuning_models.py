"""T4-3 第 2 步搜索脚本核心逻辑测试。

规格：docs/05-模型与训练.md §6.1
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.tuning_models import (
    Trial,
    TrialOutcome,
    build_trials,
    count_logged_trials,
    remaining_budget,
    select_best,
    update_train_yaml,
)
from trade.models.config import ModelConfig

_ROOT = Path(__file__).resolve().parents[1]


def _outcome(
    window: int, hidden: int, loss: str, pf: float, net: float, n: int = 100
) -> TrialOutcome:
    return TrialOutcome(
        trial=Trial(window_L=window, hidden=hidden, loss=loss),
        pf=pf,
        net_return=net,
        n_trades=n,
        theta=0.5,
        failed=False,
    )


def test_build_trials_exact_grid() -> None:
    trials = build_trials(ModelConfig())
    assert len(trials) == 12
    assert {trial.window_L for trial in trials} == {30, 60, 120}
    assert {trial.hidden for trial in trials} == {32, 64}
    assert {trial.loss for trial in trials} == {"bce", "focal"}
    assert len({trial.run_id for trial in trials}) == 12


def test_build_trials_rejects_changed_frozen_values() -> None:
    with pytest.raises(ValueError, match="dropout"):
        build_trials(ModelConfig(dropout=0.3))
    with pytest.raises(ValueError, match="time_decay"):
        build_trials(ModelConfig(time_decay_halflife_months=12))


def test_budget_counts_registered_trials(tmp_path: Path) -> None:
    log_path = tmp_path / "EXPERIMENTS.log"
    assert count_logged_trials(log_path) == 0
    assert remaining_budget(log_path) == 30
    lines = [
        {"run_id": "a"},
        {"run_id": "a"},  # 重复不计
        {"run_id": "b"},
    ]
    log_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    assert count_logged_trials(log_path) == 2
    assert remaining_budget(log_path) == 28


def test_select_best_prefers_positive_pf() -> None:
    outcomes = [
        _outcome(30, 32, "bce", pf=1.8, net=-5.0),
        _outcome(60, 64, "focal", pf=1.4, net=20.0),
        _outcome(120, 32, "bce", pf=1.6, net=10.0),
    ]
    best, all_positive = select_best(outcomes)
    assert best.trial.window_L == 120
    assert best.trial.hidden == 32
    assert all_positive is True


def test_select_best_degrades_when_all_negative() -> None:
    outcomes = [
        _outcome(30, 32, "bce", pf=1.8, net=-5.0),
        _outcome(60, 64, "focal", pf=1.4, net=-20.0),
    ]
    best, all_positive = select_best(outcomes)
    assert best.trial.window_L == 30
    assert all_positive is False


def test_select_best_skips_failed() -> None:
    outcomes = [
        TrialOutcome(Trial(30, 32, "bce"), None, None, None, None, True, "OOM"),
        _outcome(60, 64, "bce", pf=1.1, net=1.0),
    ]
    best, _ = select_best(outcomes)
    assert best.trial.window_L == 60


def test_select_best_all_failed_raises() -> None:
    with pytest.raises(ValueError, match="全部失败"):
        select_best([TrialOutcome(Trial(30, 32, "bce"), None, None, None, None, True)])


def test_update_train_yaml_only_changes_search_fields(tmp_path: Path) -> None:
    original = yaml.safe_load((_ROOT / "configs" / "train.yaml").read_text(encoding="utf-8"))
    path = tmp_path / "train.yaml"
    path.write_text(yaml.safe_dump(original, allow_unicode=True, sort_keys=False), encoding="utf-8")

    update_train_yaml(path, Trial(window_L=120, hidden=32, loss="focal"), draft=False)
    updated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert updated["model"]["window_L"] == 120
    assert updated["model"]["hidden"] == 32
    assert updated["model"]["loss"] == "focal"
    assert updated["model"]["dropout"] == 0.2
    assert updated["model"]["time_decay_halflife_months"] is None
    # 冻结项保持不变
    assert updated["r0"] == original["r0"]
    assert updated["threshold"] == original["threshold"]
    assert updated["model"].get("k_sl", True)


def test_update_train_yaml_draft_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "train.yaml"
    path.write_text("model:\n  window_L: 60\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    update_train_yaml(path, Trial(window_L=30, hidden=64, loss="bce"), draft=True)
    assert path.read_text(encoding="utf-8") == before
