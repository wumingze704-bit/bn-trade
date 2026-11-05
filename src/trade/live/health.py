"""健康监控：行情延迟（R10）、下单错误（R11）、时钟漂移（R12）、限频用量。

规格：docs/07-实盘与风控.md §7.1；docs/09-币安数据与API.md §6
"""

from __future__ import annotations

from collections import deque


class LatencyMonitor:
    """行情延迟监控（07 §3.2、§7.1 R10）。

    每秒采样一次「本机接收时间 - 事件时间」；连续 `sustained_sec` 秒超过
    `lag_sec` 即触发 R10（只平不开）。
    """

    def __init__(self, lag_sec: float, sustained_sec: int) -> None:
        self._lag_sec = lag_sec
        self._sustained = sustained_sec
        self._window: deque[float] = deque(maxlen=sustained_sec)

    def sample(self, lag_sec: float) -> bool:
        """记录一次延迟采样并返回是否触发 R10（07 §7.1）。"""
        self._window.append(lag_sec)
        if len(self._window) < self._sustained:
            return False
        return all(x > self._lag_sec for x in self._window)

    def reset(self) -> None:
        self._window.clear()


class OrderErrorMonitor:
    """下单错误计数（07 §7.1 R11）。

    5 分钟内 ≥ `limit` 次下单错误即触发只平不开 30 分钟。
    """

    def __init__(self, limit: int, window_sec: int = 300) -> None:
        self._limit = limit
        self._window_sec = window_sec
        self._timestamps: deque[int] = deque()

    def record_error(self, ts_ms: int) -> bool:
        """记录一次错误并返回是否触发 R11。"""
        self._timestamps.append(ts_ms)
        cutoff = ts_ms - self._window_sec * 1000
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        return len(self._timestamps) >= self._limit

    def count(self) -> int:
        return len(self._timestamps)


class WeightUsage:
    """限频用量监控（09 §6）。

    读取响应头 `X-MBX-USED-WEIGHT-1M`；超过 `open_limit`（默认 70%）停止新开仓，
    超过 `pause_limit`（默认 90%）暂停所有非必要请求。
    """

    def __init__(self, open_limit: float = 0.7, pause_limit: float = 0.9) -> None:
        self._open_limit = open_limit
        self._pause_limit = pause_limit
        self._used_weight = 0.0

    def update(self, used_weight: float) -> None:
        self._used_weight = used_weight

    @property
    def should_stop_opening(self) -> bool:
        return self._used_weight >= self._open_limit

    @property
    def should_pause(self) -> bool:
        return self._used_weight >= self._pause_limit
