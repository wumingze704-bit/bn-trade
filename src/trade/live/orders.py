"""订单执行与状态机（开仓 / 止损先挂 / 追踪改单先新后旧 / 幂等）。

规格：docs/07-实盘与风控.md §5

红线（AGENTS.md §6）：
- 红线 9：任何时刻不出现「有仓位无止损」窗口——止损先于止盈挂出。
- 红线 13：追踪改单必须先挂新单、确认成功再撤旧单（先新后旧，撤旧失败保留两张）。
- 幂等：超时 / 5xx 先按 clientOrderId 查询再重发。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.exchange.models import AlgoOrderResult, OrderResult

# 订单 ID 前缀（07 §5.4）
_CLIENT_ID_PREFIX = "trd-"
# 订单 ID 上限（币安 36 字符，07 §5.4）
_CLIENT_ID_MAX = 36
# 止损单重试次数上限（07 §5.2）
_STOP_RETRY = 2
# 追踪改单挂新单重试次数（07 §5.2）
_MOVE_RETRY = 1

# 出场类型订单 ID 片段（07 §5.4）：开仓 E / 止盈 TP / 止损 S{n} / 超时市价平仓 TX
_KIND_OPEN = "E"
_KIND_TP = "TP"
_KIND_TIMEOUT = "TX"


def build_order_id(alias: str, t_minute: str, kind: str, retry: int) -> str:
    """构造 `newClientOrderId`（07 §5.4）。

    Args:
        alias: `data/universe/aliases.json` 中的 ASCII 别名。
        t_minute: 信号分钟（`%Y%m%d%H%M` 格式）。
        kind: `E` / `TP` / `S{n}` / `TX`。
        retry: 重试序号（0 起）。

    Returns:
        长度不超过 36 字符的订单 ID。

    Raises:
        ValueError: 结果超过 36 字符。
    """
    retry_part = f"{retry}" if retry > 0 else ""
    client_id = f"{_CLIENT_ID_PREFIX}{alias}-{t_minute}-{kind}{retry_part}"
    if len(client_id) > _CLIENT_ID_MAX:
        raise ValueError(f"订单 ID 超过 {_CLIENT_ID_MAX} 字符：{client_id}")
    return client_id


def compute_limit_price(
    *, best_ask: float, tick_rel: float, c_t: float, max_chase: float, tick_size: float
) -> float:
    """计算开仓限价并按 tickSize 向下取整（07 §5.1）。

    限价 = min(最优卖价 × (1 + 2 × tick_rel), c_t × (1 + max_chase))。
    """
    ask_cap = best_ask * (1.0 + 2.0 * tick_rel)
    chase_cap = c_t * (1.0 + max_chase)
    limit = min(ask_cap, chase_cap)
    return math.floor(limit / tick_size + 1e-9) * tick_size


def round_step_qty(qty: float, step_size: float) -> float:
    """按 stepSize 向下取整数量（07 §5.1）。"""
    return math.floor(qty / step_size + 1e-9) * step_size


@dataclass(frozen=True)
class OpenResult:
    """开仓结果（07 §5.1）。"""

    order: OrderResult
    # 是否已挂好止损（True 表示止损 + 止盈已就绪）
    protective_ok: bool
    partial: bool


@dataclass(frozen=True)
class ProtectiveResult:
    """止损 / 止盈挂单结果（07 §5.2）。"""

    stop_placed: bool
    tp_placed: bool
    stop_client_id: str
    tp_client_id: str


@dataclass(frozen=True)
class MoveStopResult:
    """追踪改单结果（07 §5.2）。"""

    status: str  # "moved" | "kept_both" | "failed"
    new_client_id: str
    old_client_id: str


def _is_unknown_state(error: BaseException) -> bool:
    """判断订单请求是否处于状态未知（超时 / 5xx，07 §5.4）。"""
    if isinstance(error, ExchangeError):
        return error.http_status >= 500 or error.http_status in (408, 504)
    return True  # httpx.TimeoutException / 连接错误等均为未知状态


class OrderManager:
    """订单执行状态机（07 §5）。

    Args:
        client: 币安客户端（`trade.live.exchange.ExchangeClient`）。
        alias_by_symbol: symbol -> ASCII 别名映射（data/universe/aliases.json）。
    """

    def __init__(self, client: ExchangeClient, alias_by_symbol: dict[str, str]) -> None:
        self._client = client
        self._alias = alias_by_symbol

    def alias(self, symbol: str) -> str:
        """返回 symbol 的 ASCII 别名（缺省时用 symbol 去尾 USDT）。"""
        return self._alias.get(symbol, symbol.removesuffix("USDT"))

    # ---- 开仓（07 §5.1）----

    async def open_order(
        self,
        *,
        symbol: str,
        t_minute: str,
        limit_price: float,
        qty: float,
        retry: int = 0,
    ) -> OrderResult:
        """发送 LIMIT IOC 开仓单（07 §5.1）。"""
        client_id = build_order_id(self.alias(symbol), t_minute, _KIND_OPEN, retry)
        return await self._place_order_idempotent(
            symbol=symbol,
            client_id=client_id,
            retry=retry,
            side="BUY",
            order_type="LIMIT",
            time_in_force="IOC",
            quantity=qty,
            price=limit_price,
        )

    # ---- 止损 / 止盈（07 §5.2，止损先于止盈）----

    async def place_stop(
        self,
        *,
        symbol: str,
        t_minute: str,
        stop_price: float,
        qty: float,
        stop_seq: int,
        retry: int = 0,
    ) -> AlgoOrderResult:
        """挂止损（算法条件市价单 STOP_MARKET，07 §5.2）。"""
        client_id = build_order_id(self.alias(symbol), t_minute, f"S{stop_seq}", retry)
        return await self._place_algo_idempotent(
            symbol=symbol,
            client_id=client_id,
            retry=retry,
            algo_type="STOP_MARKET",
            side="SELL",
            quantity=qty,
            reduce_only=True,
            trigger_price=stop_price,
            trigger_price_type="CONTRACT_PRICE",
        )

    async def place_take_profit(
        self,
        *,
        symbol: str,
        t_minute: str,
        cap_price: float,
        qty: float,
        retry: int = 0,
    ) -> OrderResult:
        """挂封顶止盈（LIMIT GTC reduceOnly，07 §5.2）。"""
        client_id = build_order_id(self.alias(symbol), t_minute, _KIND_TP, retry)
        return await self._place_order_idempotent(
            symbol=symbol,
            client_id=client_id,
            retry=retry,
            side="SELL",
            order_type="LIMIT",
            time_in_force="GTC",
            quantity=qty,
            price=cap_price,
            reduce_only=True,
        )

    async def place_protective_orders(
        self,
        *,
        symbol: str,
        t_minute: str,
        stop_price: float,
        cap_price: float,
        qty: float,
    ) -> ProtectiveResult:
        """成交后立即挂止损 + 止盈，止损先于止盈（07 §5.2、红线 9）。

        止损失败重试 `_STOP_RETRY` 次仍失败时返回 stop_placed=False，由调用方
        立即市价平仓（07 §5.2）。
        """
        alias = self.alias(symbol)
        stop_client_id = build_order_id(alias, t_minute, "S1", 0)
        tp_client_id = build_order_id(alias, t_minute, _KIND_TP, 0)
        stop_placed = False
        for attempt in range(_STOP_RETRY + 1):
            try:
                await self._place_algo_idempotent(
                    symbol=symbol,
                    client_id=build_order_id(alias, t_minute, "S1", attempt),
                    retry=attempt,
                    algo_type="STOP_MARKET",
                    side="SELL",
                    quantity=qty,
                    reduce_only=True,
                    trigger_price=stop_price,
                    trigger_price_type="CONTRACT_PRICE",
                )
                stop_placed = True
                break
            except ExchangeError:
                continue
        if not stop_placed:
            return ProtectiveResult(False, False, stop_client_id, tp_client_id)
        try:
            await self.place_take_profit(
                symbol=symbol, t_minute=t_minute, cap_price=cap_price, qty=qty
            )
            tp_placed = True
        except ExchangeError:
            tp_placed = False
        return ProtectiveResult(True, tp_placed, stop_client_id, tp_client_id)

    # ---- 追踪改单（07 §5.2，先新后旧）----

    async def move_stop(
        self,
        *,
        symbol: str,
        t_minute: str,
        old_client_id: str,
        new_stop_price: float,
        qty: float,
        stop_seq: int,
    ) -> MoveStopResult:
        """追踪改单：先挂新单、确认成功再撤旧单（07 §5.2、红线 13）。

        - 挂新失败：保留旧止损，重试 1 次，仍失败返回 failed。
        - 撤旧失败：保留两张单（均为 reduceOnly）。
        """
        alias = self.alias(symbol)
        new_client_id = build_order_id(alias, t_minute, f"S{stop_seq}", 0)
        placed = False
        for attempt in range(_MOVE_RETRY + 1):
            try:
                await self._place_algo_idempotent(
                    symbol=symbol,
                    client_id=build_order_id(alias, t_minute, f"S{stop_seq}", attempt),
                    retry=attempt,
                    algo_type="STOP_MARKET",
                    side="SELL",
                    quantity=qty,
                    reduce_only=True,
                    trigger_price=new_stop_price,
                    trigger_price_type="CONTRACT_PRICE",
                )
                placed = True
                break
            except ExchangeError:
                continue
        if not placed:
            return MoveStopResult("failed", new_client_id, old_client_id)
        try:
            await self._client.cancel_algo_order(symbol=symbol, clientAlgoId=old_client_id)
            return MoveStopResult("moved", new_client_id, old_client_id)
        except ExchangeError:
            return MoveStopResult("kept_both", new_client_id, old_client_id)

    # ---- 市价平仓（07 §5.3）----

    async def close_market(
        self,
        *,
        symbol: str,
        t_minute: str,
        qty: float,
        retry: int = 0,
    ) -> OrderResult:
        """市价平仓（MARKET reduceOnly，07 §5.3、§7.3）。"""
        client_id = build_order_id(self.alias(symbol), t_minute, _KIND_TIMEOUT, retry)
        return await self._place_order_idempotent(
            symbol=symbol,
            client_id=client_id,
            retry=retry,
            side="SELL",
            order_type="MARKET",
            quantity=qty,
            reduce_only=True,
        )

    # ---- 幂等（07 §5.4）----

    async def _place_order_idempotent(
        self,
        *,
        symbol: str,
        client_id: str,
        retry: int,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        time_in_force: str | None = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        """下单并处理状态未知：先按 clientOrderId 查询，不存在才重发（07 §5.4）。"""
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "reduceOnly": "true" if reduce_only else "false",
            "newClientOrderId": client_id,
            "newOrderRespType": "RESULT",
        }
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        try:
            return await self._client.place_order(**params)
        except BaseException as error:
            if not _is_unknown_state(error):
                raise
            existing = await self._query_order(symbol, client_id)
            if existing is not None:
                return existing
            if retry >= 2:
                raise
            return await self._place_order_idempotent(
                symbol=symbol,
                client_id=client_id,
                retry=retry + 1,
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=price,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
            )

    async def _place_algo_idempotent(
        self,
        *,
        symbol: str,
        client_id: str,
        retry: int,
        algo_type: str,
        side: str,
        quantity: float,
        reduce_only: bool,
        trigger_price: float,
        trigger_price_type: str,
    ) -> AlgoOrderResult:
        """挂算法条件单并处理状态未知（07 §5.2、§5.4）。"""
        params: dict[str, Any] = {
            "symbol": symbol,
            "algoType": algo_type,
            "side": side,
            "quantity": quantity,
            "reduceOnly": "true" if reduce_only else "false",
            "triggerPrice": trigger_price,
            "triggerPriceType": trigger_price_type,
            "workingType": "CONTRACT_PRICE",
            "newClientAlgoId": client_id,
        }
        try:
            return await self._client.place_algo_order(**params)
        except BaseException as error:
            if not _is_unknown_state(error):
                raise
            if retry >= 2:
                raise
            return await self._place_algo_idempotent(
                symbol=symbol,
                client_id=client_id,
                retry=retry + 1,
                algo_type=algo_type,
                side=side,
                quantity=quantity,
                reduce_only=reduce_only,
                trigger_price=trigger_price,
                trigger_price_type=trigger_price_type,
            )

    async def _query_order(self, symbol: str, client_id: str) -> OrderResult | None:
        """按 clientOrderId 查询订单；不存在（-2013）返回 None。"""
        try:
            return await self._client.get_order(symbol, client_id)
        except ExchangeError as error:
            if error.code == -2013:
                return None
            raise
