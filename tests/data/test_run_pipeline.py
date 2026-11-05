"""T1-9 任务清单 build_jobs 测试。

规格：docs/03-数据与选币.md §2.1
"""

from __future__ import annotations

from pathlib import Path

from scripts.run_pipeline import (
    _download_tasks,
    _previous_day,
    _previous_month,
    _run_batch,
    build_jobs,
)
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


def test_run_batch_收集失败但不中断其他任务() -> None:
    jobs = [
        ("AAA", "C001", "2024-10", "test"),
        ("BBB", "C002", "2024-10", "test"),
        ("CCC", "C003", "2024-10", "test"),
    ]
    calls: list[str] = []

    def process(symbol: str, alias: str, month: str) -> None:
        calls.append(symbol)
        if symbol == "BBB":
            raise RuntimeError("模拟单任务失败")

    failures = _run_batch(jobs, process, workers=2)

    # 03 §2.1：单文件失败只记录、不中断整批
    assert failures == [("BBB", "C002", "2024-10", "test")]
    assert sorted(calls) == ["AAA", "BBB", "CCC"]


def test_run_batch_批末重试只重跑失败任务并返回空清单() -> None:
    jobs = [
        ("AAA", "C001", "2024-10", "test"),
        ("BBB", "C002", "2024-10", "test"),
    ]
    attempts: dict[str, int] = {}

    def flaky(symbol: str, alias: str, month: str) -> None:
        attempts[symbol] = attempts.get(symbol, 0) + 1
        if symbol == "AAA" and attempts[symbol] == 1:
            raise RuntimeError("首次失败")

    first_pass = _run_batch(jobs, flaky, workers=2)
    assert first_pass == [("AAA", "C001", "2024-10", "test")]

    retry_pass = _run_batch(first_pass, flaky, workers=2)

    assert retry_pass == []
    # BBB 只在首轮执行一次，重试只覆盖失败任务
    assert attempts == {"AAA": 2, "BBB": 1}


def test_run_batch_空任务清单返回空() -> None:
    assert _run_batch([], lambda symbol, alias, month: None, workers=2) == []


def test_previous_month_and_day_跨年跨闰年() -> None:
    assert _previous_month("2025-01") == "2024-12"
    assert _previous_month("2025-03") == "2025-02"
    assert _previous_day("2025-01") == "2024-12-31"
    assert _previous_day("2024-03") == "2024-02-29"


def test_download_tasks_显式包含前一日p99与前一月收盘价(tmp_path: Path) -> None:
    tasks = _download_tasks("AAAUSDT", "C001", "2025-01", tmp_path)
    urls = [url for url, _dest in tasks]
    names = {dest.name for _url, dest in tasks}

    # 03 §2.1：首日 p99 需要前一日 aggTrades，月首价格回退需要上一自然月 1m K 线
    assert any(url.endswith("AAAUSDT-aggTrades-2024-12-31.zip") for url in urls)
    assert (
        "https://data.binance.vision/data/futures/um/monthly/klines/AAAUSDT/1m/"
        "AAAUSDT-1m-2024-12.zip"
    ) in urls
    assert "AAAUSDT-1m-2025-01.zip" in names
    # 每个文件只下载一次
    assert len(urls) == len(set(urls))
