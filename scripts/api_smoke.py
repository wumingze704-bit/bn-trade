"""币安接口冒烟测试。

规格：docs/09-币安数据与API.md §8

- `mainnet`：只调用公开接口与只读签名接口，不下单。
- `testnet`：额外执行完整订单生命周期（开仓 IOC → 挂止损 → 挂止盈 → 撤销 → 平仓）。
- `--ws-check`：只做行情订阅冒烟（不需密钥）：组合流订阅计数、断线重连、缺口回补。

用法：`python scripts/api_smoke.py --env {testnet|mainnet}`
      `python scripts/api_smoke.py --env testnet --ws-check --ws-seconds 150`
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from typing import Any

from trade.live.exchange.client import ExchangeClient, ExchangeError
from trade.live.exchange.endpoints import EnvName, endpoints_for
from trade.live.exchange.signing import private_key_kind
from trade.live.market_data import (
    KlineGapFiller,
    MarketDataFeed,
    agg_trade_streams,
    book_ticker_streams,
    kline_streams,
)
from trade.live.orders import build_order_id, compute_limit_price, floor_to_tick, round_step_qty

_PUBLIC_CHECKS = ("ping", "time", "exchangeInfo", "klines", "aggTrades", "bookTicker")
_SIGNED_READ_CHECKS = ("account", "positionRisk", "commissionRate", "leverageBracket")
# 只跑公开检查时的占位值：公开接口不需要签名，仅用于满足客户端构造校验（02 §7）
_PUBLIC_ONLY_SECRET = "public-only"


async def _check(name: str, coro: object) -> str:
    try:
        await coro  # type: ignore[misc]
        return f"[OK] {name}"
    except ExchangeError as error:
        return f"[FAIL] {name}: {error}"
    except Exception as error:  # noqa: BLE001 - 冒烟需捕获网络异常
        return f"[FAIL] {name}: {type(error).__name__} {error}"


async def _smoke(env: EnvName, symbol: str) -> int:
    """按 09 §8 执行冒烟检查，返回失败项数量。

    Args:
        env: 环境（`testnet` / `mainnet`）。
        symbol: 冒烟使用的交易对。

    Returns:
        失败检查项数量；0 表示全部通过（缺密钥导致的 SKIP 不计为失败）。
    """
    key_name = "BINANCE_TESTNET_API_KEY" if env == "testnet" else "BINANCE_API_KEY"
    secret_name = "BINANCE_TESTNET_API_SECRET" if env == "testnet" else "BINANCE_API_SECRET"
    api_key = os.environ.get(key_name, "")
    api_secret = os.environ.get(secret_name, "")
    if api_key.startswith("-----BEGIN"):
        print(f"[FAIL] {key_name} 是 PEM 公钥；这里必须填币安生成的 API Key 字符串（09 §5.1）")
        return 1
    if api_secret.startswith("-----BEGIN"):
        print(f"[FAIL] {secret_name} 是 PEM；私钥要存成文件，用 BINANCE_PRIVATE_KEY_PATH 指向")
        return 1
    private_key_path = os.environ.get("BINANCE_PRIVATE_KEY_PATH")
    key_material = _read_private_key(private_key_path) if private_key_path else None
    kind = private_key_kind(key_material) if key_material else None
    client = ExchangeClient(
        endpoints=endpoints_for(env),
        api_key=api_key,
        api_secret=None if key_material else (api_secret or _PUBLIC_ONLY_SECRET),
        private_key_hex=key_material if kind == "ed25519" else None,
        rsa_private_key_pem=key_material if kind == "rsa" else None,
    )
    print(f"== 环境 {env} ==")
    failures = 0
    for name in _PUBLIC_CHECKS:
        if name == "klines":
            result = await _check(name, client.klines(symbol, "1m", limit=5))
        elif name == "aggTrades":
            result = await _check(name, client.agg_trades(symbol, limit=5))
        elif name == "bookTicker":
            result = await _check(name, client.book_ticker(symbol))
        elif name == "time":
            result = await _check(name, client.server_time())
        else:
            result = await _check(name, client.ping())
        print(result)
        failures += result.startswith("[FAIL]")
    if not api_key:
        print(f"[SKIP] 签名接口与下单生命周期：未设置环境变量 {key_name}（02 §7）")
        await client.aclose()
        return failures

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
        failures += result.startswith("[FAIL]")
    signed_failures = failures

    print(await _check("createListenKey", client.create_listen_key()))

    if env == "testnet":
        if signed_failures:
            # 签名不可用时不做下单演练，避免出现只有开仓没有后续处理的状态（红线 7）
            print("[SKIP] 订单生命周期：签名接口不可用（见上方 FAIL），不下单")
        else:
            failures += await _testnet_lifecycle(client, symbol)
    await client.aclose()
    return failures


async def _testnet_lifecycle(client: ExchangeClient, symbol: str) -> int:
    """测试网订单生命周期：IOC 开仓 → 止损 → 止盈 → 撤止盈 → 平仓 → 确认仓位为 0（09 §8）。

    红线 7：成交后立即挂止损；任何一步异常都在 finally 里市价平仓并核对仓位为 0。

    Returns:
        失败步骤数（0 表示全部成功）。
    """
    print("== testnet 订单生命周期 ==")
    failures = 0
    opened_qty = 0.0
    algo_id: int | None = None
    tick_size, step_size = await _filters(client, symbol)
    quantity = round_step_qty(0.001, step_size)
    minute = time.strftime("%Y%m%d%H%M", time.gmtime())
    try:
        book = await client.book_ticker(symbol)
        ask = book[0].ask_price
        limit = compute_limit_price(
            best_ask=ask, tick_rel=0.0002, c_t=ask, max_chase=0.003, tick_size=tick_size
        )
        order = await client.place_order(
            symbol=symbol,
            side="BUY",
            type="LIMIT",
            timeInForce="IOC",
            quantity=quantity,
            price=limit,
            newClientOrderId=build_order_id("BTCUSDT", minute, "E", 0),
            newOrderRespType="RESULT",
        )
        opened_qty = order.executed_qty
        print(f"[OK] 开仓 IOC {order.client_order_id} status={order.status} filled={opened_qty}")
        if opened_qty <= 0:
            print("[SKIP] IOC 未成交，无仓位，无需挂止损")
            return failures
        entry = order.avg_price or ask
        # 止损先于止盈，且盘中不改单：撤止损放在平仓之后（红线 7）
        algo = await client.place_algo_order(
            symbol=symbol,
            algoType="CONDITIONAL",
            type="STOP_MARKET",
            side="SELL",
            quantity=opened_qty,
            reduceOnly="true",
            triggerPrice=floor_to_tick(entry * 0.99, tick_size),
            triggerPriceType="CONTRACT_PRICE",
            workingType="CONTRACT_PRICE",
            newClientAlgoId=build_order_id("BTCUSDT", minute, "S1", 0),
        )
        algo_id = algo.algo_id
        print(f"[OK] 挂止损 algoId={algo.algo_id} status={algo.status}")
        tp_price = floor_to_tick(entry * 1.02, tick_size)
        tp = await client.place_order(
            symbol=symbol,
            side="SELL",
            type="LIMIT",
            timeInForce="GTC",
            quantity=opened_qty,
            price=tp_price,
            reduceOnly="true",
            newClientOrderId=build_order_id("BTCUSDT", minute, "TP", 0),
        )
        print(f"[OK] 挂止盈 {tp.client_order_id} status={tp.status} price={tp_price}")
        await client.cancel_order(symbol, tp.client_order_id)
        print("[OK] 撤止盈")
    except ExchangeError as error:
        print(f"[FAIL] 生命周期: {error}")
        failures += 1
    finally:
        failures += await _flatten(client, symbol)
        if algo_id is not None:
            try:
                await client.cancel_algo_order(symbol=symbol, algoId=algo_id)
                print("[OK] 撤止损")
            except ExchangeError as error:
                print(f"[WARN] 撤止损失败（reduceOnly 无仓后失效）: {error}")
    return failures


async def _filters(client: ExchangeClient, symbol: str) -> tuple[float, float]:
    """从 exchangeInfo 取 (tickSize, stepSize)（09 §3.1）。"""
    info = await client.exchange_info()
    for item in info.symbols:
        if item.get("symbol") != symbol:
            continue
        tick_size = 0.0
        step_size = 0.0
        for rule in item.get("filters", []):
            if not isinstance(rule, dict):
                continue
            if rule.get("filterType") == "PRICE_FILTER":
                tick_size = float(rule.get("tickSize", 0.0))
            elif rule.get("filterType") == "LOT_SIZE":
                step_size = float(rule.get("stepSize", 0.0))
        return tick_size, step_size
    raise ExchangeError(code=None, msg=f"exchangeInfo 中找不到 {symbol}", http_status=200)


async def _flatten(client: ExchangeClient, symbol: str) -> int:
    """市价 reduceOnly 平掉该币仓位并核对仓位为 0（红线 7）。

    Returns:
        失败步骤数（0 表示仓位已清零）。
    """
    failures = 0
    try:
        positions = await client.position_risk(symbol)
    except ExchangeError as error:
        print(f"[WARN] 无法查询仓位（{error}），跳过平仓与确认")
        return failures
    qty = sum(float(item.get("positionAmt", 0.0)) for item in positions)
    if abs(qty) < 1e-9:
        print("[OK] 仓位已为 0")
        return failures
    try:
        await client.place_order(
            symbol=symbol, side="SELL", type="MARKET", quantity=abs(qty), reduceOnly="true"
        )
        print(f"[OK] 市价平仓 {symbol} {abs(qty)}")
    except ExchangeError as error:
        print(f"[FAIL] 平仓失败: {error}")
        failures += 1
    positions = await client.position_risk(symbol)
    remaining = sum(abs(float(item.get("positionAmt", 0.0))) for item in positions)
    if remaining < 1e-9:
        print("[OK] 确认仓位为 0")
    else:
        print(f"[FAIL] 仍有持仓 {symbol} {remaining}")
        failures += 1
    return failures


def _read_private_key(path: str) -> str:
    """读取私钥文件内容（Ed25519 十六进制或 RSA PEM），09 §5.2。"""
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


async def _ws_check(env: EnvName, symbol: str, seconds: int) -> int:
    """行情订阅冒烟：组合流计数、断线重连、缺口回补（07 §3.1、§3.2；09 §8）。

    不读取任何密钥：只订阅公开行情流与调用公开 K 线接口。

    Args:
        env: 环境（`paper` 模式用 `testnet`）。
        symbol: 订阅与回补的交易对。
        seconds: 观察时长（秒）。

    Returns:
        进程退出码：三种流都收到消息且无未回补缺口时为 0。
    """
    endpoints = endpoints_for(env)
    streams = kline_streams([symbol]) + agg_trade_streams([symbol]) + book_ticker_streams([symbol])
    closed_open_times: list[int] = []
    gap_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
    filled: list[int] = []
    client = ExchangeClient(endpoints=endpoints, api_key="", api_secret="public-only")
    filler = KlineGapFiller(client)

    def on_message(payload: dict[str, Any]) -> None:
        if payload.get("e") != "kline":
            return
        kline = payload.get("k")
        if isinstance(kline, dict) and kline.get("x") is True:
            closed_open_times.append(int(kline.get("t", 0)))

    def on_reconnect(last_event_ms: int, now: int) -> None:
        last_open = closed_open_times[-1] if closed_open_times else last_event_ms - 60_000
        print(f"[INFO] 断线重连成功，开始回补 {last_open} 之后的 K 线")
        gap_queue.put_nowait((last_open, now))

    async def backfiller() -> None:
        while True:
            last_open, now = await gap_queue.get()
            bars = await filler.fill(symbol, last_open_time_ms=last_open, now_ms=now)
            filled.extend(bar["open_time_ms"] for bar in bars)
            print(f"[OK] 缺口回补 {len(bars)} 根 K 线")

    feed = MarketDataFeed(
        endpoints=endpoints,
        streams=streams,
        on_message=on_message,
        on_reconnect=on_reconnect,
    )
    print(f"== WS 订阅冒烟 env={env} symbol={symbol} {seconds}s ==")
    for url in feed.urls:
        print(f"[INFO] {url}")
    worker = asyncio.create_task(backfiller())
    try:
        stats = await feed.run(max_runtime_ms=seconds * 1000)
    finally:
        worker.cancel()
        await client.aclose()

    print(f"[OK] 消息 {stats.messages} 条：{dict(stats.by_event)}")
    if closed_open_times:
        print(
            f"[OK] 已收盘 1m K 线 {len(closed_open_times)} 根，"
            f"open_time {closed_open_times[0]} → {closed_open_times[-1]}"
        )
    print(f"[OK] 断线重连 {stats.reconnects} 次，缺口回补 {len(filled)} 根")
    missing = {"kline", "aggTrade", "bookTicker"} - set(stats.by_event)
    if missing:
        print(f"[FAIL] 未收到消息的流：{sorted(missing)}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="币安接口冒烟测试")
    parser.add_argument("--env", choices=["testnet", "mainnet"], required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--ws-check", action="store_true", help="只做行情订阅冒烟（不需密钥）")
    parser.add_argument("--ws-seconds", type=int, default=150, help="行情观察时长（秒）")
    args = parser.parse_args()
    if args.ws_check:
        raise SystemExit(asyncio.run(_ws_check(args.env, args.symbol, args.ws_seconds)))
    raise SystemExit(asyncio.run(_smoke(args.env, args.symbol)))


if __name__ == "__main__":
    main()
