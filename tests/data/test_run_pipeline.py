"""T1-9 任务清单 build_jobs 测试。

规格：docs/03-数据与选币.md §2.1
"""

from __future__ import annotations

from pathlib import Path

from scripts.run_pipeline import build_jobs
from trade.data.universe import SnapshotCoin, UniverseSnapshot, write_universe_snapshot


def _write(data_root: Path, month: str, *coins: SnapshotCoin) -> None:
    universe = data_root / "universe"
    universe.mkdir(parents=True, exist_ok=True)
    write_universe_snapshot(
        universe / f"{month}.json",
        UniverseSnapshot(month=month, rebalance_ms=0, rule_hash="h", coins=list(coins)),
    )


def _coin(symbol: str, alias: str) -> SnapshotCoin:
    return SnapshotCoin(
        symbol=symbol, alias=alias, role="train", rank=1, daily_quote_median=1.0, first_listing_ms=0
    )


def test_build_jobs_phase_and_warmup_and_priority(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    # M1~M4；tuning_months=2 → M1/M2 为 tuning，M4 为 holdout（holdout_months=1），M3 为 test
    months = ["2024-10", "2024-11", "2024-12", "2025-01"]
    _write(data_root, "2024-10", _coin("AAA", "C001"))
    _write(data_root, "2024-11", _coin("AAA", "C001"), _coin("BBB", "C002"))
    _write(data_root, "2024-12", _coin("BBB", "C002"))
    _write(data_root, "2025-01", _coin("CCC", "C003"))

    jobs = build_jobs(data_root, months, tuning_months=2, holdout_months=1, warmup_months=1)

    by_symbol: dict[str, list[tuple[str, str]]] = {}
    for symbol, _alias, month, phase in jobs:
        by_symbol.setdefault(symbol, []).append((month, phase))

    # AAA 入选 M1,M2：预热 M1 前 1 个月（2024-09），范围 2024-09~2024-11
    assert by_symbol["AAA"][0] == ("2024-09", "tuning")
    assert ("2024-10", "tuning") in by_symbol["AAA"]
    assert ("2024-11", "tuning") in by_symbol["AAA"]

    # CCC 只在 holdout 月（2025-01）入选：预热 2024-12（test 阶段）
    assert by_symbol["CCC"] == [("2024-12", "test"), ("2025-01", "holdout")]

    # 阶段顺序：tuning 在前，holdout 在后
    phases = [phase for _, _, _, phase in jobs]
    assert phases.index("tuning") < phases.index("test") < phases.index("holdout")

    # 阶段内按入选月数降序：AAA（2 个月）先于 BBB（2 个月，但 M1 起）
    aaa_first = next(i for i, (s, _, _, _) in enumerate(jobs) if s == "AAA")
    ccc_first = next(i for i, (s, _, _, _) in enumerate(jobs) if s == "CCC")
    assert aaa_first < ccc_first
