"""币安 USDⓈ-M 合约 REST 客户端封装。

规格：docs/09-币安数据与API.md §3、§5、§6

本模块是 HTTP/REST 层的薄封装：构造签名请求、解析响应、把错误码翻译成
`ExchangeError`。签名规则独立于 `trade.live.exchange.signing`，便于用官方文档
示例做单测核对。生产环境若启用官方 SDK，可在本封装之外替换传输层，签名算法不变。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from trade.live.exchange.endpoints import Endpoints
from trade.live.exchange.models import (
    AccountInfo,
    AlgoOrderResult,
    BookTicker,
    CommissionRate,
    ExchangeInfo,
    ListenKeyResult,
    OrderResult,
    ServerTime,
    Ticker24hr,
    UserTrade,
)
from trade.live.exchange.signing import ed25519_signature, hmac_sha256_signature


class ExchangeError(Exception):
    """币安 API 返回业务错误码或非 2xx 状态时抛出（09 §3）。

    Attributes:
        code: 币安业务错误码（如 -1021）；HTTP 层错误时为 None。
        msg: 错误描述。
        http_status: HTTP 状态码；业务错误（HTTP 200）时为 200。
        retry_after: `Retry-After` 响应头（秒）；仅 429 时可能存在。
    """

    def __init__(
        self,
        *,
        code: int | None,
        msg: str,
        http_status: int,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.msg = msg
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(f"[{code or http_status}] {msg}")


class ExchangeClient:
    """币安 USDⓈ-M 合约 REST 客户端（09 §3）。

    Args:
        endpoints: 环境端点配置（testnet / mainnet）。
        api_key: API Key（`X-MBX-APIKEY` 头）。
        api_secret: HMAC Secret；与 `private_key_hex` 二选一。
        private_key_hex: Ed25519 私钥十六进制；与 `api_secret` 二选一（09 §5.2）。
        recv_window_ms: `recvWindow`（默认 5000）。
        time_offset_ms: 本机与服务器时钟偏移（毫秒），由 `sync_time_offset` 维护。
        client: 可选的 httpx 客户端（测试注入）。
    """

    def __init__(
        self,
        *,
        endpoints: Endpoints,
        api_key: str,
        api_secret: str | None = None,
        private_key_hex: str | None = None,
        recv_window_ms: int = 5000,
        time_offset_ms: int = 0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if api_secret is None and private_key_hex is None:
            raise ValueError("必须提供 api_secret 或 private_key_hex 之一（09 §5.2）")
        if api_secret is not None and private_key_hex is not None:
            raise ValueError("api_secret 与 private_key_hex 只能提供其一（09 §5.2）")
        self._endpoints = endpoints
        self._api_key = api_key
        self._api_secret = api_secret
        self._private_key_hex = private_key_hex
        self._recv_window_ms = recv_window_ms
        self._time_offset_ms = time_offset_ms
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self._owns_client = client is None

    # ---- 时间与基础设施 ----

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, query: str) -> str:
        if self._private_key_hex is not None:
            return ed25519_signature(self._private_key_hex, query)
        assert self._api_secret is not None  # 构造时已校验
        return hmac_sha256_signature(self._api_secret, query)

    @staticmethod
    def _build_query(params: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in sorted(params):
            value = params[key]
            if isinstance(value, bool):
                value = "true" if value else "false"
            parts.append(f"{key}={value}")
        return "&".join(parts)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        """发送请求并把响应解析为 JSON；错误统一抛 `ExchangeError`。"""
        params = dict(params or {})
        if signed:
            params["timestamp"] = self._now_ms()
            params["recvWindow"] = self._recv_window_ms
            query_str = self._build_query(params)
            url = f"{self._endpoints.rest_url(path)}?{query_str}&signature={self._sign(query_str)}"
            query = None
        else:
            url = self._endpoints.rest_url(path)
            query = params if params else None
        headers = {"X-MBX-APIKEY": self._api_key} if signed else {}
        response = await self._client.request(method, url, params=query, headers=headers)
        try:
            data = response.json()
        except ValueError:
            data = response.text
        code = data.get("code") if isinstance(data, dict) else None
        is_business_error = isinstance(data, dict) and isinstance(code, int) and code < 0
        if response.status_code >= 400 or is_business_error:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after = float(retry_after_raw) if retry_after_raw else None
            raise ExchangeError(
                code=code if isinstance(code, int) else None,
                msg=str(data.get("msg", response.text)) if isinstance(data, dict) else str(data),
                http_status=response.status_code,
                retry_after=retry_after,
            )
        return data

    async def sync_time_offset(self) -> int:
        """请求服务器时间 3 次，取本机与服务器时差中位数并写入偏移（07 §2 第 2 步）。"""
        offsets: list[int] = []
        for _ in range(3):
            before = int(time.time() * 1000)
            server = await self.server_time()
            after = int(time.time() * 1000)
            offsets.append(server.server_time - (before + after) // 2)
        offsets.sort()
        self._time_offset_ms = offsets[len(offsets) // 2]
        return self._time_offset_ms

    async def aclose(self) -> None:
        """关闭底层 httpx 客户端（仅当由本类创建时）。"""
        if self._owns_client:
            await self._client.aclose()

    # ---- 公开行情（09 §3.1）----

    async def ping(self) -> dict[str, Any]:
        return dict(await self._request("GET", "/fapi/v1/ping"))

    async def server_time(self) -> ServerTime:
        data = await self._request("GET", "/fapi/v1/time")
        return ServerTime.model_validate(data)

    async def exchange_info(self) -> ExchangeInfo:
        data = await self._request("GET", "/fapi/v1/exchangeInfo")
        return ExchangeInfo.model_validate(data)

    async def klines(self, symbol: str, interval: str, *, limit: int = 500) -> list[list[Any]]:
        data = await self._request(
            "GET",
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )
        return data if isinstance(data, list) else []

    async def agg_trades(
        self, symbol: str, *, from_id: int | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if from_id is not None:
            params["fromId"] = from_id
        data = await self._request("GET", "/fapi/v1/aggTrades", params=params)
        return data if isinstance(data, list) else []

    async def book_ticker(self, symbol: str | None = None) -> list[BookTicker]:
        params = {"symbol": symbol} if symbol else None
        data = await self._request("GET", "/fapi/v1/ticker/bookTicker", params=params)
        items = data if isinstance(data, list) else [data]
        return [BookTicker.model_validate(item) for item in items]

    async def ticker_24hr(self) -> list[Ticker24hr]:
        """获取全市场 24 小时行情（07 §3.5 动态候选池涨幅榜）。"""
        data = await self._request("GET", "/fapi/v1/ticker/24hr")
        items = data if isinstance(data, list) else []
        return [Ticker24hr.model_validate(item) for item in items]

    async def premium_index(self, symbol: str) -> dict[str, Any]:
        return dict(await self._request("GET", "/fapi/v1/premiumIndex", params={"symbol": symbol}))

    async def funding_rate(self, symbol: str, *, limit: int = 500) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": limit},
        )
        return data if isinstance(data, list) else []

    # ---- 账户与交易规则（09 §3.2）----

    async def commission_rate(self, symbol: str) -> CommissionRate:
        data = await self._request(
            "GET", "/fapi/v1/commissionRate", params={"symbol": symbol}, signed=True
        )
        return CommissionRate.model_validate(data)

    async def leverage_bracket(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        data = await self._request("GET", "/fapi/v1/leverageBracket", params=params, signed=True)
        return data if isinstance(data, list) else []

    async def dual_side_position(self) -> bool:
        data = await self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        return bool(data.get("dualSidePosition", False))

    async def set_dual_side_position(self, dual: bool) -> None:
        await self._request(
            "POST",
            "/fapi/v1/positionSide/dual",
            params={"dualSidePosition": "true" if dual else "false"},
            signed=True,
        )

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        await self._request(
            "POST",
            "/fapi/v1/marginType",
            params={"symbol": symbol, "marginType": margin_type},
            signed=True,
        )

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": leverage},
            signed=True,
        )

    async def account(self) -> AccountInfo:
        data = await self._request("GET", "/fapi/v2/account", signed=True)
        return AccountInfo.model_validate(data)

    async def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        data = await self._request("GET", "/fapi/v2/positionRisk", params=params, signed=True)
        return data if isinstance(data, list) else []

    # ---- 订单（09 §3.2、07 §5）----

    async def place_order(self, **params: Any) -> OrderResult:
        data = await self._request("POST", "/fapi/v1/order", params=params, signed=True)
        return OrderResult.model_validate(data)

    async def get_order(self, symbol: str, orig_client_order_id: str) -> OrderResult:
        data = await self._request(
            "GET",
            "/fapi/v1/order",
            params={"symbol": symbol, "origClientOrderId": orig_client_order_id},
            signed=True,
        )
        return OrderResult.model_validate(data)

    async def cancel_order(self, symbol: str, orig_client_order_id: str) -> dict[str, Any]:
        return dict(
            await self._request(
                "DELETE",
                "/fapi/v1/order",
                params={"symbol": symbol, "origClientOrderId": orig_client_order_id},
                signed=True,
            )
        )

    async def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        data = await self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)
        return data if isinstance(data, list) else []

    async def cancel_all_open_orders(self, symbol: str | None = None) -> None:
        params = {"symbol": symbol} if symbol else None
        await self._request("DELETE", "/fapi/v1/allOpenOrders", params=params, signed=True)

    # ---- 算法条件单（09 §3.2、07 §5.2）----

    async def place_algo_order(self, **params: Any) -> AlgoOrderResult:
        data = await self._request("POST", "/fapi/v1/algoOrder", params=params, signed=True)
        return AlgoOrderResult.model_validate(data)

    async def cancel_algo_order(self, **params: Any) -> dict[str, Any]:
        return dict(await self._request("DELETE", "/fapi/v1/algoOrder", params=params, signed=True))

    async def algo_open_orders(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/fapi/v1/algoOpenOrders", signed=True)
        return data if isinstance(data, list) else []

    # ---- 成交与资金流（09 §3.2、07 §6）----

    async def user_trades(
        self,
        symbol: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[UserTrade]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = await self._request("GET", "/fapi/v1/userTrades", params=params, signed=True)
        items = data if isinstance(data, list) else []
        return [UserTrade.model_validate(item) for item in items]

    async def income(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = await self._request("GET", "/fapi/v1/income", params=params, signed=True)
        return data if isinstance(data, list) else []

    # ---- 用户数据流（09 §3.2）----

    async def create_listen_key(self) -> ListenKeyResult:
        data = await self._request("POST", "/fapi/v1/listenKey", signed=True)
        return ListenKeyResult.model_validate(data)

    async def renew_listen_key(self, listen_key: str) -> None:
        await self._request(
            "PUT",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            signed=True,
        )

    async def close_listen_key(self, listen_key: str) -> None:
        await self._request(
            "DELETE",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            signed=True,
        )
