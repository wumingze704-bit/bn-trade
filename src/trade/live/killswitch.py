"""紧急停机：撤全部挂单与条件单、平掉全部仓位、写停机标志（docs/07 §7.3）。

红线 7：先写停机标志（引擎立即拒绝开新仓），再撤单、再平仓，最后用 REST 确认
仓位为零。脚本与看板控制页共用本函数。

规格：docs/07-实盘与风控.md §7.3
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.orders import round_step_qty
from trade.live.store import Store

SleepFn = Callable[[float], Awaitable[None]]
_MAX_CLOSE_ATTEMPTS = 3
_EPS = 1e-9


class KillSwitchError(RuntimeError):
    """停机后仍有持仓（需要人工在币安 App 处理，并在报告中记录）。"""


async def kill_switch(
    *,
    client: ExchangeClient | None,
    store: Store,
    candidate: str,
    mode: str,
    step_sizes: dict[str, float],
    reason: str = "manual",
    sleep: SleepFn = asyncio.sleep,
) -> dict[str, Any]:
    """执行停机：写停机标志 → 撤挂单与条件单 → 市价平仓 → 确认清零。

    Args:
        client: 交易所客户端；shadow 模式传 None（不允许任何下单接口）。
        store: 状态库。
        candidate: 候选 ID。
        mode: `shadow` / `paper` / `live`。
        step_sizes: symbol → stepSize（平仓数量取整，红线 12：缺失即抛错）。
        reason: 停机原因（写库与告警）。
        sleep: 等待函数（测试注入）。

    Returns:
        停机结果字典：是否停机、撤单数、撤条件单数、已平仓列表与仍剩余列表。

    Raises:
        KillSwitchError: 平仓后仍有持仓。
        KeyError: 需要平仓的币缺少 stepSize。
    """
    # 1. 先写停机标志：引擎从下一轮巡检起拒绝开新仓
    store.set_engine_halt(candidate=candidate, mode=mode, halted=True, reason=reason)
    store.insert_event(
        level="error",
        event=f"停机（{reason}）",
        symbol=None,
        detail={"candidate": candidate, "mode": mode},
    )
    store.commit()
    summary: dict[str, Any] = {
        "halted": True,
        "cancelled_orders": 0,
        "cancelled_algos": 0,
        "closed": [],
        "remaining": [],
    }
    if mode == "shadow":
        store.commit()
        return summary
    if client is None:
        raise KillSwitchError("paper / live 停机需要交易所客户端")
    # 2. 撤全部普通挂单
    for order in await client.open_orders():
        symbol = str(order.get("symbol", ""))
        client_id = str(order.get("clientOrderId", ""))
        if not symbol or not client_id:
            continue
        try:
            await client.cancel_order(symbol, client_id)
            summary["cancelled_orders"] += 1
        except ExchangeError as error:
            if error.code not in {-2011, -2013}:
                raise
    # 3. 撤全部条件单
    for algo in await client.algo_open_orders():
        symbol = str(algo.get("symbol", ""))
        client_algo_id = str(algo.get("clientAlgoId", ""))
        if not symbol or not client_algo_id:
            continue
        try:
            await client.cancel_algo_order(symbol=symbol, clientAlgoId=client_algo_id)
            summary["cancelled_algos"] += 1
        except ExchangeError as error:
            if error.code not in {-2011, -2013}:
                raise
    # 4. 市价平仓（reduceOnly），并在每轮后复核
    for attempt in range(_MAX_CLOSE_ATTEMPTS):
        positions = await client.position_risk()
        open_positions = [
            item for item in positions if abs(float(item.get("positionAmt", 0.0))) > _EPS
        ]
        if not open_positions:
            break
        if attempt > 0:
            await sleep(float(attempt))
        for item in open_positions:
            symbol = str(item.get("symbol", ""))
            qty = float(item.get("positionAmt", 0.0))
            if symbol not in step_sizes:
                raise KeyError(f"{symbol} 缺少 stepSize，无法平仓（红线 12）")
            quantity = round_step_qty(abs(qty), step_sizes[symbol])
            if quantity <= _EPS:
                continue
            await client.place_order(
                symbol=symbol,
                side="SELL" if qty > 0 else "BUY",
                type="MARKET",
                quantity=f"{quantity:.10f}".rstrip("0").rstrip("."),
                reduceOnly="true",
            )
            summary["closed"].append({"symbol": symbol, "qty": -qty})
    remaining_positions = await client.position_risk()
    remaining = [
        str(item.get("symbol"))
        for item in remaining_positions
        if abs(float(item.get("positionAmt", 0.0))) > _EPS
    ]
    summary["remaining"] = remaining
    store.insert_event(
        level="error",
        event=(
            f"停机完成：撤单 {summary['cancelled_orders']}、"
            f"撤条件单 {summary['cancelled_algos']}、"
            f"平仓 {len(summary['closed'])}、剩余 {len(remaining)}"
        ),
        symbol=None,
        detail={"candidate": candidate, "mode": mode},
    )
    store.commit()
    if remaining:
        raise KillSwitchError(f"停机后仍有持仓：{remaining}（请在币安 App 手动平仓）")
    return summary
