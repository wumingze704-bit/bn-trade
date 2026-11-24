"""引擎与研究框架口径一致（3 号第八部分 6d）。

规格：docs/07-实盘与风控.md §6、§7；docs/13-研究框架.md §3、§4
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from trade.data.universe import SymbolMeta
from trade.live.runner import Runner
from trade.live.store import Store
from trade.live.targets import TradeIntent
from trade.research import portfolio as research_portfolio
from trade.research.config import ExecutionConfig
from trade.research.data import HOUR_MS, Bars, MarketData, UniverseHistory
from trade.research.interface import Decision, Target
from trade.research.view import View


def test_组合层直接复用研究框架() -> None:
    """引擎不得另写组合层：调用的就是 `trade.research.portfolio.plan_positions`。"""
    from trade.live import runner as live_runner

    assert live_runner.plan_positions is research_portfolio.plan_positions


def test_模式B由研究组合层计算() -> None:
    """模式 B（vol_target）走研究框架的组合层，引擎只传 sizing 配置。"""
    start = 1_700_000_000_000
    bars = _bars(start, 120)
    rules = {
        "AAAUSDT": SymbolMeta(symbol="AAAUSDT", tick_size=0.01, step_size=0.1, min_notional=5.0)
    }
    market = MarketData(
        hourly={"AAAUSDT": bars},
        daily={"AAAUSDT": _daily_bars(start)},
        funding={},
        universe=UniverseHistory({"2023-11": _snapshot(start)}),
        rules=rules,
    )
    view = View(market, start + 119 * HOUR_MS)
    sizing = _sizing(sizing_mode="vol_target")
    planned, stats = research_portfolio.plan_positions(
        Decision(
            targets=(Target(symbol="AAAUSDT", side=1, sl=0.02, weight=1.0),), exposure="neutral"
        ),
        view=view,
        equity=sizing.initial_equity,
        available_margin=sizing.initial_equity * sizing.leverage,
        sizing_cfg=sizing,
        exec_cfg=_exec_cfg(),
        portfolio_cfg=_portfolio_cfg(),
        fee_taker=0.0005,
        blocked={},
        sizing_mode="vol_target",
    )
    assert planned or stats.dropped


def test_影子盘按决策整点K线开盘价成交(tmp_path: Path) -> None:
    """shadow 执行器在 open_time = 决策整点的 1h K 线开盘价成交（与研究框架一致）。"""
    store = Store(tmp_path / "state.db")
    start = 1_704_067_200_000  # 2024-01-01 00:00 UTC（整点对齐）

    bars = _bars(start, 3)
    rules = {
        "AAAUSDT": SymbolMeta(symbol="AAAUSDT", tick_size=0.01, step_size=0.1, min_notional=5.0)
    }
    market = MarketData(
        hourly={"AAAUSDT": bars},
        daily={"AAAUSDT": _daily_bars(start)},
        funding={},
        universe=UniverseHistory({"2023-11": _snapshot(start)}),
        rules=rules,
    )
    fills: list[dict[str, object]] = []
    runner = Runner.__new__(Runner)
    runner.mode = "shadow"
    runner.candidate = SimpleNamespace(candidate_id="C1")  # type: ignore[assignment]
    runner._store = store
    runner._feed = SimpleNamespace(hourly={"AAAUSDT": bars}, market=lambda: market)  # type: ignore[assignment]
    runner._exec_cfg = _exec_cfg()
    runner._fee_taker = 0.0005
    runner._pending_shadow = [(TradeIntent("AAAUSDT", 1.0, False), "", 0.01)]
    runner._pending_shadow_hour = start // HOUR_MS
    runner._top_symbols = lambda view: frozenset()  # type: ignore[assignment]
    runner._alert = _async_noop  # type: ignore[assignment]
    runner._apply_shadow_fill = (  # type: ignore[assignment]
        lambda intent, fill, sl, ts_ms: fills.append(
            {"ts": ts_ms, "price": fill.price, "open": float(bars.open[0])}
        )
    )
    try:
        asyncio.run(runner._settle_shadow(start // HOUR_MS + 1))
    finally:
        store.close()

    assert fills
    assert fills[0]["ts"] == start
    # 买入成交价 = 开盘价 + 半价差 + 滑点（大于开盘价）
    assert float(fills[0]["price"]) > float(fills[0]["open"])  # type: ignore[arg-type]


async def _async_noop(*args: object, **kwargs: object) -> None:
    return None


def _bars(start: int, count: int) -> Bars:
    open_time = np.array([start + i * HOUR_MS for i in range(count)], dtype=np.int64)
    base = 100.0 + np.arange(count, dtype=np.float64) * 0.1
    return Bars(
        symbol="AAAUSDT",
        open_time=open_time,
        open=base,
        high=base + 1.0,
        low=base - 1.0,
        close=base + 0.5,
        quote_volume=np.full(count, 1e7),
    )


def _daily_bars(start: int) -> Bars:
    day_ms = 86_400_000
    open_time = np.array([start - 90 * day_ms + i * day_ms for i in range(90)], dtype=np.int64)
    base = 100.0 + np.arange(90, dtype=np.float64) * 0.05
    return Bars(
        symbol="AAAUSDT",
        open_time=open_time,
        open=base,
        high=base + 1.0,
        low=base - 1.0,
        close=base + 0.3,
        quote_volume=np.full(90, 1e7),
    )


def _snapshot(start: int):  # type: ignore[no-untyped-def]
    from trade.data.universe import SnapshotCoin, UniverseSnapshot

    return UniverseSnapshot(
        month="2023-11",
        rebalance_ms=start,
        rule_hash="h",
        coins=[
            SnapshotCoin(
                symbol="AAAUSDT",
                alias="AAA",
                role="train",
                rank=1,
                daily_quote_median=1e7,
                first_listing_ms=1_600_000_000_000,
            )
        ],
        excluded_counts={},
    )


def _sizing(sizing_mode: str):  # type: ignore[no-untyped-def]
    from trade.research.config import load_research_sizing

    root = Path(__file__).resolve().parents[2]
    sizing = load_research_sizing(root / "configs" / "sizing_research.yaml").with_tier()
    return sizing.model_copy(update={"sizing_mode": sizing_mode})


def _exec_cfg() -> ExecutionConfig:
    from trade.research.config import load_research_config

    root = Path(__file__).resolve().parents[2]
    return load_research_config(root / "configs" / "research.yaml").execution


def _portfolio_cfg():  # type: ignore[no-untyped-def]
    from trade.research.config import load_research_config

    root = Path(__file__).resolve().parents[2]
    return load_research_config(root / "configs" / "research.yaml").portfolio
