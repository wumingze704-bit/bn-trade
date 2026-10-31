"""通知分发测试。

规格：docs/07-实盘与风控.md §8.4、§8.5
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from trade.live.notifier.base import BaseNotifier, EventPayload, NotifierError
from trade.live.notifier.channels import WeComNotifier
from trade.live.notifier.config import ChannelConfig, NotifyConfig
from trade.live.notifier.dispatcher import NotifierDispatcher


class _RecordingNotifier(BaseNotifier):
    """记录发送内容、可配置前 N 次发送失败的假通道。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[str] = []
        self.fail_remaining = fail_times

    def format(self, event_type: str, payload: EventPayload) -> str:
        return f"{event_type}:{dict(payload)}"

    async def send_text(self, text: str) -> None:
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise NotifierError("模拟失败")
        self.sent.append(text)


def _run(coro):  # noqa: ANN202
    return asyncio.run(coro)


def test_routes_to_eligible_channels() -> None:
    wxpusher = _RecordingNotifier()
    wecom = _RecordingNotifier()
    config = NotifyConfig(
        channels=[
            ChannelConfig(provider="wxpusher", events=["open"]),
            ChannelConfig(provider="wecom", events=["close"]),
        ]
    )
    dispatcher = NotifierDispatcher(config, channels={"wxpusher": wxpusher, "wecom": wecom})

    _run(dispatcher.send("open", {"n": 1}))
    _run(dispatcher.send("close", {"n": 2}))

    assert len(wxpusher.sent) == 1 and "open" in wxpusher.sent[0]
    assert len(wecom.sent) == 1 and "close" in wecom.sent[0]


def test_retry_then_success() -> None:
    channel = _RecordingNotifier(fail_times=2)
    config = NotifyConfig(channels=[ChannelConfig(provider="wxpusher", events=["open"])])
    dispatcher = NotifierDispatcher(
        config, channels={"wxpusher": channel}, retry_delays=[0.0, 0.0, 0.0]
    )
    _run(dispatcher.send("open", {"n": 1}))
    assert channel.sent == ["open:{'n': 1}"]


def test_rate_limit_merges() -> None:
    clock = [0.0]
    channel = _RecordingNotifier()
    config = NotifyConfig(
        channels=[ChannelConfig(provider="wxpusher", events=["open"], rate_per_min=1)]
    )
    dispatcher = NotifierDispatcher(config, channels={"wxpusher": channel}, now_fn=lambda: clock[0])

    _run(dispatcher.send("open", {"n": 1}))  # 有令牌，直接发
    _run(dispatcher.send("open", {"n": 2}))  # 限流，合并
    assert len(channel.sent) == 1

    clock[0] += 60.0  # 补满令牌
    _run(dispatcher.send("open", {"n": 3}))  # 冲刷合并的 2 + 当前 3
    assert len(channel.sent) == 2
    assert "'n': 2" in channel.sent[1] and "'n': 3" in channel.sent[1]


def test_channel_down_notifies_backup() -> None:
    wxpusher = _RecordingNotifier(fail_times=1000)
    wecom = _RecordingNotifier()
    config = NotifyConfig(
        channels=[
            ChannelConfig(provider="wxpusher", events=["open"]),
            ChannelConfig(provider="wecom", events=["channel_down"]),
        ]
    )
    dispatcher = NotifierDispatcher(
        config, channels={"wxpusher": wxpusher, "wecom": wecom}, retry_delays=[0.0, 0.0, 0.0]
    )

    for _ in range(3):
        _run(dispatcher.send("open", {"n": 1}))

    assert any("channel_down" in text for text in wecom.sent)


def test_send_never_raises() -> None:
    channel = _RecordingNotifier(fail_times=1000)
    config = NotifyConfig(channels=[ChannelConfig(provider="wxpusher", events=["open"])])
    dispatcher = NotifierDispatcher(
        config, channels={"wxpusher": channel}, retry_delays=[0.0, 0.0, 0.0]
    )
    _run(dispatcher.send("open", {"n": 1}))  # 不应抛异常


async def _alert_dedup_main(dispatcher: NotifierDispatcher) -> None:
    await dispatcher.send("alert", {"reason": "X", "n": 1})
    await dispatcher.send("alert", {"reason": "X", "n": 2})
    await dispatcher.send("alert", {"reason": "X", "n": 3})
    await asyncio.sleep(0.15)


def test_alert_dedup_merges() -> None:
    channel = _RecordingNotifier()
    config = NotifyConfig(
        channels=[ChannelConfig(provider="wxpusher", events=["alert"], rate_per_min=1000)]
    )
    dispatcher = NotifierDispatcher(config, channels={"wxpusher": channel}, alert_dedup_sec=0.05)
    _run(_alert_dedup_main(dispatcher))
    assert len(channel.sent) == 1
    assert "'n': 1" in channel.sent[0]
    assert "'n': 2" in channel.sent[0]
    assert "'n': 3" in channel.sent[0]


def test_secret_key_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    wecom = WeComNotifier("SECRETKEY123", client)
    config = NotifyConfig(channels=[ChannelConfig(provider="wecom", events=["open"])])
    dispatcher = NotifierDispatcher(config, channels={"wecom": wecom}, retry_delays=[0.0, 0.0, 0.0])

    with caplog.at_level(logging.WARNING):
        _run(dispatcher.send("open", {"n": 1}))

    assert "SECRETKEY123" not in caplog.text
    assert "发送失败" in caplog.text
