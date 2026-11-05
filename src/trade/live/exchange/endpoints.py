"""币安 USDⓈ-M 合约 REST 与 WebSocket 端点。

规格：docs/09-币安数据与API.md §2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EnvName = Literal["testnet", "mainnet"]


@dataclass(frozen=True)
class Endpoints:
    """某环境的 REST / WebSocket 基址（09 §2）。

    Args:
        rest_base: REST 基址（不带路径）。
        ws_base: WebSocket 基址（不带 `/ws/`）。
    """

    rest_base: str
    ws_base: str

    def rest_url(self, path: str) -> str:
        """拼接 REST 端点完整 URL。

        Args:
            path: 以 `/` 开头的接口路径，如 `/fapi/v1/ping`。

        Returns:
            完整 URL。
        """
        return f"{self.rest_base}{path}"

    def ws_stream_url(self, streams: list[str]) -> str:
        """拼接组合流 WebSocket URL（09 §2）。"""
        joined = "/".join(streams)
        return f"{self.ws_base}/stream?streams={joined}"


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
            ws_base="wss://stream.binancefuture.com",
        )
    return Endpoints(
        rest_base="https://fapi.binance.com",
        ws_base="wss://fstream.binance.com",
    )
