"""看板测试共用 fixture。

构造最小回测报告产物（06 §8）供看板读取，不依赖真实 P4 产物。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.report import write_report


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """构造一份最小回测报告产物，返回 `artifacts/reports` 目录路径。"""
    trade = ExecutedTrade(
        symbol="ALT01USDT",
        group="train",
        signal_open_time=1_700_000_000_000,
        entry_open_time=1_700_000_060_000,
        exit_open_time=1_700_000_120_000,
        p=0.9,
        m=0.5,
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
    result = PortfolioResult(
        trades=[trade],
        equity_curve=pd.DataFrame(
            {
                "ts_ms": [1_700_000_000_000, 1_700_000_060_000, 1_700_000_120_000],
                "equity": [10.0, 10.5, 10.993],
                "drawdown": [0.0, 0.0, 0.0],
            }
        ),
    )
    reports = tmp_path / "reports"
    run_dir = reports / "run1"
    write_report(
        run_dir,
        "run1",
        source="fixture",
        run_results={"reference": {"base": result}},
        reference_metrics={"net_return_usdt": 0.993},
        sanity_results={"S1": "passed", "S8": "not_run"},
        initial_equity=10.0,
    )
    # 封存集运行标记（06 §8）
    (reports / "FINAL_RUNS.log").write_text(
        json.dumps({"ts": "2026-10-31T00:00:00Z", "run_id": "run1"}) + "\n",
        encoding="utf-8",
    )
    return reports
