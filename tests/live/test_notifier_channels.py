"""通知通道实现测试。

规格：docs/07-实盘与风控.md §8.2、§8.3、§8.4
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from trade.live.notifier.base import NotifierError
from trade.live.notifier.channels import (
    NullNotifier,
    WeComNotifier,
    WxPusherNotifier,
    build_channels,
)
from trade.live.notifier.config import ChannelConfig, NotifyConfig


def _run(coro):  # noqa: ANN202
    return asyncio.run(coro)


def test_wxpusher_send_builds_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 1000, "msg": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = WxPusherNotifier("tok", ["uid1"], client)
    _run(notifier.send("open", {"symbol": "SOLUSDT", "price": 100.0}))

    assert captured["body"]["appToken"] == "tok"
    assert captured["body"]["uids"] == ["uid1"]
    assert "**开仓**" in captured["body"]["content"]
    assert captured["body"]["summary"]


def test_wxpusher_send_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1001, "msg": "bad"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = WxPusherNotifier("tok", ["uid1"], client)
    with pytest.raises(NotifierError):
        _run(notifier.send_text("hi"))


def test_wxpusher_redact_amounts() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    notifier = WxPusherNotifier("tok", ["uid1"], client, redact_amounts=True)
    text = notifier.format("close", {"symbol": "SOLUSDT", "net_pnl_usdt": 1.23})
    assert "net_pnl_usdt: [已脱敏]" in text


def test_wecom_truncate_to_max_bytes() -> None:
    client = httpx.AsyncClient()
    notifier = WeComNotifier("key", client, max_bytes=100)
    text = notifier.format("alert", {"detail": "x" * 5000})
    assert len(text.encode("utf-8")) <= 100


def test_null_notifier_noop() -> None:
    notifier = NullNotifier()
    assert notifier.format("open", {"a": 1}) == ""
    _run(notifier.send("open", {"a": 1}))  # 不抛异常


def test_build_channels_missing_secret_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WXPUSHER_APP_TOKEN", raising=False)
    monkeypatch.delenv("WXPUSHER_UID", raising=False)
    config = NotifyConfig(channels=[ChannelConfig(provider="wxpusher", events=["open"])])
    channels, _ = build_channels(config)
    assert isinstance(channels["wxpusher"], NullNotifier)


def test_build_channels_skips_disabled() -> None:
    config = NotifyConfig(channels=[ChannelConfig(provider="feishu", enabled=False, events=[])])
    channels, _ = build_channels(config)
    assert "feishu" not in channels
