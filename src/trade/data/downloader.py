"""历史数据下载器：校验和、重试、断点续传。

规格：docs/03-数据与选币.md §2.1；docs/09-币安数据与API.md §3.4
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

import httpx

_CHECKSUM_HEX_LEN = 64
_CHUNK_SIZE = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> str:
    """计算文件 SHA256 十六进制摘要。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksum_file(text: str) -> str:
    """从 `.CHECKSUM` 文件内容提取 SHA256 十六进制串。

    Args:
        text: `.CHECKSUM` 文件全文。

    Returns:
        小写 SHA256 十六进制串。

    Raises:
        ValueError: 内容中没有合法的 SHA256 摘要。
    """
    for token in text.split():
        if len(token) == _CHECKSUM_HEX_LEN and all(c in "0123456789abcdefABCDEF" for c in token):
            return token.lower()
    raise ValueError("CHECKSUM 文件缺少 SHA256 摘要")


def verify_checksum(path: Path, expected_sha256: str) -> bool:
    """校验文件 SHA256 是否与期望一致。"""
    return sha256_file(path) == expected_sha256.lower()


def download_file(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    retries: int = 3,
    backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0),
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """下载文件到 `dest`，失败指数退避重试，支持断点续传。

    Args:
        client: httpx 客户端（测试可注入 MockTransport）。
        url: 下载地址。
        dest: 目标文件路径。
        retries: 最多重试次数（不含首次）。
        backoff_seconds: 每次重试前的退避秒数。
        sleep: 退避等待函数（测试可替换为空操作）。

    Returns:
        下载完成后的目标路径。

    Raises:
        RuntimeError: 重试耗尽后仍未成功。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            headers: dict[str, str] = {}
            if part.exists() and part.stat().st_size > 0:
                headers["Range"] = f"bytes={part.stat().st_size}-"
            with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                response.raise_for_status()
                mode = "ab" if response.status_code == 206 else "wb"
                with part.open(mode) as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
            part.replace(dest)
            return dest
        except httpx.HTTPError as error:
            last_error = error
            if attempt < retries:
                sleep(backoff_seconds[min(attempt, len(backoff_seconds) - 1)])
    raise RuntimeError(f"下载失败：{url}: {last_error}") from last_error


def download_with_checksum(
    client: httpx.Client,
    url: str,
    dest: Path,
    checksum_url: str,
    *,
    retries: int = 3,
    backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0),
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """下载文件并按同名 `.CHECKSUM` 校验，失败重试。

    目标文件已存在且校验通过时直接跳过（断点续传语义，03 §2.1）。
    校验失败重试 3 次后抛错（03 §1.1 第 4 条）。

    Args:
        client: httpx 客户端。
        url: 下载地址。
        dest: 目标文件路径。
        checksum_url: `.CHECKSUM` 文件地址。
        retries: 校验失败后的最大重试次数。
        backoff_seconds: 每次重试前的退避秒数。
        sleep: 退避等待函数。

    Returns:
        目标文件路径。

    Raises:
        RuntimeError: 重试耗尽后校验仍失败。
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            checksum_text = client.get(checksum_url, follow_redirects=True)
            checksum_text.raise_for_status()
            expected = parse_checksum_file(checksum_text.text)
            if dest.exists() and verify_checksum(dest, expected):
                return dest
            download_file(
                client, url, dest, retries=retries, backoff_seconds=backoff_seconds, sleep=sleep
            )
            if verify_checksum(dest, expected):
                return dest
            raise ValueError("校验和校验失败")
        except (httpx.HTTPError, ValueError) as error:
            last_error = error
            if attempt < retries:
                sleep(backoff_seconds[min(attempt, len(backoff_seconds) - 1)])
    raise RuntimeError(f"下载校验失败：{url}: {last_error}") from last_error


VISION_BASE = "https://data.binance.vision"


def build_vision_url(symbol: str, data_type: str, period: str) -> str:
    """构造 data.binance.vision 数据文件 URL（03 §2.1、09 §3.4）。

    Args:
        symbol: 合约 symbol（如 BTCUSDT）。
        data_type: `klines` / `aggtrades` / `fundingrate` / `metrics`。
        period: 时间段标识，月 `YYYY-MM` 或日 `YYYY-MM-DD`。

    Returns:
        数据 zip 的 URL。

    Raises:
        ValueError: 未知数据类型。
    """
    if data_type == "klines":
        return f"{VISION_BASE}/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{period}.zip"
    if data_type == "aggtrades":
        return (
            f"{VISION_BASE}/data/futures/um/daily/aggTrades/{symbol}/"
            f"{symbol}-aggTrades-{period}.zip"
        )
    if data_type == "fundingrate":
        return (
            f"{VISION_BASE}/data/futures/um/monthly/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{period}.zip"
        )
    if data_type == "metrics":
        return f"{VISION_BASE}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{period}.zip"
    raise ValueError(f"未知数据类型：{data_type}")


def checksum_url(url: str) -> str:
    """返回数据文件同名的 `.CHECKSUM` 地址。"""
    return url + ".CHECKSUM"
