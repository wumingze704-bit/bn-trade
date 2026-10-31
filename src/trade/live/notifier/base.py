"""通知基础设施：协议、限流、消息格式化与截断。

规格：docs/07-实盘与风控.md §8.4、§8.5
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Protocol

EventPayload = Mapping[str, object]

EVENT_LABELS: dict[str, str] = {
    "start": "启动",
    "stop": "停止",
    "open": "开仓",
    "stop_move": "止损上移",
    "close": "平仓",
    "sizing": "仓位设置变更",
    "alert": "告警",
    "halt": "停机",
    "heartbeat": "心跳",
    "daily": "日报",
    "channel_down": "通道异常",
}

_AMOUNT_KEY_PARTS = (
    "amount",
    "usdt",
    "pnl",
    "price",
    "notional",
    "margin",
    "fee",
    "equity",
    "balance",
    "qty",
    "cost",
    "value",
    "slippage",
)


class NotifierError(Exception):
    """通知发送失败（网络错误或返回码非成功）。"""


class Notifier(Protocol):
    """通知器协议（07 §8.4）。"""

    async def send(self, event_type: str, payload: EventPayload) -> None:
        """发送一条通知；失败时抛 `NotifierError`，不得影响交易主流程。"""
        ...


class BaseNotifier(ABC):
    """HTTP 通知器基类：`send` 组合 `format` 与 `send_text`。"""

    @abstractmethod
    def format(self, event_type: str, payload: EventPayload) -> str:
        """把事件渲染成消息正文（纯函数，不含网络请求）。"""

    @abstractmethod
    async def send_text(self, text: str) -> None:
        """把已渲染正文发送出去；失败抛 `NotifierError`。"""

    async def send(self, event_type: str, payload: EventPayload) -> None:
        await self.send_text(self.format(event_type, payload))


class TokenBucket:
    """令牌桶限流（07 §8.2/§8.3）。

    容量与补充速率都取 `rate_per_min`：每分钟最多放行 `rate_per_min` 条，
    秒级匀速补充。
    """

    def __init__(self, rate_per_min: int, *, now_fn: Callable[[], float] = time.monotonic) -> None:
        self._rate = float(rate_per_min)
        self._tokens = self._rate
        self._updated = now_fn()
        self._now_fn = now_fn

    def try_acquire(self) -> bool:
        """尝试获取一个令牌；返回 False 表示已触发限流。"""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def _refill(self) -> None:
        now = self._now_fn()
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._rate, self._tokens + elapsed * (self._rate / 60.0))
        self._updated = now


def format_event(event_type: str, payload: EventPayload, *, redact_amounts: bool = False) -> str:
    """把事件渲染成 markdown 消息正文（07 §8.5）。

    Args:
        event_type: 事件类型（见 `EVENT_LABELS`）。
        payload: 结构化字段。
        redact_amounts: 是否脱敏金额类字段（07 §8.2 `redact_amounts`）。
    """
    label = EVENT_LABELS.get(event_type, event_type)
    lines = [f"**{label}**"]
    for key in sorted(payload):
        value = payload[key]
        if redact_amounts and _is_amount_field(key):
            lines.append(f"- {key}: [已脱敏]")
        else:
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def truncate_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断，保证结果不超过 `max_bytes`（07 §8.3）。

    截断边界不回退半个多字节字符；截断后追加 `…(截断)` 标记。
    """
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    marker = "…(截断)".encode()
    limit = max_bytes - len(marker)
    if limit <= 0:
        return "…(截断)"
    return data[:limit].decode("utf-8", errors="ignore") + "…(截断)"


def _is_amount_field(key: str) -> bool:
    """判断字段名是否为金额类字段（`redact_amounts` 脱敏依据）。"""
    normalized = key.lower()
    return any(part in normalized for part in _AMOUNT_KEY_PARTS)
