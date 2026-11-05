"""实盘引擎入口。

规格：docs/07-实盘与风控.md §1

用法：`python scripts/run_live.py --mode {paper|shadow|live} --config configs/live.yaml`
`live` 模式需追加 `--i-understand-the-risk`。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from trade.common.logging import configure_logging
from trade.live.config import load_live_config
from trade.live.engine import LiveEngine
from trade.live.exchange.client import ExchangeClient
from trade.live.exchange.endpoints import endpoints_for
from trade.live.store import Store


def _api_key(mode: str) -> str:
    if mode == "paper":
        return os.environ.get("BINANCE_TESTNET_API_KEY", "")
    return os.environ.get("BINANCE_API_KEY", "")


def _api_secret(mode: str) -> str | None:
    if mode == "paper":
        return os.environ.get("BINANCE_TESTNET_API_SECRET")
    return os.environ.get("BINANCE_API_SECRET")


def _private_key_hex() -> str | None:
    path = os.environ.get("BINANCE_PRIVATE_KEY_PATH")
    if not path:
        return None
    content = Path(path).read_text(encoding="utf-8").strip()
    if content.startswith("-----BEGIN"):
        return content
    return content


async def _main(config_path: Path) -> None:
    config = load_live_config(config_path)
    if config.mode == "live":
        # 命令行已校验 --i-understand-the-risk（见 main）
        pass
    env = "testnet" if config.mode == "paper" else "mainnet"
    api_key = _api_key(config.mode)
    if not api_key:
        env_var = "BINANCE_TESTNET_API_KEY" if config.mode == "paper" else "BINANCE_API_KEY"
        print(f"[FAIL] 缺少环境变量 {env_var}")
        sys.exit(1)
    client = ExchangeClient(
        endpoints=endpoints_for(env),
        api_key=api_key,
        api_secret=_api_secret(config.mode),
        private_key_hex=_private_key_hex(),
    )
    store = Store(config.storage.db_path)
    engine = LiveEngine(config=config, base_dir=config_path.parent, client=client, store=store)
    try:
        await engine.run()
    finally:
        store.close()
        await client.aclose()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="实盘引擎")
    parser.add_argument("--mode", choices=["paper", "shadow", "live"], required=True)
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--i-understand-the-risk", action="store_true")
    args = parser.parse_args()
    if args.mode == "live" and not args.i_understand_the_risk:
        print("live 模式需追加 --i-understand-the-risk 确认（07 §1）")
        sys.exit(1)
    asyncio.run(_main(Path(args.config)))


if __name__ == "__main__":
    main()
