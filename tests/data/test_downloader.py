"""下载器校验和、重试、断点续传测试。

规格：docs/03-数据与选币.md §2.1
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from trade.data.downloader import (
    build_vision_url,
    checksum_url,
    download_file,
    download_with_checksum,
    parse_checksum_file,
    sha256_file,
    verify_checksum,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello")
    assert sha256_file(path) == hashlib.sha256(b"hello").hexdigest()


def test_parse_checksum_file() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    assert parse_checksum_file(f"{digest}  foo.zip\n") == digest


def test_parse_checksum_file_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_checksum_file("no checksum here")


def test_verify_checksum(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    assert verify_checksum(path, hashlib.sha256(b"abc").hexdigest())
    assert not verify_checksum(path, hashlib.sha256(b"xyz").hexdigest())


def test_download_file_writes_content(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello")

    dest = tmp_path / "x.zip"
    download_file(_client(handler), "https://example/x.zip", dest, sleep=lambda s: None)
    assert dest.read_bytes() == b"hello"


def test_download_file_retries_then_succeeds(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500 if calls["n"] < 3 else 200, content=b"ok")

    dest = tmp_path / "x.zip"
    download_file(_client(handler), "https://example/x.zip", dest, retries=3, sleep=lambda s: None)
    assert dest.read_bytes() == b"ok"
    assert calls["n"] == 3


def test_download_file_raises_after_retries(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(RuntimeError):
        download_file(
            _client(handler),
            "https://example/x.zip",
            tmp_path / "x.zip",
            retries=2,
            sleep=lambda s: None,
        )


def test_download_with_checksum_skips_if_valid(tmp_path: Path) -> None:
    content = b"payload"
    digest = hashlib.sha256(content).hexdigest()
    dest = tmp_path / "x.zip"
    dest.write_bytes(content)
    calls = {"download": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{digest}  x.zip\n")
        calls["download"] += 1
        return httpx.Response(200, content=content)

    download_with_checksum(
        _client(handler),
        "https://example/x.zip",
        dest,
        "https://example/x.zip.CHECKSUM",
        sleep=lambda s: None,
    )
    assert calls["download"] == 0  # 已存在且校验通过，未重新下载


def test_build_vision_url_klines() -> None:
    assert (
        build_vision_url("BTCUSDT", "klines", "2024-01")
        == "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip"
    )


def test_build_vision_url_daily() -> None:
    assert (
        build_vision_url("BTCUSDT", "daily", "2024-01")
        == "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip"
    )


def test_build_vision_url_aggtrades_daily() -> None:
    assert (
        build_vision_url("BTCUSDT", "aggtrades", "2024-01-02")
        == "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/"
        "BTCUSDT-aggTrades-2024-01-02.zip"
    )


def test_build_vision_url_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        build_vision_url("BTCUSDT", "unknown", "2024-01")


def test_checksum_url() -> None:
    url = build_vision_url("BTCUSDT", "klines", "2024-01")
    assert checksum_url(url) == url + ".CHECKSUM"
