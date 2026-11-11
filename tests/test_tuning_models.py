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
    filter_window_entries,
    logged_outcomes,
    remaining_budget,
    select_best,
    update_train_yaml,
)
from trade.data.universe import SnapshotCoin
from trade.models.config import ModelConfig

_ROOT = Path(__file__).resolve().parents[1]


def _coin(symbol: str, alias: str) -> SnapshotCoin:
    return SnapshotCoin(
        symbol=symbol, alias=alias, role="train", rank=1, daily_quote_median=1.0, first_listing_ms=0
    )


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


def test_filter_window_entries_只保留窗口内有入选月份的币() -> None:
    entries = [_coin("AAAUSDT", "C001"), _coin("BBBUSDT", "C002"), _coin("CCCUSDT", "C003")]
    membership = {
        "2025-07": frozenset({"AAAUSDT"}),
        "2025-08": frozenset({"BBBUSDT"}),
    }
    kept = filter_window_entries(entries, membership, ["2025-07", "2025-08"])
    # CCCUSDT 在验证段没有入选月份 → 没有对应 bars，必须跳过（否则 load_bars 报错）
    assert [entry.symbol for entry in kept] == ["AAAUSDT", "BBBUSDT"]


def test_filter_window_entries_缺月份时返回空() -> None:
    assert filter_window_entries([_coin("AAAUSDT", "C001")], {}, ["2025-07"]) == []


def test_logged_outcomes_只接受连续前缀(tmp_path: Path) -> None:
    trials = build_trials(ModelConfig())
    log_path = tmp_path / "EXPERIMENTS.log"
    rows = [
        {
            "run_id": trials[0].run_id,
            "profit_factor": 1.25,
            "net_return_usdt": 100.0,
            "n_trades": 500,
        },
        # 第 2 组缺失（模拟中断）：第 3 组已登记也不能当作前缀
        {
            "run_id": trials[2].run_id,
            "profit_factor": 1.1,
            "net_return_usdt": 10.0,
            "n_trades": 100,
        },
    ]
    log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    done = logged_outcomes(log_path, trials)
    assert [item.trial.run_id for item in done] == [trials[0].run_id]
    assert done[0].pf == pytest.approx(1.25)
    assert done[0].net_return == pytest.approx(100.0)
    assert done[0].n_trades == 500
    assert done[0].failed is False


def test_logged_outcomes_空指标视为失败组(tmp_path: Path) -> None:
    trials = build_trials(ModelConfig())
    log_path = tmp_path / "EXPERIMENTS.log"
    log_path.write_text(
        json.dumps({"run_id": trials[0].run_id, "profit_factor": None, "net_return_usdt": None})
        + "\n",
        encoding="utf-8",
    )
    done = logged_outcomes(log_path, trials)
    assert len(done) == 1
    assert done[0].failed is True
    assert done[0].pf is None


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
