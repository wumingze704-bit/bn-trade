"""S8 逐笔重放测试（06 §7 S8、§9 A5/A6）。

覆盖：同根 K 线内逐笔顺序决定止损/止盈先后、新止损生效前的 1 秒旧止损窗口、
跳空与补齐分钟、与 K 线级口径的一致性方向、S8 判定阈值与状态文本。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import PrecomputedTrade
from trade.backtest.replay import (
    BarsArrays,
    ReplayResult,
    S8Summary,
    TickArrays,
    compare_s8,
    relative_net_diff,
    replay_trade,
    s8_payload,
    s8_status_from_payload,
)
from trade.common.exits import ExitConfig, simulate_exit

_BASE_MS = 1_700_000_040_000 // 60_000 * 60_000
_FEES = FeeSchedule(maker=0.0002, taker=0.0005)
_SLIP_STOP = 0.0002


def _cfg(**kwargs) -> ExitConfig:
    defaults = dict(type="trail", tp_penetration=0.0001)
    defaults.update(kwargs)
    return ExitConfig(**defaults)


def _bars(
    p_1s: list[float],
    close: list[float],
    *,
    filled: list[int] | None = None,
    entry_price_col: str = "p_1s",
) -> BarsArrays:
    frame = pd.DataFrame(
        {
            "open_time": np.array([_BASE_MS + i * 60_000 for i in range(len(p_1s))], dtype="int64"),
            entry_price_col: np.array(p_1s, dtype="float64"),
            "close": np.array(close, dtype="float64"),
            "is_filled": np.array(filled or [0] * len(p_1s), dtype="int64"),
            "kline_missing": np.array(filled or [0] * len(p_1s), dtype="int64"),
            "aggtrades_missing": 0,
        }
    )
    return BarsArrays.from_frame(frame, entry_price_col)


def _trade(
    *,
    entry_idx: int = 1,
    p_fill: float = 100.0,
    sl: float = 0.01,
    bars_held: int = 1,
    exit_type: str = "initial_stop",
    exit_type_code: int = 0,
    gross_return: float = -0.01,
    exit_fee_rate: float = 0.0005,
    h_max: int = 120,
) -> PrecomputedTrade:
    return PrecomputedTrade(
        symbol="TESTUSDT",
        group="train",
        signal_open_time=_BASE_MS + (entry_idx - 1) * 60_000,
        entry_open_time=_BASE_MS + entry_idx * 60_000,
        p=1.0,
        m=0.0,
        p_fill=p_fill,
        sl=sl,
        loss_rate=sl,
        mean_qv_60=1e7,
        exit_open_time=_BASE_MS + (entry_idx + bars_held - 1) * 60_000,
        exit_price=p_fill * (1.0 + gross_return),
        exit_type=exit_type,
        exit_type_code=exit_type_code,
        bars_held=bars_held,
        stop_updates=0,
        mfe_r=0.0,
        mae_r=0.0,
        gross_return=gross_return,
        exit_fee_rate=exit_fee_rate,
        tick_size=0.01,
        step_size=1.0,
        min_notional=5.0,
        atr_abs=0.5,
        slip_stop=_SLIP_STOP,
        slip_exit=0.0001,
        half_spread=0.0001,
        notional_ref=1000.0,
        h_max=h_max,
    )


def _ticks(points: list[tuple[int, float]]) -> TickArrays:
    return TickArrays(
        time_ms=np.array([ms for ms, _ in points], dtype="int64"),
        price=np.array([price for _, price in points], dtype="float64"),
    )


def _replay(bars: BarsArrays, trade: PrecomputedTrade, ticks: TickArrays, cfg: ExitConfig):
    return replay_trade(
        bars,
        trade,
        ticks,
        exit_cfg=cfg,
        fees=_FEES,
        scen=scenario("base"),
    )


def test_同根K线内先到止损则按止损出场():
    bars = _bars([100.0, 100.0, 100.0], [100.0, 100.0, 100.0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),  # 入场 K 线（延迟 1 秒之后）
            (entry + 60_000 + 2_000, 96.0),  # 先跌到止损 99 之下
            (entry + 60_000 + 4_000, 104.5),  # 再涨到封顶止盈 104 之上
        ]
    )
    result = _replay(bars, trade, ticks, _cfg())
    assert result.exit_type == "initial_stop"
    assert result.exit_price == pytest.approx(96.0 * (1.0 - _SLIP_STOP))
    assert result.exit_time_ms == entry + 60_000 + 2_000


def test_同根K线内先到止盈则按止盈出场_K线级会保守记为止损():
    bars = _bars([100.0, 100.0, 100.0], [100.0, 100.0, 100.0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),
            (entry + 60_000 + 2_000, 104.5),
            (entry + 60_000 + 4_000, 96.0),
        ]
    )
    result = _replay(bars, trade, ticks, _cfg())
    assert result.exit_type == "tp"
    assert result.exit_price == pytest.approx(104.0)
    # K 线级同根同触发按止损处理（06 §3.3），两者出场类型不一致：A5 的量化对象。
    assert result.exit_type != trade.exit_type


def test_改单窗口内旧止损仍然有效():
    bars = _bars([100.0, 100.0, 100.0], [100.0, 101.2, 100.0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    bar2 = entry + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),
            (entry + 30_000, 101.2),  # 入场 K 线内达到 1R → 收盘把止损上移到 100.15
            (bar2 + 200, 100.05),  # 窗口内：低于新止损、高于旧止损 99.0 → 不应出场
            (bar2 + 1_500, 100.05),  # 窗口结束（+1 秒）后新止损生效 → 出场
        ]
    )
    result = _replay(bars, trade, ticks, _cfg())
    assert result.exit_type == "breakeven"
    assert result.exit_price == pytest.approx(100.05 * (1.0 - _SLIP_STOP))
    assert result.exit_time_ms == bar2 + 1_500
    assert result.old_stop_window_hit is False
    assert result.stop_updates == 1


def test_改单窗口内触发旧止损时按旧止损价出场():
    bars = _bars([100.0, 100.0, 100.0], [100.0, 101.2, 100.0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    bar2 = entry + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),
            (entry + 30_000, 101.2),
            (bar2 + 200, 98.5),  # 窗口内跌破旧止损 99.0 → 立即出场
            (bar2 + 3_000, 100.5),
        ]
    )
    result = _replay(bars, trade, ticks, _cfg())
    assert result.exit_type == "breakeven"
    assert result.exit_price == pytest.approx(98.5 * (1.0 - _SLIP_STOP))
    assert result.exit_time_ms == bar2 + 200
    assert result.old_stop_window_hit is True
    assert result.stop_updates == 0  # 新止损尚未生效就出场，不计一次改单


def test_跳空与补齐分钟不判定():
    bars = _bars([100.0, 100.0, 95.0, 92.0], [100.0, 100.0, 95.0, 92.0], filled=[0, 0, 1, 0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),
            (entry + 60_000 + 5_000, 95.0),  # 补齐分钟内的成交不判定（06 §3.3）
            (entry + 120_000 + 5_000, 90.0),  # 跳空后首笔成交触发止损
        ]
    )
    result = _replay(bars, trade, ticks, _cfg())
    assert result.exit_type == "initial_stop"
    assert result.exit_price == pytest.approx(90.0 * (1.0 - _SLIP_STOP))
    assert result.exit_time_ms == entry + 120_000 + 5_000


def test_逐笔级不比K线级乐观():
    cfg = _cfg()
    open_ = np.array([100.0, 100.0, 100.0], dtype="float64")
    high = np.array([100.0, 100.3, 100.0], dtype="float64")
    low = np.array([100.0, 99.5, 98.9], dtype="float64")
    close = np.array([100.0, 100.0, 98.9], dtype="float64")
    p_entry = np.array([100.0, 100.0, 100.0], dtype="float64")
    hi_post = np.array([100.0, 100.3, 100.0], dtype="float64")
    lo_post = np.array([100.0, 99.5, 98.9], dtype="float64")
    is_filled = np.zeros(3, dtype="int64")
    bar = simulate_exit(
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        is_filled,
        np.zeros_like(is_filled),
        t_entry=1,
        p_fill=100.0,
        sl=0.01,
        atr_abs=0.5,
        tick_size=0.01,
        H_max=cfg.H_max,
        cfg=cfg,
        slip_stop=_SLIP_STOP,
        slip_exit=0.0001,
        half_spread=0.0001,
        fee_taker=_FEES.taker,
        fee_maker=_FEES.maker,
    )
    bars = _bars([100.0, 100.0, 100.0], [100.0, 100.0, 98.9])
    entry = _BASE_MS + 60_000
    ticks = _ticks(
        [
            (entry + 2_000, 100.0),
            (entry + 10_000, 100.3),
            (entry + 60_000 + 10_000, 99.5),
            (entry + 60_000 + 20_000, 98.9),
        ]
    )
    result = _replay(bars, _trade(), ticks, cfg)
    assert result.exit_type == bar.exit_type == "initial_stop"
    assert result.exit_price <= bar.exit_price + 1e-9


def test_净收益差异分母接近零时改用绝对差():
    assert relative_net_diff(100.0, 110.0) == pytest.approx(0.10)
    assert relative_net_diff(0.0, 0.0) == 0.0
    assert relative_net_diff(1e-9, 1.0) == 1.0


def test_S8通过标准与状态文本可持久化():
    summary = S8Summary(
        month="2026-02",
        symbols=("XRPUSDT", "ARBUSDT", "STRKUSDT"),
        n_trades=40,
        bar_net_usdt=-100.0,
        tick_net_usdt=-110.0,
        net_diff=0.10,
        type_match_rate=0.95,
        old_stop_window_hits=2,
        per_symbol={},
    )
    assert summary.passed
    payload = s8_payload(summary, run_id="r1", selection_rule="规则")
    assert payload["passed"] is True
    status = s8_status_from_payload(payload)
    assert status.startswith("passed")
    assert "90%" in status


def test_逐笔重放汇总一致率与旧止损窗口计数():
    trade = _trade(exit_type="initial_stop", gross_return=-0.01)
    replay = ReplayResult(
        symbol="TESTUSDT",
        entry_open_time=trade.entry_open_time,
        exit_time_ms=trade.entry_open_time + 60_000,
        exit_price=trade.exit_price,
        exit_type="initial_stop",
        exit_type_code=0,
        stop_updates=1,
        bars_held=2,
        exit_fee_rate=_FEES.taker,
        entry_price_check_rel=0.0,
        old_stop_window_hit=True,
        n_ticks=10,
    )
    summary = compare_s8("2026-02", [(trade, replay)], entry_fee_rate=_FEES.taker)
    assert summary.n_trades == 1
    assert summary.type_match_rate == 1.0
    assert summary.old_stop_window_hits == 1
    assert summary.net_diff > 0.0
    assert summary.passed


def test_load_day_ticks_读取日度压缩包(tmp_path):
    import zipfile

    from trade.backtest.replay import load_day_ticks

    csv = (
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
        "1,100.0,1.0,1,1,1700000000000,true\n"
        "2,101.0,1.0,2,2,1700000000500,false\n"
    )
    path = tmp_path / "TESTUSDT-aggTrades-2023-11-14.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("TESTUSDT-aggTrades-2023-11-14.csv", csv)
    ticks = load_day_ticks(path)
    assert len(ticks) == 2
    assert list(ticks.price) == [100.0, 101.0]
    assert list(ticks.time_ms) == [1700000000000, 1700000000500]


def test_按月重放只装载需要的日期():
    from datetime import UTC, datetime

    from trade.backtest.replay import replay_month

    bars = _bars([100.0, 100.0, 100.0], [100.0, 100.0, 100.0])
    trade = _trade()
    entry = _BASE_MS + 60_000
    day = datetime.fromtimestamp(entry // 1000, tz=UTC).strftime("%Y-%m-%d")
    calls: list[str] = []

    def loader(name: str) -> TickArrays:
        calls.append(name)
        return _ticks([(entry + 2_000, 100.0), (entry + 62_000, 96.0)])

    pairs = replay_month(
        bars,
        [trade],
        tick_loader=loader,
        exit_cfg=_cfg(),
        fees=_FEES,
        scen=scenario("base"),
    )
    assert len(pairs) == 1
    assert pairs[0][0] is trade
    assert pairs[0][1].exit_type == "initial_stop"
    assert set(calls) == {day}


def test_S8结果回填gate_json(tmp_path):
    import json

    from trade.backtest.report import write_gate_s8

    gate_path = tmp_path / "gate.json"
    gate_path.write_text(
        json.dumps({"run_id": "r1", "sanity": {"S8": "not_run"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    gate = write_gate_s8(
        gate_path,
        {"status": "passed（净收益差异 1.0% < 15%）", "net_diff": 0.01, "passed": True},
    )
    assert isinstance(gate["sanity"], dict)
    assert gate["sanity"]["S8"].startswith("passed")
    assert isinstance(gate["s8"], dict)
    saved = json.loads(gate_path.read_text(encoding="utf-8"))
    assert saved["s8"]["passed"] is True
    assert saved["sanity"]["S8"].startswith("passed")


def test_回测脚本读取S8结果状态(tmp_path):
    import json

    from scripts.backtest import s8_status

    missing = s8_status(tmp_path / "none.json", "run1")
    assert missing.startswith("not_run")
    assert "scripts/s8_replay.py" in missing
    path = tmp_path / "s8_replay.json"
    path.write_text(json.dumps({"status": "passed（示例）"}, ensure_ascii=False), encoding="utf-8")
    assert s8_status(path, "run1") == "passed（示例）"


def _write_s8_fixture(tmp_path):
    """构造 S8 端到端 fixture：bars / signals / rules / 逐笔 zip / 快照 / base.yaml。"""
    import json
    import zipfile
    from datetime import UTC, datetime

    import yaml

    from trade.data.loader import ATOMIC_COLUMNS, KLINE_COLUMNS

    repo = __import__("pathlib").Path(__file__).resolve().parents[2]
    data_root = tmp_path / "data"
    (data_root / "bars" / "C001").mkdir(parents=True)
    (data_root / "universe").mkdir(parents=True)
    start = int(datetime(2026, 2, 1, tzinfo=UTC).timestamp() * 1000)
    n = 300
    open_time = np.array([start + i * 60_000 for i in range(n)], dtype="int64")
    price = np.full(n, 100.0)
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    # 第 202 根起跌破止损（入场 K 线是第 201 根）
    low[202:] = 96.0
    price[202:] = 96.0
    frame = pd.DataFrame(
        {
            "open_time": open_time,
            "open": price,
            "high": high,
            "low": low,
            "close": price,
            "volume": np.full(n, 10.0),
            "close_time": open_time + 59_999,
            "quote_volume": np.full(n, 1000.0),
            "count": np.full(n, 100, dtype="int64"),
            "taker_buy_volume": np.full(n, 5.0),
            "taker_buy_quote_volume": np.full(n, 500.0),
            "is_filled": np.zeros(n, dtype="int64"),
            "kline_missing": 0,
            "aggtrades_missing": 0,
        }
    )
    for column in ATOMIC_COLUMNS:
        frame[column] = 0.0
    frame["p_1s"] = price
    frame["p_3s"] = price
    frame["hi_post_1s"] = high
    frame["lo_post_1s"] = low
    frame["hi_post_3s"] = high
    frame["lo_post_3s"] = low
    frame["p_1s_filled"] = 0
    frame["p_3s_filled"] = 0
    missing = [c for c in (*KLINE_COLUMNS, *ATOMIC_COLUMNS, "is_filled") if c not in frame.columns]
    assert not missing
    frame.to_parquet(data_root / "bars" / "C001" / "202602.parquet", index=False)
    (data_root / "universe" / "aliases.json").write_text(
        json.dumps({"TESTUSDT": "C001"}), encoding="utf-8"
    )
    (data_root / "universe" / "2026-02.json").write_text(
        json.dumps(
            {
                "month": "2026-02",
                "rebalance_ms": start,
                "rule_hash": "h",
                "coins": [
                    {
                        "symbol": "TESTUSDT",
                        "alias": "C001",
                        "role": "train",
                        "rank": 1,
                        "daily_quote_median": 5e7,
                        "first_listing_ms": 0,
                        "price_median_30d": 100.0,
                        "tick_rel": 1e-4,
                        "step_notional": 1.0,
                        "max_leverage": None,
                    }
                ],
                "excluded_counts": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    signals = pd.DataFrame(
        {
            "symbol": ["C001"],
            "open_time": np.array([start + 200 * 60_000], dtype="int64"),
            "p": [1.0],
            "m": [0.0],
            "theta": [0.5],
        }
    )
    signals_path = tmp_path / "signals.parquet"
    signals.to_parquet(signals_path, index=False)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps({"C001": {"tick_size": 0.01, "step_size": 1.0, "min_notional": 5.0}}),
        encoding="utf-8",
    )
    raw_root = tmp_path / "raw"
    (raw_root / "aggtrades" / "C001").mkdir(parents=True)
    entry_ms = int(open_time[201])
    rows = [
        (1, 100.0, 10.0, entry_ms + 1_500),
        (2, 100.0, 10.0, entry_ms + 61_500),
        (3, 96.0, 10.0, entry_ms + 121_500),
    ]
    csv_lines = [
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
    ]
    csv_lines += [f"{i},{p},{q},{i},{i},{t},true" for i, p, q, t in rows]
    zip_path = raw_root / "aggtrades" / "C001" / "TESTUSDT-aggTrades-2026-02-01.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("TESTUSDT-aggTrades-2026-02-01.csv", "\n".join(csv_lines) + "\n")
    base_values = yaml.safe_load((repo / "configs" / "base.yaml").read_text(encoding="utf-8"))
    base_values["paths"] = {
        "data_root": str(data_root),
        "artifact_root": str(tmp_path / "artifacts"),
    }
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base_values, allow_unicode=True), encoding="utf-8")
    return (
        base_path,
        signals_path,
        rules_path,
        raw_root,
        tmp_path / "artifacts" / "reports" / "itest",
    )


def test_s8_replay_脚本端到端(tmp_path, monkeypatch, capsys):
    import json
    import sys

    from scripts import s8_replay

    base_path, signals_path, rules_path, raw_root, report_dir = _write_s8_fixture(tmp_path)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "gate.json").write_text(
        json.dumps({"run_id": "itest", "sanity": {"S8": "not_run"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "s8_replay.py",
            "--run-id",
            "itest",
            "--signals",
            str(signals_path),
            "--rules",
            str(rules_path),
            "--base",
            str(base_path),
            "--month",
            "2026-02",
            "--symbols",
            "TESTUSDT",
            "--raw-root",
            str(raw_root),
            "--report-dir",
            str(report_dir),
        ],
    )
    assert s8_replay.main() == 0
    payload = json.loads((report_dir / "s8_replay.json").read_text(encoding="utf-8"))
    assert payload["n_trades"] == 1
    assert payload["month"] == "2026-02"
    assert payload["symbols"] == ["TESTUSDT"]
    assert payload["net_diff_passed"] is True
    assert payload["type_match_rate"] == 1.0
    gate = json.loads((report_dir / "gate.json").read_text(encoding="utf-8"))
    assert gate["sanity"]["S8"] == payload["status"]
    assert gate["s8"]["n_trades"] == 1
    assert (report_dir / "s8_replay.parquet").is_file()
    out = capsys.readouterr().out
    assert "TESTUSDT" in out
