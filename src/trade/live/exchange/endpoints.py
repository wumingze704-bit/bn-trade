"""币安 USDⓈ-M 合约 REST 与 WebSocket 端点。

规格：docs/09-币安数据与API.md §2、§3.3

正式网与测试网的 USDⓈ-M WebSocket 按流类别拆成三个路由入口（09 §2）：
`/public`（bookTicker、depth）、`/market`（aggTrade、kline、markPrice、ticker）、
`/private`（用户数据流）。不带路由路径的旧入口只会推送 public 类数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EnvName = Literal["testnet", "mainnet"]
WsRoute = Literal["public", "market", "private"]

# 各路由下的流名称前缀（09 §3.3，比较 `@` 之后的部分，区分大小写）。
_MARKET_PREFIXES = (
    "aggTrade",
    "kline_",
    "markPrice",
    "miniTicker",
    "ticker",
    "compositeIndex",
    "contractInfo",
)
_PUBLIC_PREFIXES = ("bookTicker", "depth")
# 用户数据流只有一种：listenKey 建立的私有连接（不走组合流写法）。
_PRIVATE_STREAM = "userData"


def stream_route(stream_name: str) -> WsRoute:
    """按流名称返回所属 WebSocket 路由（09 §2、§3.3）。

    Args:
        stream_name: 组合流名称，如 `btcusdt@aggTrade`、`btcusdt@bookTicker`、`userData`。

    Returns:
        `public`、`market` 或 `private`。

    Raises:
        ValueError: 流名称未登记（红线 13，不回退到默认路由）。
    """
    if stream_name == _PRIVATE_STREAM:
        return "private"
    _, _, suffix = stream_name.partition("@")
    if not suffix:
        raise ValueError(f"未登记的 WebSocket 流名称：{stream_name}（09 §3.3）")
    if suffix.startswith(_PUBLIC_PREFIXES):
        return "public"
    if suffix.startswith(_MARKET_PREFIXES):
        return "market"
    raise ValueError(f"未登记的 WebSocket 流名称：{stream_name}（09 §3.3）")


@dataclass(frozen=True)
class Endpoints:
    """某环境的 REST / WebSocket 基址（09 §2）。

    Args:
        rest_base: REST 基址（不带路径）。
        ws_public_base: public 路由基址（含 `/public`）。
        ws_market_base: market 路由基址（含 `/market`）。
        ws_private_base: private 路由基址（含 `/private`）。
    """

    rest_base: str
    ws_public_base: str
    ws_market_base: str
    ws_private_base: str

    def rest_url(self, path: str) -> str:
        """拼接 REST 端点完整 URL。

        Args:
            path: 以 `/` 开头的接口路径，如 `/fapi/v1/ping`。

        Returns:
            完整 URL。
        """
        return f"{self.rest_base}{path}"

    def ws_base_for(self, route: WsRoute) -> str:
        """返回某路由的基址（09 §2）。"""
        if route == "public":
            return self.ws_public_base
        if route == "market":
            return self.ws_market_base
        return self.ws_private_base

    def ws_stream_url(self, route: WsRoute, streams: list[str]) -> str:
        """拼接组合流 WebSocket URL（09 §2、§3.3）。

        Args:
            route: 流所属路由。
            streams: 组合流名称列表（同一路由内）。

        Returns:
            `{基址}/stream?streams=a/b/...`。

        Raises:
            ValueError: `streams` 为空或包含不属于该路由的流（红线 13）。
        """
        if not streams:
            raise ValueError(f"组合流为空：route={route}（09 §3.3）")
        mismatched = [name for name in streams if stream_route(name) != route]
        if mismatched:
            raise ValueError(f"流 {mismatched[:3]} 不属于路由 {route}（09 §3.3）")
        joined = "/".join(streams)
        return f"{self.ws_base_for(route)}/stream?streams={joined}"

    def ws_user_url(self, listen_key: str) -> str:
        """拼接用户数据流 WebSocket URL（09 §3.3）。"""
        if not listen_key:
            raise ValueError("listenKey 不可为空（09 §3.3）")
        return f"{self.ws_private_base}/ws?listenKey={listen_key}"


def endpoints_for(env: EnvName) -> Endpoints:
    """按运行环境返回端点（09 §2）。

    Args:
        env: `testnet`（paper 模式）或 `mainnet`（shadow/live 模式）。

    Returns:
        对应环境的端点配置。
    """
    if env == "testnet":
        return Endpoints(
            rest_base="https://testnet.binancefuture.com",
            ws_public_base="wss://stream.binancefuture.com/public",
            ws_market_base="wss://stream.binancefuture.com/market",
            ws_private_base="wss://stream.binancefuture.com/private",
        )
    return Endpoints(
        rest_base="https://fapi.binance.com",
        ws_public_base="wss://fstream.binance.com/public",
        ws_market_base="wss://fstream.binance.com/market",
        ws_private_base="wss://fstream.binance.com/private",
    )
