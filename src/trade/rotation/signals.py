"""轮动信号：大盘状态过滤、截面动量打分与排名（回测与实盘共用）。

规格：docs/11-轮动策略.md §4

所有计算只用到调仓时刻之前已收盘的小时 K 线：打分的最后一根 K 线在
`调仓时刻 − score_lag_hours` 收盘，大盘过滤用调仓时刻已收盘的最后一根小时 K 线。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trade.rotation.config import RotationConfig
from trade.rotation.data import MS_HOUR, HourlyPanel, UniverseMonth, month_key

# 等权山寨指数最少需要的有效币数，不足视为过滤失效（11 §4.1）
MIN_ALT_INDEX_COINS = 5


@dataclass(frozen=True)
class Decision:
    """一次调仓（或整点卖出检查）时点的排名快照。"""

    ts_ms: int
    filter_on: bool
    ranked: tuple[tuple[str, float], ...]
    rank: dict[str, int]
    universe: frozenset[str]
    note: str = ""


def _last_closed_hour(panel: HourlyPanel, ts_ms: int) -> int:
    """返回调仓时刻 ts 已收盘的最后一根 1h K 线下标（ts 为整点时为其前一小时）。"""
    return panel.hour_index(ts_ms) - 1


def market_filter(
    panel: HourlyPanel,
    ts_ms: int,
    universe: frozenset[str],
    cfg: RotationConfig,
) -> tuple[bool, str]:
    """大盘状态过滤（11 §4.1）：BTC 收盘 > N 日均线，且 U(R) 等权指数 7 日收益 > 0。

    Args:
        panel: 小时面板。
        ts_ms: 调仓时刻（整点毫秒）。
        universe: 当月交易范围 U(R)。
        cfg: 轮动配置（`ma_days`、`alt_index_days`）。

    Returns:
        (是否通过, 未通过的说明)；任一数据不足时按“不通过”处理（保守）。
    """
    i_f = _last_closed_hour(panel, ts_ms)
    window = cfg.ma_days * 24
    start = i_f - window + 1
    if i_f < 0 or start < 0:
        return False, "btc_ma_insufficient"
    btc = panel.close[panel.btc_row, start : i_f + 1]
    if not np.all(np.isfinite(btc)):
        return False, "btc_ma_missing"
    if not float(panel.close[panel.btc_row, i_f]) > float(np.mean(btc)):
        return False, "btc_below_ma"
    back = cfg.alt_index_days * 24
    if i_f - back < 0:
        return False, "alt_index_insufficient"
    rows = [panel.row[symbol] for symbol in universe if symbol in panel.row]
    if not rows:
        return False, "alt_index_empty"
    now = panel.close[rows, i_f]
    past = panel.close[rows, i_f - back]
    valid = np.isfinite(now) & np.isfinite(past) & (past > 0.0)
    if int(valid.sum()) < MIN_ALT_INDEX_COINS:
        return False, "alt_index_missing"
    if float(np.mean(now[valid] / past[valid]) - 1.0) <= 0.0:
        return False, "alt_index_negative"
    return True, ""


def compute_scores(
    panel: HourlyPanel,
    ts_ms: int,
    cfg: RotationConfig,
    *,
    lag_hours: int,
    universe: frozenset[str],
) -> dict[str, float]:
    """截面动量打分（11 §4.2）：L 天对数收益 ÷ L 天 1h 收益标准差。

    Args:
        panel: 小时面板。
        ts_ms: 调仓时刻（整点毫秒）。
        cfg: 轮动配置（`lookback_days`、`oi_bonus`）。
        lag_hours: 打分截止相对调仓时刻的滞后小时数（delay 情景放大一档）。
        universe: 当月交易范围 U(R)。

    Returns:
        symbol -> score；窗口内收盘价缺失或标准差为 0 的币不出现。
    """
    i_last = panel.hour_index(ts_ms - lag_hours * MS_HOUR) - 1
    lookback = cfg.lookback_days * 24
    i_first = i_last - lookback
    if i_first < 0:
        return {}
    symbols = [symbol for symbol in universe if symbol in panel.row]
    if not symbols:
        return {}
    rows = [panel.row[symbol] for symbol in symbols]
    closes = panel.close[rows, i_first : i_last + 1]
    first = closes[:, 0]
    last = closes[:, -1]
    valid = np.isfinite(closes).all(axis=1) & (first > 0.0) & (last > 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        hourly = np.diff(np.log(np.maximum(closes, 1e-12)), axis=1)
        std = np.std(hourly, axis=1, ddof=1)
        scores = np.log(np.maximum(last, 1e-12) / np.maximum(first, 1e-12)) / std
    out: dict[str, float] = {}
    for i, symbol in enumerate(symbols):
        if not valid[i] or not np.isfinite(std[i]) or std[i] <= 0.0:
            continue
        if not np.isfinite(scores[i]):
            continue
        out[symbol] = float(scores[i])
    return _apply_oi_bonus(panel, cfg, i_last, out)


def _apply_oi_bonus(
    panel: HourlyPanel, cfg: RotationConfig, i_last: int, scores: dict[str, float]
) -> dict[str, float]:
    """OI 变化加分（11 §4.2 可选开关）：score += oi_bonus × z(Δlog OI)。"""
    if cfg.oi_bonus <= 0.0 or not scores:
        return scores
    day_back = cfg.oi_bonus_days * 24
    if i_last - day_back < 0:
        return scores
    symbols = sorted(scores)
    rows = [panel.row[symbol] for symbol in symbols]
    now = panel.oi[rows, i_last]
    past = panel.oi[rows, i_last - day_back]
    valid = np.isfinite(now) & np.isfinite(past) & (now > 0.0) & (past > 0.0)
    if int(valid.sum()) < 2:
        return scores
    change = np.log(now[valid] / past[valid])
    mu = float(np.mean(change))
    sigma = float(np.std(change, ddof=1))
    if sigma <= 0.0:
        return scores
    out = dict(scores)
    for i, symbol in enumerate(symbols):
        if valid[i]:
            out[symbol] += cfg.oi_bonus * float((np.log(now[i] / past[i]) - mu) / sigma)
    return out


def _funding_filter(
    panel: HourlyPanel,
    ts_ms: int,
    universe: frozenset[str],
    scores: dict[str, float],
) -> dict[str, float]:
    """资金费过滤（11 §4.2 可选开关）：只保留资金费 ≤ 当月 U(R) 中位数的币。"""
    i_f = _last_closed_hour(panel, ts_ms)
    rows = [panel.row[symbol] for symbol in universe if symbol in panel.row]
    if not rows or i_f < 0:
        return {}
    values = panel.funding[rows, i_f]
    finite = values[np.isfinite(values)]
    if finite.size < MIN_ALT_INDEX_COINS:
        return {}
    median = float(np.median(finite))
    out: dict[str, float] = {}
    for symbol, score in scores.items():
        rate = float(panel.funding[panel.row[symbol], i_f])
        if np.isfinite(rate) and rate <= median:
            out[symbol] = score
    return out


def build_decision(
    panel: HourlyPanel,
    ts_ms: int,
    *,
    universe_month: UniverseMonth,
    cfg: RotationConfig,
    lag_hours: int,
    shuffle_seed: int | None = None,
) -> Decision:
    """构建某时点的完整决策（过滤状态 + 排名）。

    Args:
        panel: 小时面板。
        ts_ms: 决策时点（整点毫秒）。
        universe_month: 该时点所属月的交易范围。
        cfg: 轮动配置。
        lag_hours: 打分滞后小时数。
        shuffle_seed: 非 None 时打乱截面打分（泄露检查情景，固定种子）。

    Returns:
        Decision；`filter_on=False` 时 ranked 仍给出排名，供卖出检查使用。
    """
    filter_on, note = market_filter(panel, ts_ms, universe_month.symbols, cfg)
    scores = compute_scores(panel, ts_ms, cfg, lag_hours=lag_hours, universe=universe_month.symbols)
    if cfg.funding_filter:
        scores = _funding_filter(panel, ts_ms, universe_month.symbols, scores)
    scores = {symbol: score for symbol, score in scores.items() if score > cfg.min_score}
    if shuffle_seed is not None and len(scores) > 1:
        rng = np.random.default_rng([shuffle_seed, ts_ms // MS_HOUR])
        symbols = sorted(scores)
        values = np.array([scores[symbol] for symbol in symbols], dtype="float64")
        rng.shuffle(values)
        scores = {symbol: float(value) for symbol, value in zip(symbols, values, strict=True)}
    ranked = tuple(sorted(scores.items(), key=lambda item: (-item[1], item[0])))
    rank = {symbol: i + 1 for i, (symbol, _) in enumerate(ranked)}
    return Decision(
        ts_ms=ts_ms,
        filter_on=filter_on,
        ranked=ranked,
        rank=rank,
        universe=universe_month.symbols,
        note=note,
    )


def build_schedule(
    panel: HourlyPanel,
    *,
    timestamps: list[int],
    universe_calendar: dict[str, UniverseMonth],
    cfg: RotationConfig,
    lag_hours: int,
    shuffle_seed: int | None = None,
) -> dict[int, Decision]:
    """为一组时点批量构建决策（每个时点只算一次）。"""
    schedule: dict[int, Decision] = {}
    for ts_ms in timestamps:
        universe_month = universe_calendar.get(month_key(ts_ms))
        if universe_month is None:
            continue
        schedule[ts_ms] = build_decision(
            panel,
            ts_ms,
            universe_month=universe_month,
            cfg=cfg,
            lag_hours=lag_hours,
            shuffle_seed=shuffle_seed,
        )
    return schedule
