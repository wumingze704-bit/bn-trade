"""通知分发：路由、限流合并、重试、通道降级。

规格：docs/07-实盘与风控.md §8.4、§8.5
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence

import httpx

from trade.live.notifier.base import BaseNotifier, EventPayload, NotifierError, TokenBucket
from trade.live.notifier.channels import build_channels
from trade.live.notifier.config import ChannelConfig, NotifyConfig

_RETRY_DELAYS = (1.0, 3.0, 10.0)
_CHANNEL_DOWN_THRESHOLD = 3


class NotifierDispatcher:
    """把事件按配置路由到各通道，负责限流、合并、重试与通道降级（07 §8.4）。"""

    def __init__(
        self,
        config: NotifyConfig,
        *,
        channels: Mapping[str, BaseNotifier] | None = None,
        client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
        now_fn: Callable[[], float] = time.monotonic,
        alert_dedup_sec: float = 300.0,
        retry_delays: Sequence[float] = _RETRY_DELAYS,
    ) -> None:
        self._config = config
        if channels is None:
            channels, client = build_channels(config, client=client)
        self._channels = channels
        self._client = client
        self._logger = logger or logging.getLogger("trade.live.notifier.dispatcher")
        self._now_fn = now_fn
        self._alert_dedup_sec = alert_dedup_sec
        self._retry_delays = tuple(retry_delays)
        self._buckets = {
            name: TokenBucket(cfg.rate_per_min, now_fn=now_fn)
            for name, cfg in self._channel_configs().items()
        }
        self._failures: dict[str, int] = {}
        self._down: set[str] = set()
        self._pending: dict[str, list[str]] = {}
        self._alert_buffer: dict[str, tuple[float, list[EventPayload]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def _channel_configs(self) -> dict[str, ChannelConfig]:
        return {cfg.provider: cfg for cfg in self._config.channels if cfg.enabled}

    def _eligible(self, event_type: str) -> list[str]:
        return [
            name
            for name, cfg in self._channel_configs().items()
            if event_type in cfg.events or "*" in cfg.events
        ]

    async def send(self, event_type: str, payload: EventPayload) -> None:
        """发送事件；任何异常都被吞掉，绝不影响交易（07 §8.4）。"""
        try:
            if event_type == "alert":
                self._buffer_alert(payload)
                return
            for name in self._eligible(event_type):
                text = self._channels[name].format(event_type, payload)
                await self._dispatch_text(name, text)
        except Exception:
            self._logger.exception("通知失败（不影响交易）")

    async def aclose(self) -> None:
        """关闭客户端并取消未完成的合并刷新任务。"""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._client is not None:
            await self._client.aclose()

    async def _dispatch_text(self, name: str, text: str) -> None:
        """限流 + 合并 + 重试 + 通道降级。"""
        if not self._buckets[name].try_acquire():
            self._pending.setdefault(name, []).append(text)
            self._logger.warning("通道 %s 触发限流，同类消息合并", name)
            return
        pending = self._pending.pop(name, [])
        combined = "\n\n".join([*pending, text])
        try:
            await self._retry_send(self._channels[name], combined)
            self._failures[name] = 0
            self._down.discard(name)
        except NotifierError:
            self._failures[name] = self._failures.get(name, 0) + 1
            self._logger.warning("通道 %s 发送失败", name)
            if self._failures[name] >= _CHANNEL_DOWN_THRESHOLD and name not in self._down:
                self._down.add(name)
                await self._notify_channel_down(name)

    async def _retry_send(self, channel: BaseNotifier, text: str) -> None:
        last_error: NotifierError | None = None
        for attempt in range(len(self._retry_delays) + 1):
            if attempt > 0:
                await asyncio.sleep(self._retry_delays[attempt - 1])
            try:
                await channel.send_text(text)
                return
            except NotifierError as error:
                last_error = error
        raise NotifierError(f"发送失败，已重试 {len(self._retry_delays)} 次：{last_error}")

    async def _notify_channel_down(self, name: str) -> None:
        """通道连续失败达阈值后，向其他通道发「通道异常」告警（07 §8.4）。"""
        self._logger.error("通道 %s 连续失败，标记为不可用", name)
        for other in self._eligible("channel_down"):
            if other == name:
                continue
            text = self._channels[other].format("channel_down", {"channel": name})
            try:
                await self._retry_send(self._channels[other], text)
            except NotifierError:
                self._logger.warning("向通道 %s 发送 channel_down 失败", other)

    def _buffer_alert(self, payload: EventPayload) -> None:
        """同类告警 5 分钟内合并发送（07 §8.5）。"""
        key = str(payload.get("reason") or payload.get("event") or payload.get("type") or "alert")
        now = self._now_fn()
        if key in self._alert_buffer:
            first_ts, _ = self._alert_buffer[key]
            if now - first_ts < self._alert_dedup_sec:
                self._alert_buffer[key][1].append(payload)
                return
            self._alert_buffer.pop(key)
        self._alert_buffer[key] = (now, [payload])
        self._schedule_alert_flush(key)

    def _schedule_alert_flush(self, key: str) -> None:
        task = asyncio.create_task(self._flush_alert(key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _flush_alert(self, key: str) -> None:
        await asyncio.sleep(self._alert_dedup_sec)
        entry = self._alert_buffer.pop(key, None)
        if entry is None:
            return
        _, payloads = entry
        if not payloads:
            return
        try:
            for name in self._eligible("alert"):
                channel = self._channels[name]
                text = "\n\n".join(channel.format("alert", p) for p in payloads)
                await self._dispatch_text(name, text)
        except Exception:
            self._logger.exception("告警合并刷新失败")
