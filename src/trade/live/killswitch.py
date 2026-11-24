"""紧急停机：写停机标志 → 停实例 → 撤普通挂单 → 市价平仓 → 清零后撤止损 → 删心跳。

红线 7：任何时刻不得出现“有仓位无交易所止损”。平仓完成且交易所持仓为零之后才撤
条件单；仍有持仓时保留止损、报错并提示人工在币安 App 平仓。

规格：docs/07-实盘与风控.md §7.3
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.orders import round_step_qty
from trade.live.store import Store

SleepFn = Callable[[float], Awaitable[None]]
StopRunner = Callable[[], Awaitable[bool]]
_MAX_CLOSE_ATTEMPTS = 3
_EPS = 1e-9


class KillSwitchError(RuntimeError):
    """停机未完成（实例未确认停止，或平仓后仍有持仓需要人工处理）。"""


async def _close_all_positions(
    client: ExchangeClient,
    *,
    step_sizes: dict[str, float],
    summary: dict[str, Any],
    sleep: SleepFn,
) -> list[str]:
    """市价 reduceOnly 平仓并复核，直到交易所持仓为零或重试耗尽。

    Returns:
        仍剩余的持仓币列表（空表示已清零）。

    Raises:
        KeyError: 持仓币缺少 stepSize（红线 12）。
    """
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
    return [
        str(item.get("symbol"))
        for item in remaining_positions
        if abs(float(item.get("positionAmt", 0.0))) > _EPS
    ]


def _remove_heartbeat(path: Path | None) -> None:
    """删除心跳文件（不存在时忽略；其它 I/O 错误向上抛）。"""
    if path is not None and path.is_file():
        path.unlink()


async def kill_switch(
    *,
    client: ExchangeClient | None,
    store: Store,
    candidate: str,
    mode: str,
    step_sizes: dict[str, float],
    reason: str = "manual",
    stop_runner: StopRunner | None = None,
    heartbeat_path: Path | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> dict[str, Any]:
    """执行停机：写停机标志 → 停实例 → 撤普通挂单 → 平仓 → 撤止损 → 删心跳。

    Args:
        client: 交易所客户端；shadow 模式传 None（不允许任何下单接口）。
        store: 状态库。
        candidate: 候选 ID。
        mode: `shadow` / `paper` / `live`。
        step_sizes: symbol → stepSize（平仓数量取整，红线 12：缺失即抛错）。
        reason: 停机原因（写库与告警）。
        stop_runner: 停止并确认 systemd 实例已停止的函数，返回是否确认停止；
            paper / live 必填（未确认停止时不得操作账户）。
        heartbeat_path: 心跳文件路径；停机完成后删除。
        sleep: 等待函数（测试注入）。

    Returns:
        停机结果字典：是否停机、是否确认停止、撤单数、撤条件单数、已平仓与仍剩余列表。

    Raises:
        KillSwitchError: 实例未确认停止，或平仓后仍有持仓。
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
        "stopped": False,
        "cancelled_orders": 0,
        "cancelled_algos": 0,
        "closed": [],
        "remaining": [],
    }

    # 2. 停止实例并确认：避免停机过程中引擎同时操作账户
    if stop_runner is not None:
        summary["stopped"] = await stop_runner()
    elif mode != "shadow":
        raise KillSwitchError("paper / live 停机必须先停止实例（未提供 stop_runner，未动账户）")
    if mode != "shadow" and not summary["stopped"]:
        raise KillSwitchError("实例未确认停止，未执行撤单与平仓（红线 7）")

    if mode == "shadow":
        _remove_heartbeat(heartbeat_path)
        store.commit()
        return summary
    if client is None:
        raise KillSwitchError("paper / live 停机需要交易所客户端")

    # 3. 只撤普通挂单（开仓用的限价单），保留止损条件单
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
    store.commit()

    # 4. 市价平仓并复核，直到交易所持仓为零（止损单此时仍在）
    summary["remaining"] = await _close_all_positions(
        client, step_sizes=step_sizes, summary=summary, sleep=sleep
    )
    if summary["remaining"]:
        # 仍有持仓：保留全部止损条件单，报错并提示人工处理
        store.insert_event(
            level="error",
            event=f"停机后仍有持仓：{summary['remaining']}；止损单已保留，请在币安 App 手动平仓",
            symbol=None,
            detail={"candidate": candidate, "mode": mode},
        )
        store.commit()
        raise KillSwitchError(f"停机后仍有持仓：{summary['remaining']}（请在币安 App 手动平仓）")

    # 5. 确认持仓为零之后，才撤剩余的条件单（止损）
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

    # 6. 删除心跳文件并写停机完成事件
    _remove_heartbeat(heartbeat_path)
    store.insert_event(
        level="error",
        event=(
            f"停机完成：撤单 {summary['cancelled_orders']}、"
            f"撤条件单 {summary['cancelled_algos']}、"
            f"平仓 {len(summary['closed'])}、剩余 0"
        ),
        symbol=None,
        detail={"candidate": candidate, "mode": mode},
    )
    store.commit()
    return summary
