"""交易所侧止损单管理（STOP_MARKET 条件单，docs/07 §6.5）。

红线 7：任何时刻不得出现“有仓位无止损”的窗口。
- 开仓成交后立刻挂止损并查询确认，确认后才算开仓完成；
- 调整仓位时先挂新止损、确认成功后再撤旧止损；
- 平仓后撤销该币止损单；止损下单失败由调用方立即平仓并告警。

规格：docs/07-实盘与风控.md §6.5
"""

from __future__ import annotations

import contextlib
import math
import re
from typing import Any, Protocol

from trade.live.exchange.client import ExchangeClient, ExchangeError

_PREFIX = "stp-"
_MAX_LEN = 36
_ID_RE = re.compile(r"^[.A-Za-z0-9_:\-/]{1,36}$")
_CONFIRMED_STATUS = frozenset({"NEW", "WORKING", "TRIGGERED", "UNTRIGGERED"})


class StopError(Exception):
    """止损单无法确认（调用方必须立即平仓并告警）。"""


class StopClient(Protocol):
    """止损管理需要的交易所接口子集（测试用 fixture 实现）。"""

    async def place_algo_order(self, **params: Any) -> Any: ...

    async def get_algo_order(self, symbol: str, client_algo_id: str) -> dict[str, Any]: ...

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]: ...

    async def algo_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...


def stop_client_id(alias: str, minute: str, seq: int) -> str:
    """构造 `clientAlgoId`（币安限制 36 字符）。

    Args:
        alias: 币种别名的 ASCII 片段（如 `SOL`）。
        minute: 调仓时刻 `%Y%m%d%H%M`。
        seq: 同一时刻的序号（重挂时递增）。

    Returns:
        合法且唯一的 clientAlgoId。

    Raises:
        ValueError: 结果不满足币安字符集或长度限制。
    """
    suffix = f"{seq:02d}"
    client_id = f"{_PREFIX}{alias.upper()}-{minute}-{suffix}"
    if len(client_id) > _MAX_LEN or _ID_RE.match(client_id) is None:
        raise ValueError(f"止损 clientAlgoId 非法：{client_id}")
    return client_id


def stop_trigger_price(*, side: int, sl: float, reference_price: float, tick_size: float) -> float:
    """按方向与 tickSize 计算止损触发价（docs/07 §6.5）。

    多头止损为卖出、触发价在下方并向下取整；空头止损为买入、触发价在上方并向上取整。

    Args:
        side: +1 多，-1 空。
        sl: 止损距离（0.03 = 3%）。
        reference_price: 参考价（入场价或标记价格）。
        tick_size: 交易对 tickSize。

    Returns:
        已按 tickSize 取整的触发价。

    Raises:
        ValueError: 参数非法。
    """
    if side not in (1, -1):
        raise ValueError(f"止损方向非法：{side}")
    if reference_price <= 0.0 or tick_size <= 0.0:
        raise ValueError(f"止损参考价或 tickSize 非法：{reference_price}, {tick_size}")
    if not 0.0 < sl < 1.0:
        raise ValueError(f"止损距离非法：{sl}")
    raw = reference_price * (1.0 - sl) if side > 0 else reference_price * (1.0 + sl)
    return _floor_to_tick(raw, tick_size) if side > 0 else _ceil_to_tick(raw, tick_size)


class StopManager:
    """止损单的挂出、确认、替换与撤销（docs/07 §6.5）。"""

    def __init__(self, client: ExchangeClient | StopClient) -> None:
        self._client = client

    async def ensure(
        self,
        *,
        symbol: str,
        side: int,
        qty: float,
        trigger_price: float,
        alias: str,
        minute: str,
        seq: int,
        old_client_algo_id: str | None = None,
    ) -> str:
        """挂出新止损并确认；确认成功后才撤销旧止损。

        Args:
            symbol: 交易对。
            side: 持仓方向（+1 多 / -1 空），止损单方向与之相反。
            qty: 当前持仓数量（正数）。
            trigger_price: 已取整的触发价。
            alias: 币种别名片段。
            minute: 调仓时刻 `%Y%m%d%H%M`。
            seq: 重挂序号。
            old_client_algo_id: 被替换的旧止损 ID；None 表示首次挂出。

        Returns:
            新止损的 clientAlgoId。

        Raises:
            StopError: 下单后查询不到或状态不在已确认集合（调用方必须平仓）。
        """
        if qty <= 0.0:
            raise ValueError(f"止损数量必须为正：{symbol} {qty}")
        client_id = stop_client_id(alias, minute, seq)
        await self._client.place_algo_order(
            algoType="CONDITIONAL",
            symbol=symbol,
            side="SELL" if side > 0 else "BUY",
            type="STOP_MARKET",
            quantity=_fmt_qty(qty),
            triggerPrice=_fmt_price(trigger_price),
            workingType="MARK_PRICE",
            reduceOnly="true",
            clientAlgoId=client_id,
        )
        if not await self._confirm(symbol, client_id):
            raise StopError(f"{symbol} 止损单未确认（clientAlgoId={client_id}）")
        if old_client_algo_id is not None and old_client_algo_id != client_id:
            # 先新后旧：新止损已确认，撤旧失败时两张同时存在，不会出现无止损窗口
            with contextlib.suppress(ExchangeError):
                await self._client.cancel_algo_order(symbol=symbol, clientAlgoId=old_client_algo_id)
        return client_id

    async def cancel(self, *, symbol: str, client_algo_id: str) -> None:
        """撤销单个止损单；已不存在时视为成功。"""
        try:
            await self._client.cancel_algo_order(symbol=symbol, clientAlgoId=client_algo_id)
        except ExchangeError as error:
            if error.code not in {-2011, -2013}:
                raise

    async def cancel_symbol(self, symbol: str) -> int:
        """撤销某币全部未触发止损单，返回撤销数量。"""
        orders = await self._client.algo_open_orders(symbol)
        cancelled = 0
        for order in orders:
            client_algo_id = str(order.get("clientAlgoId") or "")
            if not client_algo_id:
                continue
            try:
                await self._client.cancel_algo_order(symbol=symbol, clientAlgoId=client_algo_id)
                cancelled += 1
            except ExchangeError as error:
                if error.code not in {-2011, -2013}:
                    raise
        return cancelled

    async def _confirm(self, symbol: str, client_algo_id: str) -> bool:
        """查询单张条件单确认存在且为有效状态。"""
        try:
            data = await self._client.get_algo_order(symbol, client_algo_id)
        except ExchangeError:
            return False
        status = str(data.get("algoStatus") or data.get("status") or "").upper()
        return status in _CONFIRMED_STATUS


def _floor_to_tick(value: float, tick: float) -> float:
    return _round_tick(math.floor(value / tick + 1e-9) * tick, tick)


def _ceil_to_tick(value: float, tick: float) -> float:
    return _round_tick(math.ceil(value / tick - 1e-9) * tick, tick)


def _round_tick(value: float, tick: float) -> float:
    text = f"{tick:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return round(value, decimals)


def _fmt_qty(qty: float) -> str:
    return f"{qty:.10f}".rstrip("0").rstrip(".")


def _fmt_price(price: float) -> str:
    return f"{price:.10f}".rstrip("0").rstrip(".")
