"""挂单优先的订单执行器与影子盘成交模拟（docs/07 §6.4）。

- paper / live：先平后开；每笔先挂 post-only 限价单（GTX，买一 / 卖一），
  等待 `post_only_wait_sec` 秒；未成交部分撤单并改挂一次；仍有剩余用 LIMIT IOC
  吃单（买价上限 = 卖一 × (1 + N tick)，卖价下限 = 买一 × (1 − N tick)），
  最后用市价单完成。订单状态未知时先按 clientOrderId 查询再决定是否重发。
- shadow：不调用任何下单接口，按研究框架口径在下一根 1h 开盘价加半价差与滑点模拟成交。

规格：docs/07-实盘与风控.md §6.4；docs/13-研究框架.md §4
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.exchange.models import BookTicker, OrderResult
from trade.live.orders import build_order_id, floor_to_tick, round_step_qty
from trade.live.targets import TradeIntent

_BP = 1e-4
_POLL_INTERVAL_SEC = 3.0
_MAX_LIMIT_RETRY = 1


class ExecutionError(Exception):
    """订单无法完成或状态无法确认（调用方按风控规则处理）。"""


@dataclass(frozen=True)
class Fill:
    """一笔成交记录。"""

    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    client_id: str
    maker: bool
    ts_ms: int


@dataclass
class ExecutionReport:
    """单笔差额的执行结果（挂单成交比例与实际成本，docs/07 §6.4）。"""

    symbol: str
    requested_qty: float
    filled_qty: float = 0.0
    notional: float = 0.0
    maker_qty: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    @property
    def avg_price(self) -> float:
        return self.notional / self.filled_qty if self.filled_qty > 0 else 0.0

    @property
    def maker_ratio(self) -> float:
        return self.maker_qty / self.filled_qty if self.filled_qty > 0 else 0.0


@dataclass(frozen=True)
class Book:
    """决策时刻盘口（买一 / 卖一）。"""

    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)


def research_fill_price(*, open_price: float, side: int, half_spread: float, slip: float) -> float:
    """研究框架的成交价口径（docs/13 §4）：下一根 1h 开盘价 ± （半价差 + 滑点）。

    Args:
        open_price: 下一根 1h K 线开盘价。
        side: +1 买入，-1 卖出。
        half_spread: 半价差（比例，0.0002 = 2bp）。
        slip: 滑点（比例）。

    Returns:
        成交价。
    """
    if side > 0:
        return open_price * (1.0 + half_spread + slip)
    return open_price * (1.0 - half_spread - slip)


def estimate_slip(
    *,
    notional: float,
    mean_hourly_volume: float,
    hourly_vol: float,
    slip_min_bp: float,
    impact_y: float,
) -> float:
    """研究框架的滑点估计（docs/13 §4）。

    Args:
        notional: 下单名义价值，USDT。
        mean_hourly_volume: 近窗口平均小时成交额，USDT。
        hourly_vol: 近窗口 1h 对数收益标准差。
        slip_min_bp: 滑点下限，bp。
        impact_y: 冲击系数。

    Returns:
        滑点（比例）。
    """
    if notional <= 0.0 or mean_hourly_volume <= 0.0:
        return slip_min_bp * _BP
    value = impact_y * hourly_vol * (notional / mean_hourly_volume) ** 0.5
    return float(max(slip_min_bp * _BP, value))


class ShadowExecutor:
    """影子盘成交模拟：只记录，不调用任何下单接口（红线：shadow 禁止下单）。"""

    def __init__(self, *, fee_rate: float) -> None:
        self._fee_rate = fee_rate

    def fill(
        self,
        intent: TradeIntent,
        *,
        open_price: float,
        half_spread: float,
        slip: float,
        ts_ms: int,
        client_id: str,
    ) -> Fill:
        """按研究口径生成一笔虚拟成交。"""
        side = 1 if intent.qty > 0 else -1
        price = research_fill_price(
            open_price=open_price, side=side, half_spread=half_spread, slip=slip
        )
        qty = abs(intent.qty)
        return Fill(
            symbol=intent.symbol,
            side=intent.side,
            qty=qty,
            price=price,
            fee=qty * price * self._fee_rate,
            client_id=client_id,
            maker=False,
            ts_ms=ts_ms,
        )

    def report(self, fills: list[Fill]) -> ExecutionReport:
        """把影子成交汇总为执行报告。"""
        if not fills:
            return ExecutionReport(symbol="", requested_qty=0.0)
        total_qty = sum(f.qty for f in fills)
        notional = sum(f.qty * f.price for f in fills)
        return ExecutionReport(
            symbol=fills[0].symbol,
            requested_qty=total_qty,
            filled_qty=total_qty,
            notional=notional,
            maker_qty=0.0,
            fills=list(fills),
        )


@dataclass(frozen=True)
class SymbolRules:
    """下单需要的交易规则。"""

    tick_size: float
    step_size: float


SleepFn = Callable[[float], Awaitable[None]]
NowMsFn = Callable[[], int]


class ShadowModeError(RuntimeError):
    """shadow 模式调用了任何下单 / 撤单 / 改杠杆 / 改保证金模式接口。"""


class ReadOnlyMarketClient:
    """shadow 模式使用的只读客户端：任何交易类调用直接抛错（红线：shadow 禁止下单）。

    只代理行情与查询接口；不提供 `place_order`、`cancel_*`、`set_leverage`、
    `set_margin_type`、`set_dual_side_position` 等修改类方法。
    """

    def __init__(self, client: ExchangeClient) -> None:
        self._client = client

    async def ping(self) -> dict[str, Any]:
        return await self._client.ping()

    async def server_time(self) -> Any:
        return await self._client.server_time()

    async def exchange_info(self) -> Any:
        return await self._client.exchange_info()

    async def klines(self, symbol: str, interval: str, **kwargs: Any) -> list[list[Any]]:
        return await self._client.klines(symbol, interval, **kwargs)

    async def mark_price_klines(self, symbol: str, interval: str, **kwargs: Any) -> list[list[Any]]:
        """代理标记价格 K 线（止损基准，docs/13 §4）。"""
        return await self._client.mark_price_klines(symbol, interval, **kwargs)

    async def premium_index_klines(
        self, symbol: str, interval: str, **kwargs: Any
    ) -> list[list[Any]]:
        """代理溢价指数 K 线（候选用到 premium_1h 时）。"""
        return await self._client.premium_index_klines(symbol, interval, **kwargs)

    async def book_ticker(self, symbol: str | None = None) -> list[BookTicker]:
        return await self._client.book_ticker(symbol)

    async def funding_rate(self, symbol: str, *, limit: int = 500) -> list[dict[str, Any]]:
        return await self._client.funding_rate(symbol, limit=limit)

    async def place_order(self, **params: Any) -> Any:
        raise ShadowModeError("shadow 模式禁止下单")

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> Any:
        raise ShadowModeError("shadow 模式禁止撤单")

    async def cancel_all_open_orders(self, symbol: str | None = None) -> Any:
        raise ShadowModeError("shadow 模式禁止撤单")

    async def place_algo_order(self, **params: Any) -> Any:
        raise ShadowModeError("shadow 模式禁止下条件单")

    async def cancel_algo_order(self, **params: Any) -> Any:
        raise ShadowModeError("shadow 模式禁止撤条件单")

    async def set_leverage(self, symbol: str, leverage: int) -> Any:
        raise ShadowModeError("shadow 模式禁止修改杠杆")

    async def set_margin_type(self, symbol: str, margin_type: str) -> Any:
        raise ShadowModeError("shadow 模式禁止修改保证金模式")

    async def set_dual_side_position(self, dual: bool) -> Any:
        raise ShadowModeError("shadow 模式禁止修改持仓模式")


class ExchangeExecutor:
    """挂单优先的实盘执行器（docs/07 §6.4）。

    Args:
        client: 币安客户端。
        now_ms: 当前 UTC 毫秒时间函数（测试注入）。
        sleep: 异步等待函数（测试注入）。
        wait_sec: 每档 post-only 等待秒数。
        tick_buffer: IOC 吃单价格允许的 tick 数。
        fee_rate: 报告用吃单费率（精确费用以 userTrades 对账为准）。
        poll_sec: 等待期间查询订单状态的间隔。
    """

    def __init__(
        self,
        client: ExchangeClient,
        *,
        now_ms: NowMsFn,
        sleep: SleepFn,
        wait_sec: int,
        tick_buffer: int,
        fee_rate: float,
        poll_sec: float = _POLL_INTERVAL_SEC,
    ) -> None:
        self._client = client
        self._now_ms = now_ms
        self._sleep = sleep
        self._wait_sec = float(wait_sec)
        self._tick_buffer = int(tick_buffer)
        self._fee_rate = fee_rate
        self._poll_sec = float(poll_sec)

    async def execute(
        self,
        intent: TradeIntent,
        *,
        rules: SymbolRules,
        alias: str,
        minute: str,
        seq: int,
    ) -> ExecutionReport:
        """执行一笔差额：两档 post-only → IOC → 市价（docs/07 §6.4）。

        Args:
            intent: 待执行差额（正买负卖，reduce_only 表示只减仓）。
            rules: tickSize / stepSize。
            alias: 币种别名片段（订单 ID）。
            minute: 调仓时刻 `%Y%m%d%H%M`。
            seq: 同一调仓内的序号（保证 clientOrderId 唯一）。

        Returns:
            ExecutionReport（含成交明细与挂单成交比例）。

        Raises:
            ExecutionError: 订单状态未知且无法查询。
        """
        side = "BUY" if intent.qty > 0 else "SELL"
        remaining = round_step_qty(abs(intent.qty), rules.step_size)
        report = ExecutionReport(symbol=intent.symbol, requested_qty=remaining)
        if remaining <= 0.0:
            return report
        book = await self._best_book(intent.symbol)
        for attempt in range(_MAX_LIMIT_RETRY + 1):
            if remaining <= 0.0:
                break
            price = floor_to_tick(book.bid if side == "BUY" else book.ask, rules.tick_size)
            client_id = build_order_id(alias, minute, f"E{attempt + 1}", seq)
            order = await self._place(
                symbol=intent.symbol,
                side=side,
                type_="LIMIT",
                quantity=remaining,
                price=price,
                time_in_force="GTX",
                reduce_only=intent.reduce_only,
                client_id=client_id,
            )
            order = await self._wait(symbol=intent.symbol, client_id=client_id, order=order)
            filled = min(remaining, float(order.executed_qty))
            if filled > 0.0:
                self._append_fill(
                    report,
                    side=side,
                    qty=filled,
                    price=float(order.avg_price) or price,
                    client_id=client_id,
                    maker=True,
                )
                report.maker_qty += filled
                remaining = round_step_qty(remaining - filled, rules.step_size)
            if order.status in {"NEW", "PARTIALLY_FILLED"}:
                await self._cancel(symbol=intent.symbol, client_id=client_id)
            book = await self._best_book(intent.symbol)
        if remaining > 0.0:
            remaining = await self._ioc(
                intent=intent,
                side=side,
                remaining=remaining,
                rules=rules,
                book=book,
                alias=alias,
                minute=minute,
                seq=seq,
                report=report,
            )
        if remaining > 0.0:
            await self._market(
                intent=intent,
                side=side,
                qty=remaining,
                alias=alias,
                minute=minute,
                seq=seq,
                report=report,
            )
        return report

    async def _ioc(
        self,
        *,
        intent: TradeIntent,
        side: str,
        remaining: float,
        rules: SymbolRules,
        book: Book,
        alias: str,
        minute: str,
        seq: int,
        report: ExecutionReport,
    ) -> float:
        """IOC 补单：买单上限 = 卖一 + N tick，卖单下限 = 买一 − N tick。"""
        buffer = self._tick_buffer * rules.tick_size
        if side == "BUY":
            limit = _ceil_to_tick(book.ask + buffer, rules.tick_size)
        else:
            limit = floor_to_tick(max(book.bid - buffer, rules.tick_size), rules.tick_size)
        client_id = build_order_id(alias, minute, "IX", seq)
        order = await self._place(
            symbol=intent.symbol,
            side=side,
            type_="LIMIT",
            quantity=remaining,
            price=limit,
            time_in_force="IOC",
            reduce_only=intent.reduce_only,
            client_id=client_id,
        )
        filled = min(remaining, float(order.executed_qty))
        if filled > 0.0:
            self._append_fill(
                report,
                side=side,
                qty=filled,
                price=float(order.avg_price) or limit,
                client_id=client_id,
                maker=False,
            )
        return round_step_qty(remaining - filled, rules.step_size)

    async def _market(
        self,
        *,
        intent: TradeIntent,
        side: str,
        qty: float,
        alias: str,
        minute: str,
        seq: int,
        report: ExecutionReport,
    ) -> None:
        """市价兜底。"""
        client_id = build_order_id(alias, minute, "MK", seq)
        order = await self._place(
            symbol=intent.symbol,
            side=side,
            type_="MARKET",
            quantity=qty,
            price=None,
            time_in_force=None,
            reduce_only=intent.reduce_only,
            client_id=client_id,
        )
        filled = min(qty, float(order.executed_qty))
        if filled > 0.0:
            price = float(order.avg_price)
            if price <= 0.0:
                book = await self._best_book(intent.symbol)
                price = book.mid
            self._append_fill(
                report,
                side=side,
                qty=filled,
                price=price,
                client_id=client_id,
                maker=False,
            )

    async def _place(
        self,
        *,
        symbol: str,
        side: str,
        type_: str,
        quantity: float,
        price: float | None,
        time_in_force: str | None,
        reduce_only: bool,
        client_id: str,
    ) -> OrderResult:
        """下单；post-only 立即被拒视为未成交，状态未知时先查询再决定。"""
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": type_,
            "quantity": _fmt(quantity),
            "newClientOrderId": client_id,
        }
        if price is not None:
            params["price"] = _fmt(price)
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            return await self._client.place_order(**params)
        except ExchangeError as error:
            if error.code == -5022:  # post-only 会立即吃单 → 交易所拒单，视为未成交
                return OrderResult(status="EXPIRED", clientOrderId=client_id, symbol=symbol)
            if _is_unknown_state(error):
                return await self._resolve_unknown(symbol=symbol, client_id=client_id)
            raise ExecutionError(f"{symbol} 下单被拒：{error}") from error
        except Exception as error:  # 网络超时等 → 状态未知，先查询
            try:
                return await self._resolve_unknown(symbol=symbol, client_id=client_id)
            except ExecutionError as query_error:
                raise ExecutionError(
                    f"{symbol} 下单状态未知：{error}（{query_error}）"
                ) from query_error

    async def _resolve_unknown(self, *, symbol: str, client_id: str) -> OrderResult:
        """订单状态未知时按 clientOrderId 查询（红线 7：先查询再重发）。"""
        try:
            return await self._client.get_order(symbol, client_id)
        except ExchangeError as error:
            if error.code == -2013:  # 订单不存在 → 未创建
                raise ExecutionError(f"{symbol} 订单不存在：{client_id}") from error
            raise ExecutionError(
                f"{symbol} 订单状态未知且查询失败：{client_id}：{error}"
            ) from error

    async def _wait(self, *, symbol: str, client_id: str, order: OrderResult) -> OrderResult:
        """等待订单成交或超时，返回最后一笔状态。"""
        waited = 0.0
        current = order
        while current.status in {"NEW", "PARTIALLY_FILLED"} and waited < self._wait_sec:
            await self._sleep(self._poll_sec)
            waited += self._poll_sec
            current = await self._client.get_order(symbol, client_id)
        return current

    async def _cancel(self, *, symbol: str, client_id: str) -> None:
        try:
            await self._client.cancel_order(symbol, client_id)
        except ExchangeError as error:
            if error.code not in {-2011, -2013}:
                raise

    async def _best_book(self, symbol: str) -> Book:
        items = await self._client.book_ticker(symbol)
        if len(items) != 1:
            raise ExecutionError(f"{symbol} 盘口请求返回 {len(items)} 条")
        book = Book(bid=float(items[0].bid_price), ask=float(items[0].ask_price))
        if book.bid <= 0.0 or book.ask <= 0.0 or book.ask < book.bid:
            raise ExecutionError(f"{symbol} 盘口非法：bid={book.bid}, ask={book.ask}")
        return book

    def _append_fill(
        self,
        report: ExecutionReport,
        *,
        side: str,
        qty: float,
        price: float,
        client_id: str,
        maker: bool,
    ) -> None:
        fee = qty * price * self._fee_rate
        report.filled_qty += qty
        report.notional += qty * price
        report.fills.append(
            Fill(
                symbol=report.symbol,
                side=side,
                qty=qty,
                price=price,
                fee=fee,
                client_id=client_id,
                maker=maker,
                ts_ms=self._now_ms(),
            )
        )


def _is_unknown_state(error: BaseException) -> bool:
    """判断订单请求是否处于状态未知（超时 / 5xx）。"""
    if isinstance(error, ExchangeError):
        return error.http_status >= 500 or error.http_status in (408, 504)
    return True


def _ceil_to_tick(value: float, tick: float) -> float:
    return _round_tick(math.ceil(value / tick - 1e-9) * tick, tick)


def _round_tick(value: float, tick: float) -> float:
    text = f"{tick:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return round(value, decimals)


def _fmt(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")
