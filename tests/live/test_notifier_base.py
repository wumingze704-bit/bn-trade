"""通知基础设施测试。

规格：docs/07-实盘与风控.md §8.2、§8.3、§8.5
"""

from __future__ import annotations

from trade.live.notifier.base import TokenBucket, format_event, truncate_bytes


def test_token_bucket_acquire_and_refill() -> None:
    clock = [0.0]
    bucket = TokenBucket(1, now_fn=lambda: clock[0])
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False  # 容量 1，无令牌
    clock[0] += 60.0  # 1 分钟补满 1 个令牌
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_token_bucket_burst_capacity() -> None:
    clock = [0.0]
    bucket = TokenBucket(3, now_fn=lambda: clock[0])
    assert [bucket.try_acquire() for _ in range(3)] == [True, True, True]
    assert bucket.try_acquire() is False


def test_format_event_label_and_fields() -> None:
    text = format_event("open", {"symbol": "SOLUSDT", "price": 100.0})
    assert "**开仓**" in text
    assert "price: 100.0" in text
    assert "symbol: SOLUSDT" in text


def test_format_event_redact_amounts() -> None:
    text = format_event(
        "close",
        {"symbol": "SOLUSDT", "net_pnl_usdt": 1.23, "win_rate": 0.5},
        redact_amounts=True,
    )
    assert "net_pnl_usdt: [已脱敏]" in text
    assert "win_rate: 0.5" in text


def test_truncate_bytes_under_limit_unchanged() -> None:
    assert truncate_bytes("短消息", 4096) == "短消息"


def test_truncate_bytes_over_limit() -> None:
    result = truncate_bytes("x" * 5000, 4096)
    assert len(result.encode("utf-8")) <= 4096
    assert result.endswith("…(截断)")


def test_truncate_bytes_multibyte_safe() -> None:
    result = truncate_bytes("中" * 5000, 100)
    assert len(result.encode("utf-8")) <= 100
