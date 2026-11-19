"""轮动策略测试的合成数据构造（3 币 × 10 天，手工可核对）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from trade.rotation.config import RotationConfig
from trade.rotation.data import (
    MS_4H,
    MS_DAY,
    MS_HOUR,
    MS_MIN,
    HourlyPanel,
    MinuteChunk,
    SymbolSeries,
    UniverseMonth,
)
from trade.rotation.engine import Portfolio, SimContext

# 2024-11-01 00:00 UTC
EVAL_START_MS = 1730419200000
# 面板起点：评估起点前 6 天（足够 ma_days=5 与 7 日指数回看）
PANEL_START_MS = EVAL_START_MS - 8 * MS_DAY
DAYS = 10


def axis_len(days: int = DAYS) -> int:
    """面板整点轴长度（预热 8 天 + 评估 days 天）。"""
    return int((EVAL_START_MS + days * MS_DAY - PANEL_START_MS) // MS_HOUR)


def hour_ms_axis(days: int = DAYS) -> np.ndarray:
    """评估窗口的整点轴（不含预热）。"""
    return np.arange(EVAL_START_MS, EVAL_START_MS + days * MS_DAY, MS_HOUR, dtype="int64")


def minute_axis(days: int = DAYS) -> np.ndarray:
    """评估窗口的分钟轴。"""
    return np.arange(EVAL_START_MS, EVAL_START_MS + days * MS_DAY, MS_MIN, dtype="int64")


def make_config(**kwargs: object) -> RotationConfig:
    """测试用轮动配置（参数小、便于手算）。"""
    params: dict[str, object] = {
        "ma_days": 5,
        "lookback_days": 1,
        "top_k": 2,
        "buffer_mult": 2,
        "rebalance_hours": 4,
        "score_lag_hours": 1,
        "alt_index_days": 1,
        "min_score": 0.0,
        "stop_atr_period": 2,
        "stop_atr_mult": 3.0,
        "stop_max_pct": 0.15,
        "stop_cooldown_hours": 24,
        "exit_check_hours": 0,
        "funding_filter": False,
        "oi_bonus": 0.0,
    }
    params.update(kwargs)
    return RotationConfig(**params)  # type: ignore[arg-type]


def make_panel(
    *,
    closes: dict[str, np.ndarray],
    btc: str = "BTCUSDT",
    atr: dict[str, float] | None = None,
    oi: dict[str, np.ndarray] | None = None,
    funding: dict[str, np.ndarray] | None = None,
    qv: float = 1e6,
) -> HourlyPanel:
    """由各币小时收盘价构造面板（ATR 用常量，便于手算止损距离）。"""
    symbols = tuple(closes)
    hour_ms = np.arange(PANEL_START_MS, EVAL_START_MS + DAYS * MS_DAY, MS_HOUR, dtype="int64")
    n = hour_ms.shape[0]
    close = np.vstack([np.asarray(closes[s], dtype="float64") for s in symbols])
    if close.shape[1] != n:
        raise ValueError("收盘价长度必须等于面板整点轴长度")
    row = {s: i for i, s in enumerate(symbols)}
    series = {}
    for s in symbols:
        constant_atr = (atr or {}).get(s, 0.0)
        series[s] = SymbolSeries(
            symbol=s,
            alias=s,
            atr_ms=np.arange(PANEL_START_MS, EVAL_START_MS + DAYS * MS_DAY, MS_4H, dtype="int64"),
            atr=np.full(
                np.arange(PANEL_START_MS, EVAL_START_MS + DAYS * MS_DAY, MS_4H).shape[0],
                constant_atr,
            ),
            funding_times=np.array([EVAL_START_MS + 8 * MS_HOUR], dtype="int64"),
            funding_rates=np.array([0.001], dtype="float64"),
        )
    return HourlyPanel(
        hour_ms=hour_ms,
        symbols=symbols,
        row=row,
        close=close,
        qv=np.full((len(symbols), n), qv),
        oi=np.vstack([np.asarray((oi or {}).get(s, np.ones(n))) for s in symbols]),
        funding=np.vstack([np.asarray((funding or {}).get(s, np.zeros(n))) for s in symbols]),
        series=series,
        btc_row=row[btc],
    )


def make_chunk(
    *,
    prices: dict[str, tuple[float, float]],
    days: int = DAYS,
    spread_low: dict[str, float] | None = None,
) -> MinuteChunk:
    """构造分钟块：每个币恒定开高低收与 p_1s。

    Args:
        prices: symbol -> (price, low_offset)；low_offset 为最低价相对价格的跌幅。
        days: 天数。
        spread_low: symbol -> 指定最低价（覆盖 low_offset）。
    """
    minute_ms = minute_axis(days)
    n = minute_ms.shape[0]
    row = {s: i for i, s in enumerate(prices)}
    low = np.full((len(prices), n), np.nan)
    open_ = np.full((len(prices), n), np.nan)
    close = np.full((len(prices), n), np.nan)
    p1s = np.full((len(prices), n), np.nan)
    qv = np.full((len(prices), n), np.nan)
    for s, (price, low_offset) in prices.items():
        r = row[s]
        open_[r, :] = price
        close[r, :] = price
        p1s[r, :] = price
        low[r, :] = (spread_low or {}).get(s, price * (1.0 - low_offset))
        qv[r, :] = 1e6
    return MinuteChunk(
        month="2024-11",
        minute_ms=minute_ms,
        row=row,
        low=low,
        open=open_,
        close=close,
        p1s=p1s,
        qv=qv,
    )


def universe_month(
    symbols: tuple[str, ...], *, unlock: dict[str, int] | None = None
) -> UniverseMonth:
    """构造当月交易范围。"""
    return UniverseMonth(
        month="2024-11",
        symbols=frozenset(symbols),
        unlock_ms=dict(unlock or {}),
    )


def make_context(
    *,
    panel: HourlyPanel,
    cfg: RotationConfig,
    calendar: dict[str, UniverseMonth],
    symbols: dict[str, dict[str, float]],
    schedules: dict[str, dict[int, object]] | None = None,
    exit_schedules: dict[str, dict[int, object]] | None = None,
    eval_end_ms: int | None = None,
) -> SimContext:
    """构造运行上下文：成本只剩半价差、滑点为 0，便于手算成交价。"""
    from trade.common.sizing import SizingConfig
    from trade.rotation.engine import CostModel, SymbolRules, build_risk_limits

    sizing = SizingConfig(
        initial_equity=100.0,
        mode="fixed_risk",
        position_frac=0.08,
        leverage=5,
        max_positions=5,
        base="equity",
        risk_per_trade=0.0075,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )
    rules = {
        symbol: SymbolRules(tick_size=1e-9, step_size=1e-3, min_notional=1.0) for symbol in symbols
    }
    end = eval_end_ms if eval_end_ms is not None else EVAL_START_MS + DAYS * MS_DAY
    return SimContext(
        panel=panel,
        calendar=calendar,
        cfg=cfg,
        sizing=sizing,
        limits=build_risk_limits(sizing=sizing, halt_mode="strict", halt_reset_days=1),
        costs=CostModel(
            fee_taker=0.0005,
            half_spread_min=0.0001,
            slip_min=0.0,
            impact_y=0.0,
            notional_ref=float(sizing.initial_equity * sizing.position_frac * sizing.leverage),
        ),
        rules=rules,
        aliases={},
        data_root=Path("."),
        holdout_start_ms=EVAL_START_MS + 1000 * MS_DAY,
        allow_holdout=False,
        initial_equity=100.0,
        months=["2024-11"],
        chunk_symbols={"2024-11": tuple(symbols)},
        eval_start_ms=EVAL_START_MS,
        eval_end_ms=end,
        schedules=schedules or {},
        exit_schedules=exit_schedules or {},
    )


def run_chunk(
    ctx: SimContext, chunk: MinuteChunk, scenario: str = "base", qv_prior: float | None = 1e6
) -> Portfolio:
    """在单个分钟块上推进一个情景，返回组合状态。

    Args:
        ctx: 运行上下文。
        chunk: 分钟块（月内）。
        scenario: 情景名。
        qv_prior: 期初 60 分钟成交额历史（模拟上月末数据，保证块首调仓可开仓）。
    """
    from trade.rotation.engine import Portfolio, scenario_by_name

    portfolio = Portfolio(scenario_by_name(scenario), ctx)
    if qv_prior is not None:
        portfolio.qv_history = {
            symbol: np.full(60, qv_prior, dtype="float64") for symbol in chunk.row
        }
    portfolio.start_month(chunk)
    for k in range(chunk.minute_ms.shape[0]):
        portfolio.step(int(chunk.minute_ms[k]), chunk, k)
    return portfolio
