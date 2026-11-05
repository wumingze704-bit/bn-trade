"""对账：仓位 6 种情况 + 成交/资金费/手续费差异补齐。

规格：docs/07-实盘与风控.md §6.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReconAction:
    """单条对账动作（07 §6.2），由引擎执行。"""

    kind: str
    symbol: str
    detail: dict[str, Any] = field(default_factory=dict)


def reconcile_positions(
    *,
    local_open: list[dict[str, Any]],
    exchange_positions: dict[str, float],
    exchange_stop_by_symbol: dict[str, float],
    open_order_client_ids: set[str],
    local_stop_by_symbol: dict[str, float],
    sl_max: float,
) -> list[ReconAction]:
    """按 07 §6.2 的 6 种情况逐币对账，返回需要执行的动作列表。

    Args:
        local_open: 状态库中 OPEN 持仓。
        exchange_positions: symbol -> 交易所持仓数量（非零）。
        exchange_stop_by_symbol: symbol -> 交易所当前止损触发价（无止损则缺失）。
        open_order_client_ids: 交易所 `trd-` 前缀挂单的 clientId 集合。
        local_stop_by_symbol: symbol -> 本地记录的止损价。
        sl_max: 接管仓位使用的止损幅度（07 §6.2 情况 1）。

    Returns:
        需要执行的动作列表。
    """
    actions: list[ReconAction] = []
    local_by_symbol = {p["symbol"]: p for p in local_open}

    # 情况 1：交易所有仓位、本地无记录 → 接管
    for symbol, qty in exchange_positions.items():
        if symbol not in local_by_symbol:
            actions.append(ReconAction("adopt", symbol, {"qty": qty, "sl_max": sl_max}))

    # 情况 3 / 6：本地有仓位，交易所无仓位或数量不一致
    for symbol, position in local_by_symbol.items():
        if symbol not in exchange_positions:
            actions.append(
                ReconAction("mark_closed", symbol, {"position_id": position["position_id"]})
            )
            continue
        if abs(exchange_positions[symbol] - float(position["qty"])) > 1e-9:
            actions.append(
                ReconAction(
                    "update_qty",
                    symbol,
                    {"position_id": position["position_id"], "qty": exchange_positions[symbol]},
                )
            )

    # 情况 2 / 4：止损价或止损单有效性
    for symbol, position in local_by_symbol.items():
        if symbol not in exchange_positions:
            continue  # 已在情况 3 处理
        local_stop = local_stop_by_symbol.get(symbol)
        exchange_stop = exchange_stop_by_symbol.get(symbol)
        if exchange_stop is None:
            # 情况 4：有仓位但没有有效止损单 → 补挂
            actions.append(
                ReconAction(
                    "place_stop",
                    symbol,
                    {"position_id": position["position_id"], "stop_price": local_stop},
                )
            )
        elif local_stop is not None and exchange_stop < local_stop:
            # 情况 2：交易所止损价低于本地 → 改单未完成，补挂
            actions.append(
                ReconAction(
                    "place_stop",
                    symbol,
                    {"position_id": position["position_id"], "stop_price": local_stop},
                )
            )

    # 情况 5：trd- 挂单但对应仓位已不存在 → 撤销
    for client_id in open_order_client_ids:
        if client_id not in local_by_symbol and _symbol_of(client_id) not in local_by_symbol:
            actions.append(
                ReconAction("cancel_order", _symbol_of(client_id), {"client_id": client_id})
            )

    return actions


def _symbol_of(client_id: str) -> str:
    """从 `trd-{alias}-...` 提取别名（无法还原 symbol，仅用于日志关联）。"""
    return client_id


@dataclass(frozen=True)
class FillReconResult:
    """成交/资金费/手续费差异补齐结果（07 §6.1）。"""

    missing_fills: list[dict[str, Any]] = field(default_factory=list)
    missing_income: list[dict[str, Any]] = field(default_factory=list)
    cancelled_client_ids: list[str] = field(default_factory=list)


def reconcile_fills(
    *,
    local_fill_ids: set[int],
    local_income_tran_ids: set[int],
    exchange_trades: list[dict[str, Any]],
    exchange_income: list[dict[str, Any]],
    local_open_client_ids: set[str],
    exchange_open_client_ids: set[str],
) -> FillReconResult:
    """补齐断线缺口（07 §6.1）。

    - 成交未记：exchange_trades 中不在 local_fill_ids 的成交 → 补记。
    - 记了未成交 / 撤单：本地有、交易所挂单集合没有的 clientId → 标记撤单。
    - 资金费 / 手续费：exchange_income 中不在 local_income_tran_ids 的流水 → 补记。
    """
    missing_fills = [t for t in exchange_trades if int(t.get("id", 0)) not in local_fill_ids]
    missing_income = [
        i for i in exchange_income if int(i.get("tranId", 0)) not in local_income_tran_ids
    ]
    cancelled = sorted(local_open_client_ids - exchange_open_client_ids)
    return FillReconResult(
        missing_fills=missing_fills,
        missing_income=missing_income,
        cancelled_client_ids=cancelled,
    )
