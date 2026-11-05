"""币安 USDⓈ-M 合约响应模型（pydantic）。

规格：docs/09-币安数据与API.md §3
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RateLimit(BaseModel):
    """exchangeInfo 中的单条限频配置（09 §6）。"""

    model_config = ConfigDict(extra="forbid")

    rate_limit_type: str = Field(alias="rateLimitType")
    interval: str
    interval_num: int = Field(alias="intervalNum")
    limit: int


class ExchangeInfo(BaseModel):
    """`GET /fapi/v1/exchangeInfo` 响应的最小子集（09 §3.1）。"""

    model_config = ConfigDict(extra="allow")

    symbols: list[dict[str, Any]] = Field(default_factory=list)
    rate_limits: list[RateLimit] = Field(default_factory=list, alias="rateLimits")


class SymbolRule(BaseModel):
    """单个合约的交易规则（09 §3.1，从 exchangeInfo 提取）。"""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    status: str
    tick_size: float
    step_size: float
    min_notional: float
    min_qty: float = Field(default=0.0)
    max_leverage: int | None = None


class ServerTime(BaseModel):
    """`GET /fapi/v1/time` 响应（09 §3.1）。"""

    model_config = ConfigDict(extra="allow")

    server_time: int = Field(alias="serverTime")


class BookTicker(BaseModel):
    """`GET /fapi/v1/ticker/bookTicker` 单条响应（09 §3.1）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    bid_price: float = Field(alias="bidPrice")
    ask_price: float = Field(alias="askPrice")


class Ticker24hr(BaseModel):
    """`GET /fapi/v1/ticker/24hr` 单条响应（07 §3.5 动态候选池）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    last_price: float = Field(default=0.0, alias="lastPrice")
    price_change_percent: float = Field(default=0.0, alias="priceChangePercent")


class CommissionRate(BaseModel):
    """`GET /fapi/v1/commissionRate` 响应（09 §4）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    maker_commission_rate: float = Field(alias="makerCommissionRate")
    taker_commission_rate: float = Field(alias="takerCommissionRate")


class PositionSide(BaseModel):
    """`GET /fapi/v1/positionSide/dual` 响应（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    dual_side_position: bool = Field(alias="dualSidePosition")


class AccountPosition(BaseModel):
    """`/fapi/v2` 账户接口中单个持仓（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    position_amt: float = Field(default=0.0, alias="positionAmt")
    entry_price: float = Field(default=0.0, alias="entryPrice")
    un_realized_profit: float = Field(default=0.0, alias="unRealizedProfit")
    position_side: str = Field(default="BOTH", alias="positionSide")


class AccountInfo(BaseModel):
    """账户接口响应的最小子集（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    total_wallet_balance: float = Field(default=0.0, alias="totalWalletBalance")
    available_balance: float = Field(default=0.0, alias="availableBalance")
    total_unrealized_profit: float = Field(default=0.0, alias="totalUnrealizedProfit")
    positions: list[AccountPosition] = Field(default_factory=list)


class OrderResult(BaseModel):
    """`POST /fapi/v1/order` 响应的最小子集（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    client_order_id: str = Field(default="", alias="clientOrderId")
    order_id: int = Field(default=0, alias="orderId")
    symbol: str = ""
    status: str = ""
    order_type: str = Field(default="", alias="type")
    side: str = ""
    executed_qty: float = Field(default=0.0, alias="executedQty")
    cum_qty: float = Field(default=0.0, alias="cumQty")
    avg_price: float = Field(default=0.0, alias="avgPrice")
    orig_qty: float = Field(default=0.0, alias="origQty")
    reduce_only: bool = Field(default=False, alias="reduceOnly")
    stop_price: float = Field(default=0.0, alias="stopPrice")
    update_time: int = Field(default=0, alias="updateTime")


class AlgoOrderResult(BaseModel):
    """`POST /fapi/v1/algoOrder` 响应的最小子集（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    client_algo_id: str = Field(default="", alias="clientAlgoId")
    algo_id: int = Field(default=0, alias="algoId")
    symbol: str = ""
    status: str = ""


class ListenKeyResult(BaseModel):
    """`POST /fapi/v1/listenKey` 响应（09 §3.2）。"""

    model_config = ConfigDict(extra="allow")

    listen_key: str = Field(alias="listenKey")


class UserTrade(BaseModel):
    """`GET /fapi/v1/userTrades` 单条成交（09 §3.2、07 §6）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    id: int
    order_id: int = Field(default=0, alias="orderId")
    side: str
    price: float
    qty: float
    realized_pnl: float = Field(default=0.0, alias="realizedPnl")
    commission: float = Field(default=0.0)
    commission_asset: str = Field(default="USDT", alias="commissionAsset")
    time: int
    buyer: bool = Field(default=False)
    maker: bool = Field(default=False)


class IncomeEntry(BaseModel):
    """`GET /fapi/v1/income` 单条流水（09 §3.2、07 §6）。"""

    model_config = ConfigDict(extra="allow")

    symbol: str
    income_type: str = Field(alias="incomeType")
    income: float
    time: int
    tran_id: int = Field(default=0, alias="tranId")
