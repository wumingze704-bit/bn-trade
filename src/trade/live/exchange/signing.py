"""币安 REST 签名实现（HMAC-SHA256 / RSA / Ed25519）。

规格：docs/09-币安数据与API.md §5.2

币安支持三种 API Key 类型：HMAC（对称 secret）、RSA（上传公钥，私钥签名）、
Ed25519（上传公钥，私钥签名）。RSA / Ed25519 用 `cryptography` 或已锁定的
`pycryptodome` 实现，私钥只在服务器上。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore[import-not-found]
        Ed25519PrivateKey,
    )

PrivateKeyKind = Literal["rsa", "ed25519"]


def private_key_kind(content: str) -> PrivateKeyKind:
    """按 PEM 头判断私钥类型（09 §5.2）。

    Args:
        content: 私钥文件内容（PEM 文本或 Ed25519 十六进制）。

    Returns:
        `rsa`（`BEGIN PRIVATE KEY` / `BEGIN RSA PRIVATE KEY`）或 `ed25519`（十六进制私钥）。

    Raises:
        ValueError: 传入的是公钥 PEM（`BEGIN PUBLIC KEY` / `BEGIN RSA PUBLIC KEY`），
            签名必须用私钥。这是把「上传给交易所的公钥」误当私钥时的常见错误。
    """
    stripped = content.strip()
    if not stripped.startswith("-----BEGIN"):
        return "ed25519"
    first_line = stripped.splitlines()[0].upper()
    if "PUBLIC KEY" in first_line:
        raise ValueError("这是公钥 PEM（BEGIN PUBLIC KEY），签名需要私钥 PEM（BEGIN PRIVATE KEY）")
    return "rsa"


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


def rsa_signature(private_key_pem: str, query_string: str) -> str:
    """用 RSA 私钥做 SHA256withRSA（PKCS#1 v1.5）签名（09 §5.2）。

    币安对 RSA 型 API Key 的签名规则与 Ed25519 相同：对完整查询字符串签名，
    结果 Base64 后 URL 编码。使用已锁定的 `pycryptodome`（`requirements-live.lock`），
    避免新增依赖。

    Args:
        private_key_pem: RSA 私钥 PEM 内容（PKCS#8 `BEGIN PRIVATE KEY` 或 PKCS#1
            `BEGIN RSA PRIVATE KEY`）。
        query_string: 完整查询字符串（含 timestamp、recvWindow，不含 signature）。

    Returns:
        经过 URL 编码的 Base64 签名。

    Raises:
        RuntimeError: 未安装 `pycryptodome`。
        ValueError: 私钥内容不是可解析的 RSA 私钥。
    """
    try:
        # pycryptodome 无类型存根（requirements-live.lock 已锁定版本）
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15
    except ImportError as error:  # pragma: no cover - 服务器已安装
        raise RuntimeError("RSA 签名需要 pycryptodome（requirements-live.lock）") from error
    try:
        key = RSA.import_key(private_key_pem)
    except (ValueError, IndexError, TypeError) as error:
        raise ValueError("私钥不是合法的 RSA 私钥 PEM") from error
    digest = SHA256.new(query_string.encode("utf-8"))
    raw = pkcs1_15.new(key).sign(digest)
    return urllib.parse.quote(base64.b64encode(raw).decode("ascii"), safe="")
