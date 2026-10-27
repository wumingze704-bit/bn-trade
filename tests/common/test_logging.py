"""日志字段脱敏测试。

规格：docs/08-开发计划与验收.md §2 P0
"""

import json
import logging

from trade.common.logging import JsonFormatter


def test_json_logging_redacts_sensitive_fields() -> None:
    record = logging.LogRecord(
        name="trade.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request api_secret=message-secret",
        args=(),
        exc_info=None,
    )
    record.event = "test_event"
    record.api_secret = "field-secret"
    record.feishu_webhook_url = "https://example.invalid/hook/private"

    output = JsonFormatter().format(record)
    payload = json.loads(output)

    assert payload["event"] == "test_event"
    assert "message-secret" not in output
    assert "field-secret" not in output
    assert "example.invalid" not in output
    assert output.count("[REDACTED]") == 2


def test_json_logging_includes_utc_timestamp_and_context() -> None:
    record = logging.LogRecord(
        name="trade.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="started",
        args=(),
        exc_info=None,
    )
    record.symbol = "BTCUSDT"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "started"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["ts"].endswith("Z")
