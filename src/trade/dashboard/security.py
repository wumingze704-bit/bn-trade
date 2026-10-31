"""看板网络安全校验：回环监听与 Host/Origin 校验。

规格：docs/10-可视化看板.md §2、§9
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_address(address: str) -> bool:
    """判断监听地址是否为回环地址（10 §2）。"""
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def validate_host_header(host: str | None) -> bool:
    """校验请求 `Host` 头是否为本地回环主机（10 §9）。

    只接受 `localhost` / `127.0.0.1` / `::1`（可带端口，IPv6 用方括号），
    拒绝其他主机名，防止 DNS 重绑定把请求劫持到非本机服务。

    Args:
        host: 原始 `Host` 头值，如 `localhost:8501` 或 `[::1]:8501`。

    Returns:
        是否为回环主机。
    """
    if not host:
        return False
    if host.startswith("["):
        name = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        name = host.rsplit(":", 1)[0]
    else:
        name = host
    return name in _LOOPBACK_HOSTS


def validate_origin_header(origin: str | None) -> bool:
    """校验 `Origin` 头（如存在）是否同源（10 §9）。

    无 `Origin`（如命令行 WebSocket 客户端）或值为 `null` 视为放行；
    有 `Origin` 时必须为 `http(s)://localhost` / `127.0.0.1` / `[::1]`。

    Args:
        origin: 原始 `Origin` 头值。

    Returns:
        是否同源（或可放行）。
    """
    if origin is None or origin == "null":
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    return parsed.hostname in _LOOPBACK_HOSTS
