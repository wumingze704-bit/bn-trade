"""币安 USDⓈ-M 合约客户端接口包。

规格：docs/02-系统架构与部署.md §2；docs/09-币安数据与API.md §3
"""

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.exchange.endpoints import Endpoints, endpoints_for
from trade.live.exchange.models import (
    AccountInfo,
    AccountPosition,
    AlgoOrderResult,
    BookTicker,
    CommissionRate,
    ExchangeInfo,
    ListenKeyResult,
    OrderResult,
    PositionSide,
    RateLimit,
    ServerTime,
    SymbolRule,
    UserTrade,
)
from trade.live.exchange.signing import (
    ed25519_signature,
    hmac_sha256_signature,
    private_key_kind,
    rsa_signature,
)
from trade.live.exchange.ws import (
    AggTradeEvent,
    BookTickerEvent,
    KlineEvent,
    WsConnection,
    parse_agg_trade,
    parse_book_ticker,
    parse_kline,
    parse_ws_message,
)

__all__ = [
    "AccountInfo",
    "AccountPosition",
    "AggTradeEvent",
    "AlgoOrderResult",
    "BookTicker",
    "BookTickerEvent",
    "CommissionRate",
    "Endpoints",
    "ExchangeClient",
    "ExchangeError",
    "ExchangeInfo",
    "KlineEvent",
    "ListenKeyResult",
    "OrderResult",
    "PositionSide",
    "RateLimit",
    "ServerTime",
    "SymbolRule",
    "UserTrade",
    "WsConnection",
    "ed25519_signature",
    "endpoints_for",
    "hmac_sha256_signature",
    "parse_agg_trade",
    "parse_book_ticker",
    "parse_kline",
    "parse_ws_message",
    "private_key_kind",
    "rsa_signature",
]
