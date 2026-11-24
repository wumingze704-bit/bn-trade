"""组合层：fixed_risk 名义、敞口上限、中性平衡与流动性约束（docs/13 §3）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from trade.common.sizing import SizingConfig, calc_notional, loss_rate
from trade.research.config import ExecutionConfig, PortfolioConfig
from trade.research.interface import EXPOSURE_NEUTRAL, Decision
from trade.research.view import View

_BP = 1e-4
# 不允许开仓的资金费判定窗口（1 号 B 第 1 条：过去 7 天没有结算记录时不允许开仓）
_FUNDING_REQUIRED_HOURS = 7 * 24


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
    """

    dropped: int = 0
    attempted: int = 0
    dropped_min_notional: int = 0
    kept: int = 0
    kept_symbols: set[str] = field(default_factory=set)
    long_notional: float = 0.0
    short_notional: float = 0.0
    capped: dict[str, int] = field(default_factory=dict)


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

    Returns:
        (待成交仓位列表, 统计)。被丢弃的仓位记入 dropped，进入仓位计算的记入 attempted。
    """
    held = held or {}
    stats = PlanStats()
    top_symbols = top_volume_symbols(view, view.universe_symbols(), exec_cfg.half_spread_top_n)
    weighted: list[tuple[float, PlannedPosition]] = []
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
            entry_price=price,
            mean_qv_60=mean_volume,
            available_margin=available_margin,
            step_size=rule.step_size,
            min_notional=rule.min_notional,
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
                    price_estimate=price,
                ),
            )
        )
    # 持仓数不超过 max_positions（策略按强度排序，保留靠前的目标）
    weighted = weighted[: portfolio_cfg.max_positions]
    return _apply_caps(
        [position for _, position in weighted],
        decision=decision,
        equity=equity,
        sizing_cfg=sizing_cfg,
        portfolio_cfg=portfolio_cfg,
        stats=stats,
    )


def _apply_caps(
    planned: list[PlannedPosition],
    *,
    decision: Decision,
    equity: float,
    sizing_cfg: SizingConfig,
    portfolio_cfg: PortfolioConfig,
    stats: PlanStats,
) -> tuple[list[PlannedPosition], PlanStats]:
    """中性平衡、净敞口、总名义与保证金上限（docs/13 §3）。"""
    long_total = sum(p.notional for p in planned if p.side > 0)
    short_total = sum(p.notional for p in planned if p.side < 0)
    if decision.exposure == EXPOSURE_NEUTRAL and long_total > 0.0 and short_total > 0.0:
        common = min(long_total, short_total)
        planned = [
            _scaled(p, common / (long_total if p.side > 0 else short_total)) for p in planned
        ]
        long_total = short_total = common
    elif decision.exposure != EXPOSURE_NEUTRAL:
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


def _scaled(position: PlannedPosition, scale: float) -> PlannedPosition:
    return PlannedPosition(
        symbol=position.symbol,
        side=position.side,
        sl=position.sl,
        notional=position.notional * scale,
        price_estimate=position.price_estimate,
    )
