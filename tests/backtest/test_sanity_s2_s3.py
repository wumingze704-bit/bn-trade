"""S2 标签打乱 / S3 特征平移判定与 gate 写入测试。

规格：docs/06-回测引擎.md §7 S2、S3；docs/08-开发计划与验收.md §3 G2
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from trade.backtest.report import write_report
from trade.backtest.sanity import s2_label_shuffle, s3_feature_shift


def test_s2_pf_at_or_below_limit_passes() -> None:
    assert s2_label_shuffle(1.05).startswith("passed")
    assert s2_label_shuffle(0.9).startswith("passed")


def test_s2_high_pf_fails() -> None:
    assert s2_label_shuffle(1.06).startswith("failed")


def test_s3_shifted_net_within_limit_passes() -> None:
    assert s3_feature_shift(10.0, 10.5, 1.0).startswith("passed")


def test_s3_shifted_net_above_limit_fails() -> None:
    assert s3_feature_shift(10.0, 11.5, 1.0).startswith("failed")


def test_gate_contains_s2_s3_and_signals_kind(tmp_path: Path) -> None:
    """gate.json 记录 S2/S3 状态与信号来源（R0 / 模型，T3-9 / T4-8）。"""
    from trade.backtest.engine import ExecutedTrade, PortfolioResult

    trade = ExecutedTrade(
        symbol="A",
        group="train",
        signal_open_time=1,
        entry_open_time=2,
        exit_open_time=3,
        p=0.9,
        m=0.0,
        p_fill=100.0,
        sl=0.01,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        notional=10.0,
        qty=0.1,
        margin=2.0,
        bars_held=1,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        gross_pnl=1.0,
        net_pnl=1.0,
        capped_by=None,
    )
    result = PortfolioResult(
        trades=[trade],
        equity_curve=pd.DataFrame(
            {"ts_ms": [1, 2], "equity": [10.0, 11.0], "drawdown": [0.0, 0.0]}
        ),
    )
    write_report(
        tmp_path / "run",
        "run",
        source="real",
        run_results={"reference": {"base": result}},
        reference_metrics={"net_return_usdt": 1.0},
        sanity_results={
            "S2": s2_label_shuffle(1.5),
            "S3": s3_feature_shift(10.0, 12.0, 1.0),
        },
        initial_equity=10.0,
        signals_kind="model",
    )
    gate = json.loads((tmp_path / "run" / "gate.json").read_text(encoding="utf-8"))
    assert gate["signals_kind"] == "model"
    assert gate["sanity"]["S2"].startswith("failed")
    assert gate["sanity"]["S3"].startswith("failed")
    # 图表与明细同时落地（06 §8）
    for name in ("equity.png", "monthly.png", "by_symbol.png", "p_calibration.png"):
        assert (tmp_path / "run" / name).is_file()
