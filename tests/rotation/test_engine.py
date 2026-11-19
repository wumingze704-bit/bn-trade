"""轮动组合模拟测试：成交价、资金费、止损、缓冲区与整点卖出检查。"""

from __future__ import annotations

import numpy as np
import pytest

from tests.rotation.helpers import (
    EVAL_START_MS,
    axis_len,
    make_chunk,
    make_config,
    make_context,
    make_panel,
    run_chunk,
    universe_month,
)
from trade.rotation.data import MS_HOUR
from trade.rotation.signals import Decision

_HALF = 0.0001  # 半价差最小值（backtest.yaml costs.half_spread_min）
_FEE = 0.0005  # 吃单费率（base.yaml fees.taker）


def _panel(*, atr: float = 0.01, days: int = 10):
    n = axis_len(days)
    return make_panel(
        closes={
            "AAAUSDT": np.full(n, 1.0),
            "BBBUSDT": np.full(n, 1.0),
            "CCCUSDT": np.full(n, 1.0),
            "BTCUSDT": np.full(n, 50000.0),
        },
        atr={"AAAUSDT": atr, "BBBUSDT": atr, "CCCUSDT": atr},
    )


def _decision(ts_ms, ranked, *, filter_on=True, universe=("AAAUSDT", "BBBUSDT", "CCCUSDT")):
    return Decision(
        ts_ms=ts_ms,
        filter_on=filter_on,
        ranked=tuple(ranked),
        rank={symbol: index + 1 for index, (symbol, _) in enumerate(ranked)},
        universe=frozenset(universe),
    )


def test_成交价与资金费与手算一致() -> None:
    cfg = make_config(top_k=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    calendar = {"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))}
    schedule = {
        EVAL_START_MS: _decision(EVAL_START_MS, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
        EVAL_START_MS + 4 * MS_HOUR: _decision(
            EVAL_START_MS + 4 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]
        ),
        EVAL_START_MS + 8 * MS_HOUR: _decision(
            EVAL_START_MS + 8 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]
        ),
        EVAL_START_MS + 12 * MS_HOUR: _decision(
            EVAL_START_MS + 12 * MS_HOUR,
            [("BBBUSDT", 3.0), ("CCCUSDT", 2.0), ("AAAUSDT", 1.0)],
        ),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar=calendar,
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    assert len(portfolio.trades) == 1
    trade = portfolio.trades[0]
    entry_price = 1.0 * (1.0 + _HALF)
    exit_price = 1.0 * (1.0 - _HALF)
    assert trade.entry_ts_ms == EVAL_START_MS
    assert trade.exit_ts_ms == EVAL_START_MS + 12 * MS_HOUR
    assert trade.entry_price == pytest.approx(entry_price)
    assert trade.exit_price == pytest.approx(exit_price)
    stop_pct = min(cfg.stop_atr_mult * 0.01 / entry_price, cfg.stop_max_pct)
    assert trade.stop_pct == pytest.approx(stop_pct)
    notional = trade.qty * trade.entry_price
    assert trade.notional == pytest.approx(notional)
    assert trade.entry_fee == pytest.approx(notional * _FEE)
    funding = notional * 0.001  # 04:00 UTC 后的首个结算（helpers 设 08:00）
    assert trade.funding == pytest.approx(funding)
    expected_net = (
        trade.qty * (exit_price - entry_price) - trade.entry_fee - trade.exit_fee - funding
    )
    assert trade.net_pnl == pytest.approx(expected_net)
    assert trade.exit_reason == "rank_out"


def test_止损距离被15pct硬上限截断并触发止损() -> None:
    cfg = make_config(top_k=1)
    panel = _panel(atr=0.06)  # 3 × 0.06 ÷ 1.0001 > 15% → 截断到 15%
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    hit = 30
    chunk.low[chunk.row["AAAUSDT"], hit] = 0.80
    calendar = {"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))}
    schedule = {
        EVAL_START_MS: _decision(EVAL_START_MS, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
        EVAL_START_MS + 4 * MS_HOUR: _decision(
            EVAL_START_MS + 4 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]
        ),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar=calendar,
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    trade = portfolio.trades[0]
    assert trade.stop_pct == pytest.approx(0.15)
    assert trade.exit_reason == "stop"
    assert trade.exit_ts_ms == EVAL_START_MS + hit * 60_000
    # 止损价 = 入场价 × 0.85，滑点为 0 → 成交价等于止损价
    assert trade.exit_price == pytest.approx(trade.entry_price * 0.85)
    # 止损后 24 小时内不再买入
    assert portfolio.skipped.get("cooldown", 0) >= 1


def test_跳空时按开盘价成交() -> None:
    cfg = make_config(top_k=1)
    panel = _panel(atr=0.06)
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    hit = 30
    chunk.low[chunk.row["AAAUSDT"], hit] = 0.70
    chunk.open[chunk.row["AAAUSDT"], hit] = 0.72
    calendar = {"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))}
    schedule = {
        EVAL_START_MS: _decision(EVAL_START_MS, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar=calendar,
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    trade = portfolio.trades[0]
    assert trade.exit_reason == "stop"
    # 跳空：开盘价 0.72 已低于止损价 → 按开盘价成交（滑点为 0）
    assert trade.exit_price == pytest.approx(0.72)


def test_缓冲区_前2K名继续持有_掉出前2K名卖出() -> None:
    cfg = make_config(top_k=2, buffer_mult=2)  # buffer_k = 4
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0), "CCCUSDT": (1.0, 0.0)})
    calendar = {"2024-11": universe_month(("AAAUSDT", "BBBUSDT", "CCCUSDT"))}
    t0 = EVAL_START_MS
    schedule = {
        t0: _decision(t0, [("AAAUSDT", 4.0), ("BBBUSDT", 3.0), ("CCCUSDT", 2.0)]),
        t0 + 4 * MS_HOUR: _decision(
            t0 + 4 * MS_HOUR, [("CCCUSDT", 9.0), ("AAAUSDT", 5.0), ("BBBUSDT", 4.0)]
        ),
        t0 + 8 * MS_HOUR: _decision(
            t0 + 8 * MS_HOUR, [("CCCUSDT", 9.0), ("BBBUSDT", 8.0), ("AAAUSDT", 7.0)]
        ),
    }
    # 04:00 决策：AAA=2、BBB=3（均在前 4 名内）→ 继续持有；CCC=1 但已满 2 仓 → 不买
    # 08:00 决策：AAA 排第 6 → 掉出前 4 名 → 卖出
    calendar_decision = dict(schedule)
    calendar_decision[t0 + 4 * MS_HOUR] = _decision(
        t0 + 4 * MS_HOUR,
        [
            ("CCCUSDT", 9.0),
            ("AAAUSDT", 5.0),
            ("XXXXUSDT", 4.5),
            ("YYYYUSDT", 4.2),
            ("BBBUSDT", 4.0),
        ],
    )
    calendar_decision[t0 + 8 * MS_HOUR] = _decision(
        t0 + 8 * MS_HOUR,
        [
            ("CCCUSDT", 9.0),
            ("BBBUSDT", 8.0),
            ("XXXXUSDT", 7.5),
            ("YYYYUSDT", 7.2),
            ("AAAUSDT", 7.0),
        ],
    )
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar=calendar,
        symbols={"AAAUSDT": {}, "BBBUSDT": {}, "CCCUSDT": {}},
        schedules={"base": calendar_decision},
    )
    portfolio = run_chunk(ctx, chunk)
    # 04:00 不卖出（AAA 第 2、BBB 第 5 名 → BBB 掉出前 4 名，会被卖出）
    exits = {trade.symbol: trade.exit_ts_ms for trade in portfolio.trades}
    assert exits["BBBUSDT"] == t0 + 4 * MS_HOUR
    assert exits["AAAUSDT"] == t0 + 8 * MS_HOUR


def test_掉出U_R时卖出() -> None:
    cfg = make_config(top_k=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    t0 = EVAL_START_MS
    schedule = {
        t0: _decision(t0, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
        t0 + 4 * MS_HOUR: _decision(
            t0 + 4 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)], universe=("BBBUSDT",)
        ),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar={"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))},
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    assert portfolio.trades[0].exit_reason == "universe"
    assert portfolio.trades[0].exit_ts_ms == t0 + 4 * MS_HOUR


def test_大盘过滤失效时全部卖出且不买入() -> None:
    cfg = make_config(top_k=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    t0 = EVAL_START_MS
    schedule = {
        t0: _decision(t0, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
        t0 + 4 * MS_HOUR: _decision(
            t0 + 4 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)], filter_on=False
        ),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar={"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))},
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].exit_reason == "filter_off"
    assert portfolio.filter_off == 1


def test_exit_check_hours整点只卖不买() -> None:
    cfg = make_config(top_k=1, exit_check_hours=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    t0 = EVAL_START_MS
    schedule = {t0: _decision(t0, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)])}
    exit_schedule = {
        t0 + 1 * MS_HOUR: _decision(
            t0 + 1 * MS_HOUR,
            [("BBBUSDT", 9.0), ("CCCUSDT", 8.0), ("AAAUSDT", 7.0)],
        )
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar={"2024-11": universe_month(("AAAUSDT", "BBBUSDT", "CCCUSDT"))},
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
        exit_schedules={"base": exit_schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].exit_ts_ms == t0 + 1 * MS_HOUR
    assert portfolio.trades[0].exit_reason == "rank_out"


def test_exit_check_hours不在整点买入() -> None:
    cfg = make_config(top_k=1, exit_check_hours=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    t0 = EVAL_START_MS
    schedule = {
        t0: _decision(t0, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)], filter_on=False),
        t0 + 4 * MS_HOUR: _decision(
            t0 + 4 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)], filter_on=False
        ),
    }
    exit_schedule = {
        t0 + 1 * MS_HOUR: _decision(t0 + 1 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
        t0 + 2 * MS_HOUR: _decision(t0 + 2 * MS_HOUR, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)]),
    }
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar={"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))},
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": schedule},
        exit_schedules={"base": exit_schedule},
    )
    portfolio = run_chunk(ctx, chunk)
    assert portfolio.trades == []


def test_新月份无数据时按最后价格平仓() -> None:
    """持仓币在新月份没有任何分钟数据时按上一可得价格平仓（11 §5.2 data_end）。"""
    from trade.rotation.engine import Portfolio, scenario_by_name

    cfg = make_config(top_k=1)
    panel = _panel()
    chunk = make_chunk(prices={"AAAUSDT": (1.0, 0.0), "BBBUSDT": (1.0, 0.0)})
    t0 = EVAL_START_MS
    ctx = make_context(
        panel=panel,
        cfg=cfg,
        calendar={"2024-11": universe_month(("AAAUSDT", "BBBUSDT"))},
        symbols={"AAAUSDT": {}, "BBBUSDT": {}},
        schedules={"base": {t0: _decision(t0, [("AAAUSDT", 2.0), ("BBBUSDT", 1.0)])}},
    )
    portfolio = Portfolio(scenario_by_name("base"), ctx)
    portfolio.qv_history = {symbol: np.full(60, 1e6) for symbol in chunk.row}
    portfolio.start_month(chunk)
    portfolio.step(t0, chunk, 0)
    assert "AAAUSDT" in portfolio.positions
    next_chunk = make_chunk(prices={"BBBUSDT": (1.0, 0.0)})
    portfolio.start_month(next_chunk)
    exits = [trade for trade in portfolio.trades if trade.exit_reason == "data_end"]
    assert len(exits) == 1
    assert exits[0].symbol == "AAAUSDT"
    assert exits[0].exit_price < exits[0].exit_p1s  # 扣了卖出半价差
