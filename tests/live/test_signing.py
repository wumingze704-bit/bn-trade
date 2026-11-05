"""签名单测：HMAC-SHA256（官方示例）与 Ed25519 往返。

规格：docs/09-币安数据与API.md §5.2
"""

from __future__ import annotations

import base64
import urllib.parse

import pytest

from trade.live.exchange.signing import ed25519_signature, hmac_sha256_signature

# 币安官方文档 HMAC-SHA256 示例（09 §5.2）
_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
_QUERY = (
    "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC"
    "&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
)
_EXPECTED = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_hmac_sha256_matches_official_example() -> None:
    assert hmac_sha256_signature(_SECRET, _QUERY) == _EXPECTED


def test_ed25519_round_trip() -> None:
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_hex = private_key.private_bytes_raw().hex()
    signature = ed25519_signature(private_hex, _QUERY)
    raw = base64.b64decode(urllib.parse.unquote(signature))
    public_key = private_key.public_key()
    public_key.verify(raw, _QUERY.encode("utf-8"))
    assert crypto is not None


def test_ed25519_signature_is_url_encoded() -> None:
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    private_hex = private_key.private_bytes_raw().hex()
    signature = ed25519_signature(private_hex, _QUERY)
    assert crypto is not None
    assert signature == urllib.parse.quote(signature, safe="")
