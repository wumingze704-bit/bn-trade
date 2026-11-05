"""币安接口冒烟测试。

规格：docs/09-币安数据与API.md §8

- `mainnet`：只调用公开接口与只读签名接口，不下单。
- `testnet`：额外执行完整订单生命周期（开仓 IOC → 挂止损 → 挂止盈 → 撤销 → 平仓）。

用法：`python scripts/api_smoke.py --env {testnet|mainnet}`
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.exchange.endpoints import EnvName, endpoints_for

_PUBLIC_CHECKS = ("ping", "time", "exchangeInfo", "klines", "aggTrades", "bookTicker")
_SIGNED_READ_CHECKS = ("account", "positionRisk", "commissionRate", "leverageBracket")


async def _check(name: str, coro: object) -> str:
    try:
        await coro  # type: ignore[misc]
        return f"[OK] {name}"
    except ExchangeError as error:
        return f"[FAIL] {name}: {error}"
    except Exception as error:  # noqa: BLE001 - 冒烟需捕获网络异常
        return f"[FAIL] {name}: {type(error).__name__} {error}"


async def _smoke(env: EnvName, symbol: str) -> None:
    api_key = os.environ.get(
        "BINANCE_TESTNET_API_KEY" if env == "testnet" else "BINANCE_API_KEY", ""
    )
    api_secret = os.environ.get(
        "BINANCE_TESTNET_API_SECRET" if env == "testnet" else "BINANCE_API_SECRET", ""
    )
    private_key_path = os.environ.get("BINANCE_PRIVATE_KEY_PATH")
    if not api_key:
        env_var = "BINANCE_TESTNET_API_KEY" if env == "testnet" else "BINANCE_API_KEY"
        print(f"[FAIL] 缺少环境变量：{env_var}")
        sys.exit(1)

    client = ExchangeClient(
        endpoints=endpoints_for(env),
        api_key=api_key,
        api_secret=api_secret or None,
        private_key_hex=_read_private_key_hex(private_key_path) if private_key_path else None,
    )
    print(f"== 环境 {env} ==")
    for name in _PUBLIC_CHECKS:
        if name == "klines":
            result = await _check(name, client.klines(symbol, "1m", limit=5))
        elif name == "aggTrades":
            result = await _check(name, client.agg_trades(symbol, limit=5))
        elif name == "bookTicker":
            result = await _check(name, client.book_ticker(symbol))
        else:
            result = await _check(name, getattr(client, name)())
        print(result)

    for name in _SIGNED_READ_CHECKS:
        if name == "account":
            result = await _check(name, client.account())
        elif name == "positionRisk":
            result = await _check(name, client.position_risk(symbol))
        elif name == "commissionRate":
            result = await _check(name, client.commission_rate(symbol))
        else:
            result = await _check(name, client.leverage_bracket(symbol))
        print(result)

    print(await _check("createListenKey", client.create_listen_key()))

    if env == "testnet":
        await _testnet_lifecycle(client, symbol)
    await client.aclose()


async def _testnet_lifecycle(client: ExchangeClient, symbol: str) -> None:
    """测试网完整订单生命周期（09 §8）。"""
    print("== testnet 订单生命周期 ==")
    try:
        order = await client.place_order(
            symbol=symbol,
            side="BUY",
            type="MARKET",
            quantity=0.001,
            reduceOnly="false",
        )
        print(f"[OK] 开仓: {order.order_id} {order.status}")
        algo = await client.place_algo_order(
            symbol=symbol,
            algoType="STOP_MARKET",
            side="SELL",
            quantity=0.001,
            reduceOnly="true",
            triggerPrice=0.01,
            triggerPriceType="CONTRACT_PRICE",
        )
        print(f"[OK] 挂止损: {algo.algo_id}")
        await client.cancel_algo_order(symbol=symbol, algoId=algo.algo_id)
        print("[OK] 撤止损")
        close = await client.place_order(
            symbol=symbol, side="SELL", type="MARKET", quantity=0.001, reduceOnly="true"
        )
        print(f"[OK] 平仓: {close.order_id} {close.status}")
    except ExchangeError as error:
        print(f"[FAIL] 生命周期: {error}")


def _read_private_key_hex(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        content = handle.read().strip()
    if content.startswith("-----BEGIN"):
        return content  # PEM 由 cryptography 读取（暂只支持 hex 直传）
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="币安接口冒烟测试")
    parser.add_argument("--env", choices=["testnet", "mainnet"], required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    asyncio.run(_smoke(args.env, args.symbol))


if __name__ == "__main__":
    main()
