"""停机脚本：halt_flag + 撤 trd- 挂单 + MARKET reduceOnly 平仓 + REST 确认。

规格：docs/07-实盘与风控.md §7.3

不依赖主进程；可独立运行。用法：`python scripts/kill_switch.py --config configs/live.yaml`
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from trade.live.config import load_live_config
from trade.live.exchange.client import ExchangeClient
from trade.live.exchange.endpoints import endpoints_for
from trade.live.exchange.signing import private_key_kind
from trade.live.store import Store


async def _kill(config_path: Path) -> None:
    config = load_live_config(config_path)
    env = "testnet" if config.mode == "paper" else "mainnet"
    api_key = os.environ.get(
        "BINANCE_TESTNET_API_KEY" if config.mode == "paper" else "BINANCE_API_KEY", ""
    )
    if not api_key:
        print("[FAIL] 缺少 API Key 环境变量")
        sys.exit(1)
    if api_key.startswith("-----BEGIN"):
        print("[FAIL] API Key 是 PEM 公钥；这里必须填币安生成的 API Key 字符串（09 §5.1）")
        sys.exit(1)
    key_material = _private_key()
    kind = private_key_kind(key_material) if key_material else None
    client = ExchangeClient(
        endpoints=endpoints_for(env),
        api_key=api_key,
        api_secret=None
        if key_material
        else os.environ.get(
            "BINANCE_TESTNET_API_SECRET" if config.mode == "paper" else "BINANCE_API_SECRET"
        ),
        private_key_hex=key_material if kind == "ed25519" else None,
        rsa_private_key_pem=key_material if kind == "rsa" else None,
    )
    store = Store(config.storage.db_path)
    date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    # 1. 写 halt_flag
    store.set_halt_flag(1, date=date)
    store.commit()
    print("[OK] halt_flag 已置位")

    # 2. 撤 trd- 前缀挂单
    open_orders = await client.open_orders()
    for order in open_orders:
        client_id = str(order.get("clientOrderId", ""))
        symbol = str(order.get("symbol", ""))
        if client_id.startswith("trd-"):
            try:
                await client.cancel_order(symbol, client_id)
                print(f"[OK] 撤单 {client_id}")
            except Exception as error:  # noqa: BLE001
                print(f"[WARN] 撤单失败 {client_id}: {error}")

    # 3. MARKET reduceOnly 平仓
    positions = await client.position_risk()
    for pos in positions:
        qty = float(pos.get("positionAmt", 0.0))
        symbol = str(pos.get("symbol", ""))
        if abs(qty) > 0:
            try:
                await client.place_order(
                    symbol=symbol,
                    side="SELL",
                    type="MARKET",
                    quantity=abs(qty),
                    reduceOnly="true",
                )
                print(f"[OK] 平仓 {symbol} {abs(qty)}")
            except Exception as error:  # noqa: BLE001
                print(f"[FAIL] 平仓失败 {symbol}: {error}")

    # 4. REST 确认仓位为 0
    positions = await client.position_risk()
    remaining = [p for p in positions if abs(float(p.get("positionAmt", 0.0))) > 1e-9]
    if remaining:
        print(f"[WARN] 仍有持仓：{[p.get('symbol') for p in remaining]}")
    else:
        print("[OK] 仓位已清零")
    store.close()
    await client.aclose()


def _private_key() -> str | None:
    """读取 `BINANCE_PRIVATE_KEY_PATH` 指向的私钥（Ed25519 十六进制或 RSA PEM）。"""
    path = os.environ.get("BINANCE_PRIVATE_KEY_PATH")
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="停机开关")
    parser.add_argument("--config", default="configs/live.yaml")
    args = parser.parse_args()
    asyncio.run(_kill(Path(args.config)))


if __name__ == "__main__":
    main()
