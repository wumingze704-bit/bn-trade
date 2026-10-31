"""看板网络来源校验测试。

规格：docs/10-可视化看板.md §2、§9
"""

from __future__ import annotations

from trade.dashboard.security import (
    is_loopback_address,
    validate_host_header,
    validate_origin_header,
)


def test_is_loopback_address() -> None:
    assert is_loopback_address("127.0.0.1") is True
    assert is_loopback_address("::1") is True
    assert is_loopback_address("0.0.0.0") is False
    assert is_loopback_address("8.8.8.8") is False
    assert is_loopback_address("not-an-ip") is False


def test_validate_host_header() -> None:
    assert validate_host_header("localhost:8501") is True
    assert validate_host_header("127.0.0.1:8501") is True
    assert validate_host_header("[::1]:8501") is True
    assert validate_host_header("localhost") is True
    assert validate_host_header("evil.com") is False
    assert validate_host_header("evil.com:8501") is False
    assert validate_host_header(None) is False


def test_validate_origin_header() -> None:
    assert validate_origin_header("http://localhost:8501") is True
    assert validate_origin_header("http://127.0.0.1:8501") is True
    assert validate_origin_header(None) is True
    assert validate_origin_header("null") is True
    assert validate_origin_header("http://evil.com") is False
    assert validate_origin_header("https://evil.com") is False
