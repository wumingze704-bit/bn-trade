"""订单状态机单测：开仓 / 止损先挂 / 追踪改单先新后旧 / 幂等。

规格：docs/07-实盘与风控.md §5；AGENTS.md §6 红线 9、13
"""

from __future__ import annotations

from typing import Any

import pytest

from trade.live.exchange.client import ExchangeError
from trade.live.exchange.models import AlgoOrderResult, OrderResult
from trade.live.orders import OrderManager, build_order_id, compute_limit_price, round_step_qty


def _order_result(client_order_id: str = "x", **overrides: Any) -> OrderResult:
    values = {
        "clientOrderId": client_order_id,
        "orderId": 1,
        "symbol": "SOLUSDT",
        "status": "NEW",
        "type": "LIMIT",
        "side": "BUY",
        "executedQty": 0.0,
        "avgPrice": 0.0,
        "origQty": 1.0,
    }
    values.update(overrides)
    return OrderResult.model_validate(values)


def _algo_result(client_id: str = "x") -> AlgoOrderResult:
    return AlgoOrderResult.model_validate(
        {"clientAlgoId": client_id, "algoId": 1, "symbol": "SOLUSDT", "status": "NEW"}
    )


class FakeClient:
    """可编程的假客户端：记录调用、按配置返回或抛错。"""

    def __init__(self) -> None:
        self.placed_orders: list[dict[str, Any]] = []
        self.placed_algos: list[dict[str, Any]] = []
        self.cancelled_algos: list[str] = []
        self.queried: list[str] = []
        self.order_result = _order_result()
        self.algo_result = _algo_result()
        self.order_error: Exception | None = None
        self.algo_error: Exception | None = None
        self.cancel_algo_error: Exception | None = None
        self.query_result: OrderResult | None = None
        self.order_error_remaining: int = 0
        self.algo_error_remaining: int = 0

    async def place_order(self, **params: Any) -> OrderResult:
        self.placed_orders.append(params)
        if self.order_error_remaining > 0:
            self.order_error_remaining -= 1
            raise ExchangeError(code=None, msg="timeout", http_status=504)
        if self.order_error is not None:
            raise self.order_error
        return self.order_result

    async def get_order(self, symbol: str, orig_client_order_id: str) -> OrderResult:
        self.queried.append(orig_client_order_id)
        if self.query_result is not None:
            return self.query_result
        raise ExchangeError(code=-2013, msg="Order does not exist", http_status=400)

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        return {"status": "CANCELED"}

    async def place_algo_order(self, **params: Any) -> AlgoOrderResult:
        self.placed_algos.append(params)
        if self.algo_error_remaining > 0:
            self.algo_error_remaining -= 1
            raise ExchangeError(code=None, msg="timeout", http_status=504)
        if self.algo_error is not None:
            raise self.algo_error
        return self.algo_result

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]:
        self.cancelled_algos.append(str(params.get("clientAlgoId", "")))
        if self.cancel_algo_error is not None:
            raise self.cancel_algo_error
        return {"status": "CANCELED"}


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def manager(client: FakeClient) -> OrderManager:
    return OrderManager(client, {"SOLUSDT": "SOL"})  # type: ignore[arg-type]


def test_build_order_id_within_limit() -> None:
    client_id = build_order_id("SOMELONGALIAS", "202611041030", "S2", 3)
    assert client_id.startswith("trd-")
    assert len(client_id) <= 36


def test_compute_limit_price_floor_to_tick() -> None:
    limit = compute_limit_price(
        best_ask=100.0, tick_rel=0.0001, c_t=100.5, max_chase=0.003, tick_size=0.01
    )
    assert limit == round(limit, 2)
    assert limit <= 100.0 * (1 + 2 * 0.0001)
    assert limit <= 100.5 * (1 + 0.003)


def test_round_step_qty() -> None:
    import pytest

    assert round_step_qty(1.234, 0.1) == pytest.approx(1.2)


def _run(coro: object) -> object:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]


def test_open_order_sends_ioc_buy(manager: OrderManager, client: FakeClient) -> None:
    _run(manager.open_order(symbol="SOLUSDT", t_minute="202611041030", limit_price=99.0, qty=1.0))
    order = client.placed_orders[-1]
    assert order["type"] == "LIMIT"
    assert order["timeInForce"] == "IOC"
    assert order["side"] == "BUY"


def test_place_protective_stop_before_tp(manager: OrderManager, client: FakeClient) -> None:
    result = _run(
        manager.place_protective_orders(
            symbol="SOLUSDT", t_minute="202611041030", stop_price=95.0, cap_price=110.0, qty=1.0
        )
    )
    assert result.stop_placed is True
    assert result.tp_placed is True
    assert client.placed_algos[0]["algoType"] == "STOP_MARKET"
    assert client.placed_orders[-1]["type"] == "LIMIT"


def test_place_protective_stop_failure_returns_false(
    manager: OrderManager, client: FakeClient
) -> None:
    client.algo_error = ExchangeError(code=-4000, msg="fail", http_status=400)
    result = _run(
        manager.place_protective_orders(
            symbol="SOLUSDT", t_minute="202611041030", stop_price=95.0, cap_price=110.0, qty=1.0
        )
    )
    assert result.stop_placed is False
    assert result.tp_placed is False


def test_move_stop_places_new_before_cancel(manager: OrderManager, client: FakeClient) -> None:
    result = _run(
        manager.move_stop(
            symbol="SOLUSDT",
            t_minute="202611041030",
            old_client_id="trd-SOL-x-S1",
            new_stop_price=98.0,
            qty=1.0,
            stop_seq=2,
        )
    )
    assert result.status == "moved"
    assert client.placed_algos[-1]["newClientAlgoId"] == result.new_client_id
    assert client.cancelled_algos == ["trd-SOL-x-S1"]


def test_move_stop_keep_old_when_new_fails(manager: OrderManager, client: FakeClient) -> None:
    client.algo_error = ExchangeError(code=-4000, msg="fail", http_status=400)
    result = _run(
        manager.move_stop(
            symbol="SOLUSDT",
            t_minute="202611041030",
            old_client_id="trd-SOL-x-S1",
            new_stop_price=98.0,
            qty=1.0,
            stop_seq=2,
        )
    )
    assert result.status == "failed"
    assert client.cancelled_algos == []


def test_move_stop_keeps_both_when_cancel_fails(manager: OrderManager, client: FakeClient) -> None:
    client.cancel_algo_error = ExchangeError(code=-4000, msg="fail", http_status=400)
    result = _run(
        manager.move_stop(
            symbol="SOLUSDT",
            t_minute="202611041030",
            old_client_id="trd-SOL-x-S1",
            new_stop_price=98.0,
            qty=1.0,
            stop_seq=2,
        )
    )
    assert result.status == "kept_both"


def test_idempotent_timeout_query_then_resend(manager: OrderManager, client: FakeClient) -> None:
    client.order_error_remaining = 1  # 第一次超时，之后成功
    client.query_result = None
    result = _run(
        manager.open_order(symbol="SOLUSDT", t_minute="202611041030", limit_price=99.0, qty=1.0)
    )
    assert result.order_id == 1
    assert len(client.queried) == 1


def test_idempotent_timeout_query_found_no_resend(
    manager: OrderManager, client: FakeClient
) -> None:
    client.order_error_remaining = 1
    client.query_result = _order_result(client_order_id="trd-SOL-x-E")
    _run(manager.open_order(symbol="SOLUSDT", t_minute="202611041030", limit_price=99.0, qty=1.0))
    assert len(client.placed_orders) == 1
