"""结构化 JSON 日志与敏感字段脱敏。

规格：docs/02-系统架构与部署.md §8；docs/08-开发计划与验收.md §2 P0
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TextIO

_SENSITIVE_PARTS = (
    "apikey",
    "secret",
    "signature",
    "webhook",
    "privatekey",
    "pin",
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|signature|webhook(?:[_-]?url)?|"
    r"private[_-]?key|dashboard[_-]?control[_-]?pin|pin)\b\s*([=:])\s*([^\s,;]+)"
)
_STANDARD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def _is_sensitive(key: str) -> bool:
    normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in normalized_key for part in _SENSITIVE_PARTS)


def _sanitize(value: object, key: str = "") -> object:
    if _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
        )
    return value


class JsonFormatter(logging.Formatter):
    """輸出 UTC 毫秒時間戳並對結構化欄位做遞迴脫敏。"""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        message = _sanitize(record.getMessage())
        fields = {
            key: _sanitize(value, key)
            for key, value in record.__dict__.items()
            if key not in _STANDARD_FIELDS and not key.startswith("_")
        }
        event = fields.pop("event", message)
        payload: dict[str, object] = {
            "ts": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "module": record.name,
            "event": event,
            **fields,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """配置根 logger 输出单行 JSON 日志。

    Args:
        level: 根 logger 最低记录级别。
        stream: 可选输出流；省略时使用标准错误流。

    Returns:
        无。
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """返回指定名称的标准库 logger。

    Args:
        name: logger 名称，通常使用模块名。

    Returns:
        标准库 `logging.Logger` 实例。
    """
    return logging.getLogger(name)
