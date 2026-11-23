"""以交易所为准的目标仓位对账（docs/07 §6.1）。

引擎不依赖本地记忆判断持仓：每次调仓与巡检都先读取交易所实际持仓，再与
策略目标求差，只执行差额。进程崩溃后重启只要重新对账即可回到目标状态。

规格：docs/07-实盘与风控.md §6.1
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from trade.live.orders import round_step_qty

# 视为零的数量（交易所数量最小步长通常远大于该值；用于浮点尾差）
EPS = 1e-9


@dataclass(frozen=True)
class TradeIntent:
    """一笔待执行差额（正数量 = 买入，负数量 = 卖出）。"""

    symbol: str
    qty: float
    reduce_only: bool

    @property
    def side(self) -> str:
        return "BUY" if self.qty > 0 else "SELL"

    @property
    def abs_qty(self) -> float:
        return abs(self.qty)


@dataclass(frozen=True)
class ActualPosition:
    """交易所实际持仓（数量带符号：正为多、负为空）。"""

    symbol: str
    qty: float
    entry_price: float


def held_directions(actual: Mapping[str, float]) -> dict[str, int]:
    """把实际持仓转换为策略接口需要的 `symbol -> ±1`。

    Args:
        actual: symbol → 带符号数量。

    Returns:
        仅包含非零持仓的方向映射。
    """
    return {symbol: 1 if qty > 0 else -1 for symbol, qty in actual.items() if abs(qty) > EPS}


def plan_delta(
    actual: Mapping[str, float],
    targets: Mapping[str, float],
    *,
    step_sizes: Mapping[str, float],
) -> list[TradeIntent]:
    """计算从实际持仓到目标持仓的差额（先平后开）。

    规则（docs/07 §6.1）：
    1. 同向且目标绝对值更小：减仓（reduceOnly）；
    2. 目标为零：全部平掉（reduceOnly）；
    3. 方向相反：先平掉原方向，再开目标方向；
    4. 同向且目标绝对值更大：加仓（非 reduceOnly）；
    5. 所有平仓 / 减仓意图排在开仓意图之前，避免中性策略两边同时成交导致敞口放大。

    Args:
        actual: symbol → 带符号实际数量。
        targets: symbol → 带符号目标数量。
        step_sizes: symbol → stepSize（数量必须向下取整到该步长）。

    Returns:
        TradeIntent 列表；数量为 0 的差额被丢弃。

    Raises:
        KeyError: 需要下单的币缺少 stepSize（红线 12，不回退默认值）。
    """
    closes: list[TradeIntent] = []
    opens: list[TradeIntent] = []
    for symbol in sorted(set(actual) | set(targets)):
        position = float(actual.get(symbol, 0.0))
        target = float(targets.get(symbol, 0.0))
        if abs(position) <= EPS and abs(target) <= EPS:
            continue
        if abs(target) <= EPS:
            qty = _quantize(abs(position), _step(symbol, step_sizes))
            if qty > EPS:
                closes.append(TradeIntent(symbol, -_sign(position) * qty, True))
            continue
        if abs(position) <= EPS:
            qty = _quantize(abs(target), _step(symbol, step_sizes))
            if qty > EPS:
                opens.append(TradeIntent(symbol, _sign(target) * qty, False))
            continue
        target_side = _sign(target)
        position_side = _sign(position)
        if target_side == position_side:
            if abs(target) < abs(position):
                qty = _quantize(abs(position) - abs(target), _step(symbol, step_sizes))
                if qty > EPS:
                    closes.append(TradeIntent(symbol, -position_side * qty, True))
            elif abs(target) > abs(position):
                qty = _quantize(abs(target) - abs(position), _step(symbol, step_sizes))
                if qty > EPS:
                    opens.append(TradeIntent(symbol, target_side * qty, False))
            continue
        # 方向相反：先全部平掉，再按目标数量开仓
        close_qty = _quantize(abs(position), _step(symbol, step_sizes))
        if close_qty > EPS:
            closes.append(TradeIntent(symbol, -position_side * close_qty, True))
        open_qty = _quantize(abs(target), _step(symbol, step_sizes))
        if open_qty > EPS:
            opens.append(TradeIntent(symbol, target_side * open_qty, False))
    return closes + opens


def notional_by_side(
    positions: Mapping[str, float], prices: Mapping[str, float]
) -> tuple[float, float]:
    """按侧统计名义价值。

    Args:
        positions: symbol → 带符号数量。
        prices: symbol → 最新价格。

    Returns:
        (多头名义, 空头名义)，均为正数。

    Raises:
        KeyError: 有持仓的币缺少价格（红线 12）。
    """
    long_total = 0.0
    short_total = 0.0
    for symbol, qty in positions.items():
        if abs(qty) <= EPS:
            continue
        if symbol not in prices:
            raise KeyError(f"缺少 {symbol} 的价格，无法统计敞口（红线 12）")
        value = abs(qty) * float(prices[symbol])
        if qty > 0:
            long_total += value
        else:
            short_total += value
    return long_total, short_total


def net_exposure(positions: Mapping[str, float], prices: Mapping[str, float]) -> float:
    """净敞口（多 − 空，带符号，USDT）。"""
    long_total, short_total = notional_by_side(positions, prices)
    return long_total - short_total


def _sign(value: float) -> int:
    return 1 if value >= 0 else -1


def _step(symbol: str, step_sizes: Mapping[str, float]) -> float:
    if symbol not in step_sizes or step_sizes[symbol] <= 0:
        raise KeyError(f"缺少 {symbol} 的 stepSize，无法取整数量（红线 12）")
    return float(step_sizes[symbol])


def _quantize(qty: float, step: float) -> float:
    """向下取整到 stepSize；浮点尾差视为零。"""
    value = round_step_qty(qty, step)
    return 0.0 if value <= EPS else value
