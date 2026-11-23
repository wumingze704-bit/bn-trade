"""滚动验证与阶段守卫测试（docs/13 §6、§8）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from trade.research.engine import Trade
from trade.research.rolling import Fold, ParamRun, fold_windows, select_for_fold
from trade.research.stage import StageError, enforce_final, enforce_research


def _month_ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def test_折切分连续覆盖训练期之后() -> None:
    folds = fold_windows(_month_ms(2024, 1), _month_ms(2026, 1), train_months=12, folds=4)
    assert len(folds) == 4
    assert folds[0].start_ms == _month_ms(2025, 1)
    assert folds[-1].end_ms == _month_ms(2026, 1)
    for left, right in zip(folds, folds[1:], strict=False):
        assert left.end_ms == right.start_ms


def _trade(ts_ms: int) -> Trade:
    return Trade(
        symbol="AAAUSDT",
        side=1,
        entry_ts=ts_ms,
        exit_ts=ts_ms + 3_600_000,
        notional=100.0,
        fees=0.1,
        funding=0.0,
        gross_pnl=0.2,
        net_pnl=0.1,
        reason="rebalance",
        entry_equity=100.0,
    )


def test_折内选参只用折开始之前的数据() -> None:
    """A 在前 12 个月夏普低、折内极高；B 折前略高、折内为 0 → 必须选 B。"""
    index = pd.date_range("2024-01-01", "2026-01-01", freq="D", tz="UTC")
    first_fold = Fold(index=0, start_ms=_month_ms(2025, 1), end_ms=_month_ms(2025, 7))
    before = index < pd.Timestamp(first_fold.start_ms, unit="ms", tz="UTC")
    alternating = pd.Series([1.0 if i % 2 == 0 else -1.0 for i in range(len(index))], index=index)
    a_returns = 0.0001 + 0.001 * alternating
    b_returns = 0.0006 + 0.001 * alternating
    a_returns[~before] = 0.05
    b_returns[~before] = 0.0
    pre_trades = [_trade(int(ts.timestamp() * 1000)) for ts in index[:60]]
    runs = {
        "A": ParamRun(
            key="A",
            params={},
            daily=a_returns,
            trades=pre_trades,
            turnover=0.0,
            dropped=0,
            forced=0,
            cost_total=0.0,
            funding_total=0.0,
            long_gross=0.0,
            short_gross=0.0,
            risk_events=0,
        ),
        "B": ParamRun(
            key="B",
            params={},
            daily=b_returns,
            trades=pre_trades,
            turnover=0.0,
            dropped=0,
            forced=0,
            cost_total=0.0,
            funding_total=0.0,
            long_gross=0.0,
            short_gross=0.0,
            risk_events=0,
        ),
    }
    choice = select_for_fold(runs, first_fold, _month_ms(2024, 1))
    assert choice.params_key == "B"


def _ledger(tmp_path, *, status: str = "unused", available_after: str = "2026-12-03") -> Path:
    path = tmp_path / "ledger.yaml"
    path.write_text(
        "\n".join(
            [
                'research_start: "2021-01-01"',
                'research_end: "2026-07-31"',
                "final_windows:",
                '  - {id: W1, start: "2026-08-01", end: "2026-11-30", '
                f'available_after: "{available_after}", status: {status}, candidate: C1}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_研究阶段区间越界报错(tmp_path) -> None:
    path = _ledger(tmp_path)
    with pytest.raises(StageError):
        enforce_research(ledger_path=path, start_ms=_month_ms(2020, 12), end_ms=_month_ms(2026, 8))
    start, end = enforce_research(
        ledger_path=path, start_ms=_month_ms(2021, 1), end_ms=_month_ms(2026, 8)
    )
    assert start == _month_ms(2021, 1)
    assert end == _month_ms(2026, 8)


def test_最终检验只在可用后运行且不得重跑(tmp_path) -> None:
    path = _ledger(tmp_path, status="running")
    final = tmp_path / "C1_W1.md"
    with pytest.raises(StageError):
        enforce_final(
            ledger_path=path,
            candidate="C1",
            window="W1",
            final_path=final,
            now_ms=_month_ms(2026, 12),
        )
    start, _ = enforce_final(
        ledger_path=path,
        candidate="C1",
        window="W1",
        final_path=final,
        now_ms=_month_ms(2026, 12) + 3 * 86_400_000,
    )
    assert start == _month_ms(2026, 8)
    final.write_text("结论：PASS\n", encoding="utf-8")
    with pytest.raises(StageError):
        enforce_final(
            ledger_path=path,
            candidate="C1",
            window="W1",
            final_path=final,
            now_ms=_month_ms(2026, 12) + 3 * 86_400_000,
        )


def test_非running窗口拒绝检验(tmp_path) -> None:
    path = _ledger(tmp_path, status="assigned")
    with pytest.raises(StageError):
        enforce_final(
            ledger_path=path,
            candidate="C1",
            window="W1",
            final_path=tmp_path / "C1_W1.md",
            now_ms=_month_ms(2027, 1),
        )
