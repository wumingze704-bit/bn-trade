"""研究框架测试用的小型市场构造（手工 fixture，不依赖真实数据）。"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from trade.data.universe import SnapshotCoin, SymbolMeta, UniverseSnapshot
from trade.research.config import ExecutionConfig, PortfolioConfig, QualityConfig, ResearchConfig
from trade.research.data import DAY_MS, HOUR_MS, Bars, FundingSeries, MarketData, UniverseHistory

MONTH = "2024-01"
START_MS = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)


def make_bars(
    symbol: str,
    *,
    n: int = 240,
    start_ms: int = START_MS,
    price: float = 100.0,
    drift: float = 0.0,
    amplitude: float = 0.01,
    volume: float = 5_000_000.0,
) -> Bars:
    """构造逐小时 K 线：开盘价按 drift 缓慢变化，高低价在开盘价上下 amplitude。"""
    times = start_ms + np.arange(n, dtype=np.int64) * HOUR_MS
    trend = price * (1.0 + drift) ** np.arange(n, dtype="float64")
    opens = trend
    closes = trend * (1.0 + drift)
    highs = np.maximum(opens, closes) * (1.0 + amplitude)
    lows = np.minimum(opens, closes) * (1.0 - amplitude)
    return Bars(
        symbol=symbol,
        open_time=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        quote_volume=np.full(n, volume, dtype="float64"),
    )


def make_daily_bars(symbol: str, *, n: int = 40, price: float = 100.0) -> Bars:
    """构造日线（用于 30 天成交额中位数与日线 ATR）。"""
    times = START_MS + np.arange(n, dtype=np.int64) * DAY_MS
    closes = np.full(n, price, dtype="float64")
    return Bars(
        symbol=symbol,
        open_time=times,
        open=closes,
        high=closes * 1.01,
        low=closes * 0.99,
        close=closes,
        quote_volume=np.full(n, 1e8, dtype="float64"),
    )


def make_market(
    symbols: tuple[str, ...] = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"),
    *,
    n: int = 240,
    drift_by_symbol: dict[str, float] | None = None,
    funding_by_symbol: dict[str, float] | None = None,
) -> MarketData:
    """构造 4 个山寨币 + BTC/ETH 的小型市场。"""
    drift = drift_by_symbol or {}
    funding = funding_by_symbol or {}
    hourly = {
        symbol: make_bars(symbol, n=n, drift=drift.get(symbol, 0.0))
        for symbol in (*symbols, "BTCUSDT", "ETHUSDT")
    }
    daily = {symbol: make_daily_bars(symbol) for symbol in hourly}
    funding_series = {
        symbol: FundingSeries(
            symbol=symbol,
            calc_time=np.asarray([START_MS + i * 8 * HOUR_MS for i in range(180)], dtype="int64"),
            hourly_rate=np.full(180, rate, dtype="float64"),
            interval_hours=np.full(180, 8.0, dtype="float64"),
        )
        for symbol, rate in funding.items()
    }
    snapshot_coins = [
        SnapshotCoin(
            symbol=symbol,
            alias=f"C{index:03d}",
            role="train",
            rank=index,
            daily_quote_median=1e9,
            first_listing_ms=START_MS - 400 * DAY_MS,
        )
        for index, symbol in enumerate(symbols, start=1)
    ]
    snapshots = {}
    for month_index in range(18):
        year = 2024 + (month_index) // 12
        month = 1 + (month_index % 12)
        key = f"{year:04d}-{month:02d}"
        snapshots[key] = UniverseSnapshot(
            month=key,
            rebalance_ms=int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000),
            rule_hash="test",
            coins=snapshot_coins,
            excluded_counts={},
        )
    rules = {
        symbol: SymbolMeta(
            symbol=symbol,
            tick_size=0.01,
            step_size=0.001,
            min_notional=5.0,
            max_leverage=10,
        )
        for symbol in hourly
    }
    return MarketData(
        hourly=hourly,
        daily=daily,
        funding=funding_series,
        universe=UniverseHistory(snapshots),
        rules=rules,
    )


def make_research_config() -> ResearchConfig:
    """测试用研究配置（与 configs/research.yaml 同结构）。"""
    return ResearchConfig(
        paths={
            "klines_1h": "data/klines_1h",
            "klines_1d": "data/klines_1d",
            "funding": "data/funding",
            "universe_hist": "data/universe_hist",
            "research_cache": "artifacts/research_cache",
            "experiments_log": "artifacts/reports/EXPERIMENTS.log",
            "artifacts": "artifacts",
            "data_root": "data",
        },
        rolling={"train_months": 12, "folds": 5},
        bootstrap={"iters": 100, "seed": 42},
        execution={
            "half_spread_top_n": 20,
            "half_spread_top_bp": 1.0,
            "half_spread_other_bp": 3.0,
            "slip_min_bp": 1.0,
            "impact_y": 1.0,
            "impact_vol_hours": 24,
            "stop_slip_mult": 2.0,
            "cooldown_hours": 24,
            "delist_penalty": 0.01,
            "stop_max": 0.15,
        },
        portfolio={
            "total_notional_cap": 3.0,
            "net_exposure_cap": 1.5,
            "margin_cap": 0.95,
            "neutral_max_gap": 0.10,
            "liq_cap_hours": 24,
            "max_positions": 10,
        },
        quality={"missing_ratio_max": 0.01},
        risk={"halt_mode": "reset", "halt_reset_days": 7},
        equity={"signal": 10_000.0, "exec": 500.0},
        scenarios=["base"],
    )


def execution_config(**overrides: float) -> ExecutionConfig:
    values = dict(make_research_config().execution.model_dump())
    values.update(overrides)
    return ExecutionConfig(**values)


def portfolio_config(**overrides: float) -> PortfolioConfig:
    values = dict(make_research_config().portfolio.model_dump())
    values.update(overrides)
    return PortfolioConfig(**values)


def quality_config() -> QualityConfig:
    return QualityConfig(missing_ratio_max=0.01)
