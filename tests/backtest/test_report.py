"""报告产物与 gate.json 测试（06 §8）。"""

from __future__ import annotations

import json

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.report import gate_payload, write_report


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
        scenario_results={"base": result},
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


def test_gate_payload():
    p = gate_payload("r", {"net_return_usdt": 1.0}, {"S1": "passed"}, source="fixture")
    assert p["run"] == "reference"
    assert p["gate"] is None
