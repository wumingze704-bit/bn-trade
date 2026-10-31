"""通知通道实现：WxPusher / 企业微信 / 飞书 / Null。

规格：docs/07-实盘与风控.md §8.2、§8.3、§8.4
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence

import httpx

from trade.live.notifier.base import (
    BaseNotifier,
    EventPayload,
    NotifierError,
    format_event,
    truncate_bytes,
)
from trade.live.notifier.config import ChannelConfig, NotifyConfig

# 端点以各服务官方文档为准（07 §8.2/§8.3）；P5 实测阶段核对后回填本节。
_WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message"
_WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
_FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook"


class NullNotifier(BaseNotifier):
    """测试/降级用空实现（07 §8.4）。"""

    def format(self, event_type: str, payload: EventPayload) -> str:
        return ""

    async def send_text(self, text: str) -> None:
        return None


class WxPusherNotifier(BaseNotifier):
    """个人微信主通道（07 §8.2）。"""

    def __init__(
        self,
        app_token: str,
        uids: Sequence[str],
        client: httpx.AsyncClient,
        *,
        redact_amounts: bool = False,
    ) -> None:
        self._app_token = app_token
        self._uids = list(uids)
        self._client = client
        self._redact = redact_amounts

    def format(self, event_type: str, payload: EventPayload) -> str:
        return format_event(event_type, payload, redact_amounts=self._redact)

    async def send_text(self, text: str) -> None:
        body = {
            "appToken": self._app_token,
            "content": text,
            "summary": _summary(text),
            "contentType": 3,
            "uids": self._uids,
        }
        response = await _post(self._client, _WXPUSHER_URL, body)
        data = _response_json(response)
        if data.get("code") != 1000:
            raise NotifierError(f"WxPusher 返回错误：{data.get('msg', data)}")


class WeComNotifier(BaseNotifier):
    """企业微信群机器人备份通道（07 §8.3）。"""

    def __init__(
        self, webhook_key: str, client: httpx.AsyncClient, *, max_bytes: int = 4096
    ) -> None:
        self._key = webhook_key
        self._client = client
        self._max_bytes = max_bytes

    def format(self, event_type: str, payload: EventPayload) -> str:
        return truncate_bytes(format_event(event_type, payload), self._max_bytes)

    async def send_text(self, text: str) -> None:
        url = f"{_WECOM_URL}?key={self._key}"
        body = {"msgtype": "markdown", "markdown": {"content": text}}
        response = await _post(self._client, url, body)
        data = _response_json(response)
        if data.get("errcode") != 0:
            raise NotifierError(f"WeCom 返回错误：{data.get('errmsg', data)}")


class FeishuNotifier(BaseNotifier):
    """飞书自定义机器人（可选，默认关闭，07 §8.1）。"""

    def __init__(self, bot_token: str, client: httpx.AsyncClient) -> None:
        self._token = bot_token
        self._client = client

    def format(self, event_type: str, payload: EventPayload) -> str:
        return format_event(event_type, payload)

    async def send_text(self, text: str) -> None:
        url = f"{_FEISHU_URL}/{self._token}"
        body = {"msg_type": "text", "content": {"text": text}}
        response = await _post(self._client, url, body)
        data = _response_json(response)
        if data.get("code") != 0:
            raise NotifierError(f"Feishu 返回错误：{data.get('msg', data)}")


def build_channels(
    config: NotifyConfig, *, client: httpx.AsyncClient | None = None
) -> tuple[dict[str, BaseNotifier], httpx.AsyncClient]:
    """按配置与环境变量构建启用的通道（07 §8.4）。

    密钥从环境变量读取；缺失密钥的通道降级为 `NullNotifier`，
    保证「通知失败不影响交易」。

    Args:
        config: 通知配置。
        client: 共享的 httpx 客户端；省略则新建（由调用方负责关闭）。

    Returns:
        `(provider -> 通知器, 共享客户端)`。
    """
    client = client or httpx.AsyncClient()
    logger = logging.getLogger("trade.live.notifier")
    channels: dict[str, BaseNotifier] = {}
    for cfg in config.channels:
        if not cfg.enabled:
            continue
        channels[cfg.provider] = _build_channel(cfg, client, logger)
    return channels, client


def _build_channel(
    cfg: ChannelConfig, client: httpx.AsyncClient, logger: logging.Logger
) -> BaseNotifier:
    if cfg.provider == "wxpusher":
        token = os.environ.get(cfg.token_env or "WXPUSHER_APP_TOKEN", "")
        uid = os.environ.get(cfg.uid_env or "WXPUSHER_UID", "")
        if not token or not uid:
            logger.warning("WxPusher 缺少 token/uid，通知降级为 no-op")
            return NullNotifier()
        return WxPusherNotifier(token, [uid], client, redact_amounts=cfg.redact_amounts)
    if cfg.provider == "wecom":
        key = os.environ.get(cfg.key_env or "WECOM_WEBHOOK_KEY", "")
        if not key:
            logger.warning("WeCom 缺少 webhook key，通知降级为 no-op")
            return NullNotifier()
        return WeComNotifier(key, client, max_bytes=cfg.max_bytes)
    if cfg.provider == "feishu":
        token = os.environ.get(cfg.token_env or "FEISHU_BOT_TOKEN", "")
        if not token:
            logger.warning("Feishu 缺少 token，通知降级为 no-op")
            return NullNotifier()
        return FeishuNotifier(token, client)
    return NullNotifier()


async def _post(client: httpx.AsyncClient, url: str, body: Mapping[str, object]) -> httpx.Response:
    """发送 POST 并在网络错误/非 200 时抛 NotifierError（不记录密钥与完整 URL）。"""
    try:
        response = await client.post(url, json=body, timeout=10.0)
    except httpx.HTTPError as error:
        raise NotifierError("通知网络错误") from error
    if response.status_code != 200:
        raise NotifierError(f"通知 HTTP {response.status_code}")
    return response


def _response_json(response: httpx.Response) -> dict[str, object]:
    try:
        data = response.json()
    except ValueError as error:
        raise NotifierError("通知响应不是合法 JSON") from error
    return data if isinstance(data, dict) else {}


def _summary(text: str) -> str:
    line = text.splitlines()[0] if text else ""
    return line[:40]
