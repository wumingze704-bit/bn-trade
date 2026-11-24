"""组合层：模式 A / B 名义、敞口上限、β 中性与流动性约束（docs/13 §3）。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from trade.common.sizing import SizingConfig, calc_notional, loss_rate, size_from_notional
from trade.research.config import ExecutionConfig, PortfolioConfig
from trade.research.interface import EXPOSURE_NEUTRAL, Decision, Target
from trade.research.view import View

_BP = 1e-4
# 不允许开仓的资金费判定窗口（1 号 B 第 1 条：过去 7 天没有结算记录时不允许开仓）
_FUNDING_REQUIRED_HOURS = 7 * 24
# β 回归与协方差估计的最小样本数（13 §3：样本不足 30 天的币 β 取 1.0 并计数）
_MIN_BETA_OBS = 30
_MIN_COV_OBS = 30
_MODE_FIXED_RISK = "fixed_risk"
_MODE_VOL_TARGET = "vol_target"
_SIZING_MODES = frozenset({_MODE_FIXED_RISK, _MODE_VOL_TARGET})
# 组合年化波动的日频换算（13 §3：日收益协方差 × √365）
_ANNUALIZATION = math.sqrt(365.0)


@dataclass(frozen=True)
class PlannedPosition:
    """待成交的目标仓位（数量在成交价确定后计算）。"""

    symbol: str
    side: int
    sl: float
    notional: float
    price_estimate: float


@dataclass
class PlanStats:
    """组合层统计（写入报告与诊断）。

    `attempted`：进入仓位计算的目标数（不含已同向持有的仓位），是可执行性检查的分母；
    `dropped_min_notional`：其中因最小下单额或取整被丢弃的数量（第七部分第 5 条 i 款）。
    `beta_default`：日线样本不足 30 天、β 按 1.0 处理的次数（13 §3）。
    `vol_missing`：模式 B 因缺少日线波动或协方差数据被剔除的仓位数。
    """

    dropped: int = 0
    attempted: int = 0
    dropped_min_notional: int = 0
    kept: int = 0
    kept_symbols: set[str] = field(default_factory=set)
    long_notional: float = 0.0
    short_notional: float = 0.0
    capped: dict[str, int] = field(default_factory=dict)
    beta_default: int = 0
    vol_missing: int = 0


@dataclass(frozen=True)
class _Candidate:
    """通过基础校验、等待计算名义价值的目标。"""

    target: Target
    price: float
    mean_volume: float
    step_size: float
    min_notional: float


def half_spread_for(
    view: View, symbol: str, exec_cfg: ExecutionConfig, top_symbols: frozenset[str]
) -> float:
    """按成交额分层的半价差（docs/13 §4）。"""
    bp = exec_cfg.half_spread_top_bp if symbol in top_symbols else exec_cfg.half_spread_other_bp
    return bp * _BP


def top_volume_symbols(view: View, symbols: frozenset[str], top_n: int) -> frozenset[str]:
    """当月 30 天成交额前 `top_n` 的币（数据不足时按可得中位数排序）。"""
    scored: list[tuple[float, str]] = []
    for symbol in symbols:
        volume = view.median_daily_quote_volume(symbol, 30)
        if volume is not None:
            scored.append((volume, symbol))
    scored.sort(reverse=True)
    return frozenset(symbol for _, symbol in scored[:top_n])


def plan_positions(
    decision: Decision,
    *,
    view: View,
    equity: float,
    available_margin: float,
    sizing_cfg: SizingConfig,
    exec_cfg: ExecutionConfig,
    portfolio_cfg: PortfolioConfig,
    fee_taker: float,
    blocked: Mapping[str, int],
    held: Mapping[str, int] | None = None,
    sizing_mode: str | None = None,
) -> tuple[list[PlannedPosition], PlanStats]:
    """把策略目标转换为可成交的名义价值（docs/13 §3）。

    Args:
        decision: 策略目标（symbol/side/sl/weight）。
        view: 决策时刻视角。
        equity: 当前盯市权益，USDT。
        available_margin: 当前可用保证金，USDT。
        sizing_cfg: 风险包络（configs/sizing_research.yaml）。
        exec_cfg: 执行与成本参数。
        portfolio_cfg: 组合层上限。
        fee_taker: 吃单费率。
        blocked: 冷却中的币（symbol → 解禁时刻），冷却期内不新开仓。
        held: 当前持仓方向（symbol → ±1）；已同向持有的目标不重复开仓，也不计入应开仓位。
        sizing_mode: `fixed_risk`（模式 A）或 `vol_target`（模式 B）；
            缺省取 `sizing_cfg.sizing_mode`。

    Returns:
        (待成交仓位列表, 统计)。被丢弃的仓位记入 dropped，进入仓位计算的记入 attempted。

    Raises:
        ValueError: 仓位模式非法，或模式 B 的波动 / β 数据不足且无法按规则处理。
    """
    mode = sizing_mode or sizing_cfg.sizing_mode
    if mode not in _SIZING_MODES:
        raise ValueError(f"未知仓位模式：{mode}（13 §3 允许 {sorted(_SIZING_MODES)}）")
    held = held or {}
    stats = PlanStats()
    top_symbols = top_volume_symbols(view, view.universe_symbols(), exec_cfg.half_spread_top_n)
    candidates = _valid_candidates(
        decision,
        view=view,
        blocked=blocked,
        held=held,
        exec_cfg=exec_cfg,
        portfolio_cfg=portfolio_cfg,
        stats=stats,
    )
    if mode == _MODE_VOL_TARGET:
        planned = _vol_target_positions(
            candidates,
            view=view,
            equity=equity,
            available_margin=available_margin,
            sizing_cfg=sizing_cfg,
            exec_cfg=exec_cfg,
            stats=stats,
        )
    else:
        planned = _fixed_risk_positions(
            candidates,
            view=view,
            equity=equity,
            available_margin=available_margin,
            sizing_cfg=sizing_cfg,
            exec_cfg=exec_cfg,
            fee_taker=fee_taker,
            top_symbols=top_symbols,
            stats=stats,
        )
    # 持仓数不超过 max_positions（策略按强度排序，保留靠前的目标）
    planned = planned[: portfolio_cfg.max_positions]
    return _apply_caps(
        planned,
        decision=decision,
        view=view,
        equity=equity,
        sizing_cfg=sizing_cfg,
        portfolio_cfg=portfolio_cfg,
        stats=stats,
    )


def _valid_candidates(
    decision: Decision,
    *,
    view: View,
    blocked: Mapping[str, int],
    held: Mapping[str, int],
    exec_cfg: ExecutionConfig,
    portfolio_cfg: PortfolioConfig,
    stats: PlanStats,
) -> list[_Candidate]:
    """基础校验：资金费、价格、成交额与交易规则；通过的目标进入仓位计算。"""
    candidates: list[_Candidate] = []
    for target in decision.targets:
        if held.get(target.symbol) == target.side:
            stats.kept += 1
            stats.kept_symbols.add(target.symbol)
            continue
        if target.symbol in blocked or not view.has(target.symbol):
            stats.dropped += 1
            continue
        if target.side not in (1, -1) or not (0.0 < target.sl <= exec_cfg.stop_max):
            stats.dropped += 1
            continue
        if view.funding_hourly_mean(target.symbol, _FUNDING_REQUIRED_HOURS) is None:
            # 没有资金费序列或过去 7 天没有结算：不允许开仓（1 号 B 第 1 条）
            stats.dropped += 1
            stats.capped["no_funding"] = stats.capped.get("no_funding", 0) + 1
            continue
        price = view.close(target.symbol)
        if price is None or price <= 0.0:
            stats.dropped += 1
            continue
        mean_volume = view.mean_quote_volume(target.symbol, portfolio_cfg.liq_cap_hours)
        if mean_volume is None or mean_volume <= 0.0:
            stats.dropped += 1
            continue
        rule = view.meta(target.symbol)
        if rule.step_size is None or rule.step_size <= 0.0 or rule.min_notional is None:
            stats.dropped += 1
            continue
        candidates.append(
            _Candidate(
                target=target,
                price=price,
                mean_volume=mean_volume,
                step_size=rule.step_size,
                min_notional=rule.min_notional,
            )
        )
    return candidates


def _fixed_risk_positions(
    candidates: list[_Candidate],
    *,
    view: View,
    equity: float,
    available_margin: float,
    sizing_cfg: SizingConfig,
    exec_cfg: ExecutionConfig,
    fee_taker: float,
    top_symbols: frozenset[str],
    stats: PlanStats,
) -> list[PlannedPosition]:
    """模式 A：名义 = 权益 × risk_per_trade ÷ 止损亏损率（13 §3 第 1 条）。"""
    weighted: list[tuple[float, PlannedPosition]] = []
    for candidate in candidates:
        target = candidate.target
        spread = half_spread_for(view, target.symbol, exec_cfg, top_symbols)
        slip = exec_cfg.slip_min_bp * _BP
        rate = loss_rate(
            sl=target.sl,
            slip_stop=exec_cfg.stop_slip_mult * slip,
            half_spread=spread,
            fee_taker=fee_taker,
        )
        result = calc_notional(
            base=equity,
            sl=target.sl,
            loss_rate=rate,
            entry_price=candidate.price,
            mean_qv_60=candidate.mean_volume,
            available_margin=available_margin,
            step_size=candidate.step_size,
            min_notional=candidate.min_notional,
            cfg=sizing_cfg,
        )
        stats.attempted += 1
        if not result.accepted:
            stats.dropped += 1
            if result.status in {"below_min_notional", "step_rounding"}:
                stats.dropped_min_notional += 1
            stats.capped[result.status] = stats.capped.get(result.status, 0) + 1
            continue
        if result.capped_by is not None:
            stats.capped[result.capped_by] = stats.capped.get(result.capped_by, 0) + 1
        notional = result.notional * max(0.0, target.weight)
        if notional <= 0.0:
            stats.dropped += 1
            continue
        weighted.append(
            (
                target.weight,
                PlannedPosition(
                    symbol=target.symbol,
                    side=target.side,
                    sl=target.sl,
                    notional=notional,
                    price_estimate=candidate.price,
                ),
            )
        )
    return [position for _, position in weighted]


def _vol_target_positions(
    candidates: list[_Candidate],
    *,
    view: View,
    equity: float,
    available_margin: float,
    sizing_cfg: SizingConfig,
    exec_cfg: ExecutionConfig,
    stats: PlanStats,
) -> list[PlannedPosition]:
    """模式 B：波动率倒数权重 + 组合事前波动缩放 + 15% 防灾止损（13 §3）。"""
    stats.attempted += len(candidates)
    stop_max = exec_cfg.stop_max
    weights: dict[str, float] = {}
    candidates_by_symbol: dict[str, _Candidate] = {}
    for candidate in candidates:
        symbol = candidate.target.symbol
        data = view.daily_returns(symbol, sizing_cfg.vol_lookback_days)
        if data is None:
            _drop_without_vol(stats, "no_vol")
            continue
        _times, returns = data
        sigma = float(np.std(returns, ddof=1))
        if not np.isfinite(sigma) or sigma <= 0.0:
            _drop_without_vol(stats, "no_vol")
            continue
        weights[symbol] = 1.0 / sigma
        candidates_by_symbol[symbol] = candidate
    if not weights:
        return []
    covariance, kept = _covariance(view, weights, sizing_cfg, stats)
    if covariance is None or not kept:
        return []
    weight_vector = np.asarray([weights[symbol] for symbol in kept], dtype="float64")
    variance = float(weight_vector @ covariance @ weight_vector)
    if not np.isfinite(variance) or variance <= 0.0:
        raise ValueError(
            f"{view.now_ms} 模式 B 组合事前波动为 {variance}，无法缩放到目标波动（{kept}，13 §3）"
        )
    scale = sizing_cfg.target_vol / (_ANNUALIZATION * math.sqrt(variance))
    risk_cap = equity * sizing_cfg.max_risk_per_trade / stop_max
    position_cap = equity * sizing_cfg.position_frac * float(sizing_cfg.leverage)
    planned: list[PlannedPosition] = []
    for symbol in kept:
        candidate = candidates_by_symbol[symbol]
        notional = equity * scale * weights[symbol]
        liquidity_cap = sizing_cfg.liq_cap * candidate.mean_volume
        margin_cap = available_margin * 0.95 * float(sizing_cfg.leverage)
        cap = min(risk_cap, position_cap, liquidity_cap, margin_cap)
        if notional > cap:
            reason = _cap_reason(cap, risk_cap, position_cap, liquidity_cap, margin_cap)
            stats.capped[reason] = stats.capped.get(reason, 0) + 1
            notional = cap
        _qty, status = size_from_notional(
            notional=notional,
            entry_price=candidate.price,
            step_size=candidate.step_size,
            min_notional=candidate.min_notional,
        )
        if status != "accepted":
            stats.dropped += 1
            if status in {"below_min_notional", "step_rounding"}:
                stats.dropped_min_notional += 1
            stats.capped[status] = stats.capped.get(status, 0) + 1
            continue
        planned.append(
            PlannedPosition(
                symbol=symbol,
                side=candidate.target.side,
                sl=stop_max,
                notional=notional,
                price_estimate=candidate.price,
            )
        )
    return planned


def _drop_without_vol(stats: PlanStats, reason: str) -> None:
    """模式 B 缺少日线波动数据：丢弃并计数（不得静默补值，红线 12）。"""
    stats.dropped += 1
    stats.vol_missing += 1
    stats.capped[reason] = stats.capped.get(reason, 0) + 1


def _cap_reason(
    cap: float, risk_cap: float, position_cap: float, liquidity_cap: float, margin_cap: float
) -> str:
    """模式 B 单仓上限的截断原因（取最小值对应的名字）。"""
    for name, value in (
        ("risk_cap", risk_cap),
        ("position_cap", position_cap),
        ("liquidity", liquidity_cap),
        ("margin", margin_cap),
    ):
        if math.isclose(value, cap, rel_tol=1e-12, abs_tol=1e-12):
            return name
    return "risk_cap"


def _covariance(
    view: View,
    weights: Mapping[str, float],
    sizing_cfg: SizingConfig,
    stats: PlanStats,
) -> tuple[np.ndarray | None, list[str]]:
    """过去 `cov_lookback_days` 天日收益的协方差矩阵与保留的币（13 §3）。"""
    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for symbol in weights:
        data = view.daily_returns(symbol, sizing_cfg.cov_lookback_days)
        if data is None:
            _drop_without_vol(stats, "no_cov")
        else:
            series[symbol] = data
    if not series:
        return None, []
    common: np.ndarray | None = None
    for times, _ in series.values():
        common = times if common is None else np.intersect1d(common, times)
    if common is None or common.size < _MIN_COV_OBS:
        for _symbol in series:
            _drop_without_vol(stats, "no_cov")
        return None, []
    kept = sorted(series)
    matrix = np.empty((common.size, len(kept)), dtype="float64")
    for column, symbol in enumerate(kept):
        times, returns = series[symbol]
        index = np.searchsorted(times, common)
        matrix[:, column] = returns[index]
    covariance = np.atleast_2d(np.cov(matrix, rowvar=False, ddof=1))
    return covariance, kept


def _symbol_betas(
    view: View, symbols: set[str], sizing_cfg: SizingConfig, stats: PlanStats
) -> dict[str, float]:
    """各币对 BTCUSDT 日收益的 β（样本不足 30 天或方差为 0 时取 1.0 并计数，13 §3）。"""
    if view.market.daily.get("BTCUSDT") is None:
        raise ValueError(f"{view.now_ms} 缺少 BTCUSDT 日线，无法计算 β（红线 12）")
    btc = view.daily_returns("BTCUSDT", sizing_cfg.beta_lookback_days)
    betas: dict[str, float] = {}
    for symbol in sorted(symbols):
        betas[symbol] = 1.0
        data = view.daily_returns(symbol, sizing_cfg.beta_lookback_days)
        if data is None or btc is None:
            stats.beta_default += 1
            continue
        times, returns = data
        btc_times, btc_returns = btc
        common, index_symbol, index_btc = np.intersect1d(times, btc_times, return_indices=True)
        if common.size < _MIN_BETA_OBS:
            stats.beta_default += 1
            continue
        benchmark = btc_returns[index_btc]
        variance = float(np.var(benchmark, ddof=1))
        if not np.isfinite(variance) or variance <= 0.0:
            stats.beta_default += 1
            continue
        beta = float(np.cov(returns[index_symbol], benchmark, ddof=1)[0, 1] / variance)
        if not np.isfinite(beta):
            stats.beta_default += 1
            continue
        betas[symbol] = beta
    return betas


def _apply_caps(
    planned: list[PlannedPosition],
    *,
    decision: Decision,
    view: View,
    equity: float,
    sizing_cfg: SizingConfig,
    portfolio_cfg: PortfolioConfig,
    stats: PlanStats,
) -> tuple[list[PlannedPosition], PlanStats]:
    """β 中性、净敞口、总名义与保证金上限（docs/13 §3）。"""
    if not planned:
        return planned, stats
    if decision.exposure == EXPOSURE_NEUTRAL:
        planned = _neutral_balance(
            planned, view=view, equity=equity, sizing_cfg=sizing_cfg, stats=stats
        )
    long_total = sum(p.notional for p in planned if p.side > 0)
    short_total = sum(p.notional for p in planned if p.side < 0)
    if decision.exposure != EXPOSURE_NEUTRAL:
        net = long_total - short_total
        if abs(net) > portfolio_cfg.net_exposure_cap * equity > 0.0:
            target_net = portfolio_cfg.net_exposure_cap * equity * (1.0 if net > 0 else -1.0)
            heavier = 1 if net > 0 else -1
            heavier_total = long_total if heavier > 0 else short_total
            lighter_total = short_total if heavier > 0 else long_total
            scale = (lighter_total + target_net) / heavier_total if heavier_total > 0 else 0.0
            planned = [_scaled(p, scale) if p.side == heavier else p for p in planned]
            long_total = sum(p.notional for p in planned if p.side > 0)
            short_total = sum(p.notional for p in planned if p.side < 0)
    total = long_total + short_total
    scale = 1.0
    if total > portfolio_cfg.total_notional_cap * equity > 0.0:
        scale = min(scale, portfolio_cfg.total_notional_cap * equity / total)
    margin = total / float(sizing_cfg.leverage)
    if margin > portfolio_cfg.margin_cap * equity > 0.0:
        scale = min(scale, portfolio_cfg.margin_cap * equity / margin)
    if scale < 1.0:
        planned = [_scaled(p, scale) for p in planned]
    planned = [p for p in planned if p.notional > 0.0]
    stats.long_notional = sum(p.notional for p in planned if p.side > 0)
    stats.short_notional = sum(p.notional for p in planned if p.side < 0)
    return planned, stats


def _neutral_balance(
    planned: list[PlannedPosition],
    *,
    view: View,
    equity: float,
    sizing_cfg: SizingConfig,
    stats: PlanStats,
) -> list[PlannedPosition]:
    """中性策略的 β 中性调整与多空名义比约束（13 §3）。

    1. 按 β 缩小两边使组合事前 β 为 0；
    2. 多头总名义 ÷ 空头总名义超出 `ls_ratio_range` 时按比例缩小名义较大的一边；
    3. 仍不满足 |β| ≤ `beta_abs_max` 时两边等比缩小（保持多空比）。
    """
    longs = [p for p in planned if p.side > 0]
    shorts = [p for p in planned if p.side < 0]
    if not longs or not shorts:
        return planned
    betas = _symbol_betas(view, {p.symbol for p in planned}, sizing_cfg, stats)

    def beta_sum(items: list[PlannedPosition]) -> float:
        return sum(item.notional * betas[item.symbol] for item in items)

    long_beta = beta_sum(longs)
    short_beta = beta_sum(shorts)
    if long_beta > 0.0 and short_beta > 0.0:
        common = min(long_beta, short_beta)
        longs = [_scaled(p, common / long_beta) for p in longs]
        shorts = [_scaled(p, common / short_beta) for p in shorts]
    ratio_min, ratio_max = sizing_cfg.ls_ratio_range
    long_total = sum(p.notional for p in longs)
    short_total = sum(p.notional for p in shorts)
    if long_total > 0.0 and short_total > 0.0:
        ratio = long_total / short_total
        if ratio > ratio_max:
            longs = [_scaled(p, ratio_max / ratio) for p in longs]
            stats.capped["beta"] = stats.capped.get("beta", 0) + 1
        elif ratio < ratio_min:
            shorts = [_scaled(p, ratio / ratio_min) for p in shorts]
            stats.capped["beta"] = stats.capped.get("beta", 0) + 1
    balanced = [*longs, *shorts]
    exposure = beta_sum(longs) - beta_sum(shorts)
    cap = sizing_cfg.beta_abs_max * equity
    if cap > 0.0 and abs(exposure) > cap:
        balanced = [_scaled(p, cap / abs(exposure)) for p in balanced]
        stats.capped["beta_shrink"] = stats.capped.get("beta_shrink", 0) + 1
    return balanced


def _scaled(position: PlannedPosition, scale: float) -> PlannedPosition:
    return PlannedPosition(
        symbol=position.symbol,
        side=position.side,
        sl=position.sl,
        notional=position.notional * scale,
        price_estimate=position.price_estimate,
    )
