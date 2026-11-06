"""通知通道真实发送冒烟：逐类事件发送 + 突发限流观察。

规格：docs/07-实盘与风控.md §8.2、§8.4、§8.5

用法：`python scripts/notify_smoke.py --config configs/live.yaml [--burst 6]`

密钥只从环境变量读取（02 §7）；输出不含密钥、签名与完整 Webhook 地址。
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from trade.live.notifier.base import BaseNotifier, NotifierError, TokenBucket
from trade.live.notifier.channels import build_channels
from trade.live.notifier.config import load_notify_config

# 每类事件的样例载荷（07 §8.5）；金额为虚构值，仅用于验证渲染与送达
_SAMPLES: dict[str, dict[str, Any]] = {
    "start": {"mode": "paper", "model": "smoke", "exit_policy": "fixed", "equity": 15000.0},
    "open": {
        "symbol": "BTCUSDT",
        "entry": 78000.0,
        "qty": 0.001,
        "notional": 78.0,
        "stop": 77220.0,
    },
    "stop_move": {"symbol": "BTCUSDT", "new_stop": 78400.0, "r_multiple": 0.5},
    "close": {"symbol": "BTCUSDT", "exit_type": "trail", "exit_price": 79000.0, "pnl_pct": 1.3},
    "sizing": {"position_frac": 0.2, "max_positions": 3, "source": "cli"},
    "alert": {"reason": "smoke_test", "detail": "通知冒烟测试，可忽略"},
    "halt": {"reason": "R6_daily_loss_halt", "equity": 14000.0},
    "heartbeat": {"equity": 15000.0, "positions": 0, "latency_p95_ms": 15},
    "daily": {"trades": 3, "win_rate": 0.67, "pnl_usdt": 4.2, "fees_usdt": 0.6},
    "stop": {"reason": "smoke_test_end"},
}


async def _send_all(
    channel: BaseNotifier, events: list[str], *, rate_per_min: int
) -> tuple[int, int]:
    """逐类事件发送，返回 (成功数, 失败数)。"""
    ok = 0
    failed = 0
    bucket = TokenBucket(rate_per_min)
    for event in events:
        if not bucket.try_acquire():
            print(f"[WARN] {event}: 触发令牌桶限流（{rate_per_min} 条/分钟），跳过")
            continue
        started = time.perf_counter()
        try:
            await channel.send(event, _SAMPLES[event])
        except NotifierError as error:
            failed += 1
            print(f"[FAIL] {event:<10} {error}")
            continue
        ok += 1
        print(f"[OK] {event:<10} {(time.perf_counter() - started) * 1000:.0f} ms")
    return ok, failed


async def _burst(channel: BaseNotifier, burst: int) -> int:
    """连续快速发送，观察服务端是否拒绝（07 §8.2 限额实测）。"""
    failed = 0
    started = time.perf_counter()
    for index in range(burst):
        try:
            await channel.send("heartbeat", {"seq": index + 1, "equity": 15000.0})
        except NotifierError as error:
            failed += 1
            print(f"[FAIL] burst#{index + 1} {error}")
    elapsed = time.perf_counter() - started
    print(f"[INFO] 突发 {burst} 条耗时 {elapsed:.2f} 秒，失败 {failed} 条")
    return failed


async def _run(config_path: Path, events: list[str], burst: int) -> int:
    config = load_notify_config(config_path)
    channels, client = build_channels(config)
    channel = channels.get("wxpusher")
    if channel is None:
        print("[FAIL] 配置中没有启用的 wxpusher 通道")
        await client.aclose()
        return 1
    print(f"== 通知冒烟：{len(events)} 类事件 + 突发 {burst} 条 ==")
    rate = next((cfg.rate_per_min for cfg in config.channels if cfg.provider == "wxpusher"), 10)
    ok, failed = await _send_all(channel, events, rate_per_min=rate)
    failed += await _burst(channel, burst)
    await client.aclose()
    print(f"[SUMMARY] 成功 {ok} 类，失败 {failed} 次")
    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="通知通道真实发送冒烟")
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument(
        "--events",
        default="start,open,stop_move,close,sizing,alert,halt,heartbeat,daily,stop",
        help="逗号分隔的事件类型",
    )
    parser.add_argument("--burst", type=int, default=6, help="连续快速发送条数（限流实测）")
    args = parser.parse_args()
    events = [item.strip() for item in args.events.split(",") if item.strip()]
    unknown = [event for event in events if event not in _SAMPLES]
    if unknown:
        parser.error(f"未知事件类型：{unknown}")
    raise SystemExit(asyncio.run(_run(Path(args.config), events, args.burst)))


if __name__ == "__main__":
    main()
