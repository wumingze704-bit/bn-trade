"""报告产物与 gate.json 测试（06 §8）。"""

from __future__ import annotations

import json

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.report import (
    compute_experiment_hash,
    experiments_log_path,
    gate_payload,
    write_report,
)


def _result() -> PortfolioResult:
    trade = ExecutedTrade(
        symbol="A",
        group="train",
        signal_open_time=0,
        entry_open_time=60_000,
        exit_open_time=120_000,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.008,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        notional=10.0,
        qty=0.1,
        margin=2.0,
        bars_held=2,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.005,
        exit_fee=0.002,
        gross_pnl=1.0,
        net_pnl=0.993,
        capped_by=None,
    )
    return PortfolioResult(trades=[trade])


def test_write_report产物齐全(tmp_path):
    result = _result()
    write_report(
        tmp_path,
        "run1",
        source="fixture",
        run_results={"reference": {"base": result}},
        reference_metrics={"net_return_usdt": 0.993},
        sanity_results={"S8": "not_run_待真实数据", "S9": "passed"},
        initial_equity=10.0,
    )
    for name in ("report.md", "summary.json", "trades.parquet", "skipped.parquet", "gate.json"):
        assert (tmp_path / name).exists()
    gate = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    assert gate["gate"] is None
    assert gate["source"] == "fixture"
    assert gate["sanity"]["S8"] == "not_run_待真实数据"


def test_write_report保留真实run与scenario(tmp_path):
    import pandas as pd

    base = _result()
    stress = _result()
    write_report(
        tmp_path,
        "run1",
        source="fixture",
        run_results={"reference": {"base": base, "stress": stress}},
        reference_metrics={"net_return_usdt": 0.993},
        sanity_results={"S1": "passed"},
        initial_equity=10.0,
        g2_bootstrap={"all": {"lower_bp": 1.0, "passed": True}},
    )
    trades = pd.read_parquet(tmp_path / "trades.parquet")
    # 每个情景各 1 笔，run 均为 reference，scenario 分别为 base/stress。
    assert set(trades["run"]) == {"reference"}
    assert list(trades["scenario"]) == ["base", "stress"]
    assert len(trades[trades["scenario"] == "base"]) == 1
    gate = json.loads((tmp_path / "gate.json").read_text(encoding="utf-8"))
    assert gate["g2_bootstrap"]["all"]["passed"] is True


def test_gate_payload():
    p = gate_payload("r", {"net_return_usdt": 1.0}, {"S1": "passed"}, source="fixture")
    assert p["run"] == "reference"
    assert p["gate"] is None
    assert p["g2_bootstrap"] is None


def test_experiment_hash对相同配置稳定且追踪参数与输入(tmp_path):
    signal_file = tmp_path / "signals.parquet"
    signal_file.write_bytes(b"signals-v1")
    input_files = {"signals": signal_file}
    configs = {"backtest": {"threshold": 0.5}}

    first = compute_experiment_hash(configs, input_files)
    assert compute_experiment_hash(configs, input_files) == first
    assert compute_experiment_hash({"backtest": {"threshold": 0.6}}, input_files) != first
    assert compute_experiment_hash({"run_window": {"final": True}}, input_files) != first

    signal_file.write_bytes(b"signals-v2")
    assert compute_experiment_hash(configs, input_files) != first


def test_experiments_log_path位于统一报告目录(tmp_path):
    assert experiments_log_path(tmp_path) == tmp_path / "reports" / "EXPERIMENTS.log"
