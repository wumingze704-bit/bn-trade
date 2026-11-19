"""轮动信号测试：打分、排序、大盘过滤与未来函数。"""

from __future__ import annotations

import numpy as np
import pytest

from tests.rotation.helpers import (
    EVAL_START_MS,
    PANEL_START_MS,
    make_config,
    make_panel,
    universe_month,
)
from trade.rotation.data import MS_DAY, MS_HOUR
from trade.rotation.signals import build_decision, build_schedule, compute_scores, market_filter


def _axis_len(days: int = 10) -> int:
    return int((EVAL_START_MS + days * MS_DAY - PANEL_START_MS) // MS_HOUR)


def _pad(closes: dict[str, np.ndarray], count: int = 5) -> dict[str, np.ndarray]:
    """补齐山寨币数量（等权指数至少需要 5 个有效币，11 §4.1）。"""
    n = next(iter(closes.values())).shape[0]
    out = dict(closes)
    index = 0
    while len([symbol for symbol in out if symbol != "BTCUSDT"]) < count:
        out[f"PAD{index}USDT"] = np.full(n, 10.0 + index)
        index += 1
    return out


def test_打分与显式公式一致() -> None:
    """score = L 天对数收益 ÷ L 天 1h 收益标准差，窗口截止到调仓时刻前 lag 小时。"""
    n = _axis_len()
    rng = np.random.default_rng(7)
    steps = rng.normal(0.0, 0.01, size=n - 1)
    close = 100.0 * np.exp(np.r_[0.0, np.cumsum(steps)])
    btc = 50000.0 * np.exp(np.r_[0.0, np.cumsum(steps * 0.5)])
    panel = make_panel(closes={"AAAUSDT": close, "BTCUSDT": btc}, atr={"AAAUSDT": 1.0})
    cfg = make_config(lookback_days=1)
    ts = EVAL_START_MS + 3 * MS_DAY
    scores = compute_scores(panel, ts, cfg, lag_hours=1, universe=frozenset({"AAAUSDT"}))
    # 显式手算：最后一根 K 线在 ts − 1h 收盘 → 下标为 hour(ts − 1h) − 1
    i_last = int((ts - MS_HOUR - PANEL_START_MS) // MS_HOUR) - 1
    i_first = i_last - 24
    window = close[i_first : i_last + 1]
    expected = float(np.log(window[-1] / window[0]) / np.std(np.diff(np.log(window)), ddof=1))
    assert scores["AAAUSDT"] == pytest.approx(expected, rel=1e-12)


def test_大盘过滤_btc在均线上下() -> None:
    n = _axis_len()
    cfg = make_config(ma_days=5)
    ts = EVAL_START_MS + 5 * MS_DAY
    i_f = int((ts - PANEL_START_MS) // MS_HOUR) - 1
    alt = np.full(n, 10.0)
    alt[i_f - 23 : i_f + 1] = 11.0
    btc_up = np.full(n, 100.0)
    btc_up[i_f] = 200.0  # 收盘 200 > 均线约 100.08
    btc_down = np.full(n, 200.0)
    btc_down[i_f] = 100.0  # 收盘 100 < 均线约 199.92
    panel_up = make_panel(closes=_pad({"BTCUSDT": btc_up, "AAAUSDT": alt}))
    panel_down = make_panel(closes=_pad({"BTCUSDT": btc_down, "AAAUSDT": alt}))
    universe = frozenset(panel_up.row) - {"BTCUSDT"}
    ok, note = market_filter(panel_up, ts, universe, cfg)
    assert ok is True and note == ""
    ok2, note2 = market_filter(panel_down, ts, universe, cfg)
    assert ok2 is False and note2 == "btc_below_ma"


def test_大盘过滤_山寨指数为负时关闭() -> None:
    n = _axis_len()
    cfg = make_config(ma_days=5)
    ts = EVAL_START_MS + 5 * MS_DAY
    i_f = int((ts - PANEL_START_MS) // MS_HOUR) - 1
    btc = np.full(n, 40000.0)
    btc[i_f] = 50000.0
    falling = np.full(n, 100.0)
    falling[i_f - 23 : i_f + 1] = 90.0  # alt_index_days=1 → 7 日收益按 24h 算
    rising = np.full(n, 100.0)
    rising[i_f - 23 : i_f + 1] = 110.0
    down_panel = make_panel(closes=_pad({"BTCUSDT": btc, "AAAUSDT": falling}))
    up_panel = make_panel(closes=_pad({"BTCUSDT": btc, "AAAUSDT": rising}))
    universe = frozenset(down_panel.row) - {"BTCUSDT"}
    ok, note = market_filter(down_panel, ts, universe, cfg)
    assert ok is False and note == "alt_index_negative"
    ok2, note2 = market_filter(up_panel, ts, universe, cfg)
    assert ok2 is True and note2 == ""


def test_排序与负分被剔除() -> None:
    n = _axis_len()
    cfg = make_config(lookback_days=1, top_k=1)
    ts = EVAL_START_MS + 8 * MS_DAY
    i_f = int((ts - PANEL_START_MS) // MS_HOUR) - 1
    up = np.full(n, 100.0)
    up[i_f - 23 : i_f + 1] = np.linspace(100.0, 130.0, 24)
    down = np.full(n, 100.0)
    down[i_f - 23 : i_f + 1] = np.linspace(100.0, 80.0, 24)
    btc = np.full(n, 40000.0)
    btc[i_f] = 50000.0
    panel = make_panel(closes=_pad({"BTCUSDT": btc, "AAAUSDT": up, "BBBUSDT": down}))
    decision = build_decision(
        panel,
        ts,
        universe_month=universe_month(frozenset(panel.row) - {"BTCUSDT"}),
        cfg=cfg,
        lag_hours=1,
    )
    assert decision.filter_on is True
    assert decision.rank["AAAUSDT"] == 1
    assert "BBBUSDT" not in decision.rank  # 负分被剔除
    assert "PAD0USDT" not in decision.rank  # 标准差为 0 被剔除


def test_未来函数_修改调仓时刻之后的数据不改变决策() -> None:
    n = _axis_len()
    rng = np.random.default_rng(11)
    steps = rng.normal(0.0, 0.02, size=n - 1)
    closes = {
        "AAAUSDT": 10.0 * np.exp(np.r_[0.0, np.cumsum(steps)]),
        "BBBUSDT": 20.0 * np.exp(np.r_[0.0, np.cumsum(-steps)]),
        "BTCUSDT": 50000.0 * np.exp(np.r_[0.0, np.cumsum(steps * 0.3)]),
    }
    padded = _pad(closes)
    cfg = make_config(lookback_days=2, top_k=2)
    ts = EVAL_START_MS + 6 * MS_DAY
    panel = make_panel(closes=padded)
    universe = universe_month(frozenset(panel.row) - {"BTCUSDT"})
    base = build_decision(panel, ts, universe_month=universe, cfg=cfg, lag_hours=1)
    # 把调仓时刻之后（含当小时）的小时收盘价全部改掉
    i_ts = panel.hour_index(ts)
    mutated = {key: value.copy() for key, value in padded.items()}
    for value in mutated.values():
        value[i_ts:] = value[i_ts:] * 3.0 + 7.0
    panel2 = make_panel(closes=mutated)
    after = build_decision(panel2, ts, universe_month=universe, cfg=cfg, lag_hours=1)
    assert after.filter_on == base.filter_on
    assert after.ranked == base.ranked
    assert after.rank == base.rank


def test_shuffle情景确定性() -> None:
    n = _axis_len()
    rng = np.random.default_rng(3)
    closes = {
        f"C{index}USDT": 10.0 * np.exp(np.r_[0.0, np.cumsum(rng.normal(0.01, 0.02, n - 1))])
        for index in range(6)
    }
    closes["BTCUSDT"] = np.full(n, 50000.0)
    panel = make_panel(closes=closes)
    cfg = make_config(lookback_days=2, top_k=2)
    universe = universe_month(frozenset(panel.row) - {"BTCUSDT"})
    ts = EVAL_START_MS + 6 * MS_DAY
    first = build_decision(
        panel, ts, universe_month=universe, cfg=cfg, lag_hours=1, shuffle_seed=42
    )
    second = build_decision(
        panel, ts, universe_month=universe, cfg=cfg, lag_hours=1, shuffle_seed=42
    )
    assert first.ranked == second.ranked
    plain = build_decision(panel, ts, universe_month=universe, cfg=cfg, lag_hours=1)
    assert [symbol for symbol, _ in plain.ranked] != [symbol for symbol, _ in first.ranked]


def test_资金费过滤只保留中位数以下() -> None:
    n = _axis_len()
    rng = np.random.default_rng(5)
    closes = {
        f"C{index}USDT": 10.0 * np.exp(np.r_[0.0, np.cumsum(rng.normal(0.001, 0.02, n - 1))])
        for index in range(6)
    }
    closes["BTCUSDT"] = np.full(n, 50000.0)
    funding = {symbol: np.full(n, 0.001 * (index + 1)) for index, symbol in enumerate(closes)}
    panel = make_panel(closes=closes, funding=funding)
    cfg = make_config(lookback_days=2, top_k=2, funding_filter=True)
    universe = universe_month(frozenset(panel.row) - {"BTCUSDT"})
    ts = EVAL_START_MS + 6 * MS_DAY
    decision = build_decision(panel, ts, universe_month=universe, cfg=cfg, lag_hours=1)
    # 中位数 = 0.0035（6 个币 0.001~0.006），只保留 0.001~0.003 三个币
    assert set(decision.rank) <= {"C0USDT", "C1USDT", "C2USDT", "BTCUSDT"}


def test_build_schedule只覆盖给定月份() -> None:
    n = _axis_len()
    closes = _pad({"BTCUSDT": np.full(n, 50000.0), "AAAUSDT": np.full(n, 10.0)})
    panel = make_panel(closes=closes)
    schedule = build_schedule(
        panel,
        timestamps=[EVAL_START_MS, EVAL_START_MS + 4 * MS_HOUR],
        universe_calendar={"2024-11": universe_month(frozenset(panel.row) - {"BTCUSDT"})},
        cfg=make_config(),
        lag_hours=1,
    )
    assert set(schedule) == {EVAL_START_MS, EVAL_START_MS + 4 * MS_HOUR}
