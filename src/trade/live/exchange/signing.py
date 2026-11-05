"""币安 REST 签名实现（HMAC-SHA256 / Ed25519）。

规格：docs/09-币安数据与API.md §5.2
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore[import-not-found]
        Ed25519PrivateKey,
    )


def hmac_sha256_signature(secret: str, query_string: str) -> str:
    """对完整查询字符串做 HMAC-SHA256 签名，返回十六进制字符串（09 §5.2）。

    Args:
        secret: API Secret。
        query_string: 完整查询字符串（含 timestamp、recvWindow，不含 signature）。

    Returns:
        十六进制签名。
    """
    digest = hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest


def _ed25519_private_key(private_key_hex: str) -> Ed25519PrivateKey:
    """按十六进制私钥字节构造 Ed25519 私钥（延迟导入 cryptography）。

    cryptography 仅在 Ed25519 私钥方案下使用；服务器环境随 SDK 一并安装，
    本地 HMAC 方案与签名单测不依赖它（09 §5.2）。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as error:  # pragma: no cover - 服务器环境具备
        raise RuntimeError("Ed25519 签名需要 cryptography 包") from error
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))


def ed25519_signature(private_key_hex: str, query_string: str) -> str:
    """用 Ed25519 私钥对载荷签名，返回 URL 编码的 Base64 签名（09 §5.2）。

    Args:
        private_key_hex: Ed25519 私钥的十六进制字节串。
        query_string: 完整查询字符串。

    Returns:
        经过 URL 编码的 Base64 签名。
    """
    key = _ed25519_private_key(private_key_hex)
    raw = key.sign(query_string.encode("utf-8"))
    encoded = base64.b64encode(raw).decode("ascii")
    return urllib.parse.quote(encoded, safe="")
