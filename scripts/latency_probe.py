"""测量币安公开 REST 与 WebSocket 的网络延迟。

规格：docs/02-系统架构与部署.md §5.3；docs/08-开发计划与验收.md §2 P0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections.abc import Sequence

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

REST_URL = "https://fapi.binance.com/fapi/v1/time"
WS_URL = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
REST_SAMPLES = 20
WS_DURATION_SEC = 60


def percentile(samples: Sequence[float], percentage: float) -> float:
    """计算 nearest-rank 百分位数。

    Args:
        samples: 毫秒单位的测量值。
        percentage: 百分位比例，范围 `(0, 1]`。

    Returns:
        对应百分位的毫秒值。

    Raises:
        ValueError: 样本为空或百分位超出范围。
    """
    if not samples or not 0 < percentage <= 1:
        raise ValueError("样本不可为空，百分位必须在 (0, 1] 范围内")
    ordered = sorted(samples)
    index = math.ceil(percentage * len(ordered)) - 1
    return ordered[index]


def measure_rest(samples: int = REST_SAMPLES) -> list[float]:
    """测量 USDⓈ-M 公开服务器时间接口往返时间。

    Args:
        samples: 请求次数。

    Returns:
        每次请求往返延迟，单位毫秒。
    """
    latencies: list[float] = []
    with httpx.Client(timeout=10.0) as client:
        for _ in range(samples):
            started = time.perf_counter()
            response = client.get(REST_URL)
            response.raise_for_status()
            response.json()
            latencies.append((time.perf_counter() - started) * 1000)
    return latencies


async def measure_websocket(duration_sec: int = WS_DURATION_SEC) -> list[float]:
    """测量公开 aggTrade 事件时间到本机接收时间的延迟。

    Args:
        duration_sec: WebSocket 测量时长，单位秒。

    Returns:
        每条有效行情的事件延迟，单位毫秒。
    """
    latencies: list[float] = []
    async with connect(WS_URL, open_timeout=10, close_timeout=5) as websocket:
        end_time = time.monotonic() + duration_sec
        while time.monotonic() < end_time:
            remaining = end_time - time.monotonic()
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            except TimeoutError:
                break
            payload = json.loads(message)
            event_time_ms = payload.get("E") if isinstance(payload, dict) else None
            if isinstance(event_time_ms, int):
                received_time_ms = time.time_ns() // 1_000_000
                latencies.append(float(received_time_ms - event_time_ms))
    return latencies


def _print_summary(rest: Sequence[float], websocket: Sequence[float]) -> None:
    print(f"{'来源':<12}{'P50 (ms)':>12}{'P95 (ms)':>12}{'样本数':>12}")
    for label, samples in (("REST", rest), ("WebSocket", websocket)):
        if not samples:
            print(f"{label:<12}{'N/A':>12}{'N/A':>12}{0:>12}")
            continue
        print(
            f"{label:<12}{percentile(samples, 0.50):>12.2f}"
            f"{percentile(samples, 0.95):>12.2f}{len(samples):>12}"
        )


def main() -> int:
    """执行 REST 与 WebSocket 延迟探测。"""
    parser = argparse.ArgumentParser(description="测量币安公开行情 API 延迟")
    parser.add_argument("--rest-samples", type=int, default=REST_SAMPLES)
    parser.add_argument("--ws-duration-sec", type=int, default=WS_DURATION_SEC)
    arguments = parser.parse_args()
    if arguments.rest_samples < 1 or arguments.ws_duration_sec < 1:
        parser.error("测量次数与时长必须为正整数")

    try:
        rest_latencies = measure_rest(arguments.rest_samples)
        websocket_latencies = asyncio.run(measure_websocket(arguments.ws_duration_sec))
    except (httpx.HTTPError, WebSocketException, TimeoutError, ValueError) as error:
        print(f"延迟探测失败：{error}", file=sys.stderr)
        return 1
    _print_summary(rest_latencies, websocket_latencies)
    return 0 if websocket_latencies else 1


if __name__ == "__main__":
    raise SystemExit(main())
