"""运行模式的密钥来源与交易所客户端构造（docs/07 §2、§5）。

密钥只从环境变量读取；本模块不打印、不记录任何密钥内容（红线 6）。
- paper：`BINANCE_DEMO_API_KEY` / `BINANCE_DEMO_API_SECRET`，端点见 `demo_endpoints()`；
- live：`BINANCE_API_KEY` + `BINANCE_API_SECRET`，或 `BINANCE_PRIVATE_KEY_PATH`
  （Ed25519 十六进制或 RSA PEM，签名方式见 09 §5.2）。

规格：docs/07-实盘与风控.md §2、§5；docs/09-币安数据与API.md §5
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

from trade.live.exchange.client import ExchangeClient
from trade.live.exchange.endpoints import demo_endpoints, endpoints_for


class CredentialError(RuntimeError):
    """缺少密钥环境变量、私钥文件不可读或权限不安全（消息只含变量名/路径，不含内容）。"""


def check_secret_permissions(
    paths: Iterable[Path | None],
    *,
    stat_fn: Callable[[Path], os.stat_result] = os.stat,
    expected_uid: int | None = None,
) -> None:
    """检查密钥相关文件的权限（docs/07 §5）：必须 600 且属主为运行用户。

    只读取文件元数据，不读取内容（红线 6）。文件不存在时跳过（shadow / paper 没有实盘密钥）。

    Args:
        paths: 环境变量文件与私钥文件路径；None 项忽略。
        stat_fn: 取文件状态函数（测试注入）。
        expected_uid: 运行用户 uid；None 表示不检查属主（非 POSIX 平台）。

    Raises:
        CredentialError: 权限不是 600、属主不符或无法读取元数据。
    """
    for path in paths:
        if path is None:
            continue
        try:
            info = stat_fn(Path(path))
        except FileNotFoundError:
            continue
        except OSError as error:
            raise CredentialError(f"无法读取密钥文件元数据：{path}：{error}") from error
        mode = stat.S_IMODE(info.st_mode)
        if mode != 0o600:
            raise CredentialError(
                f"密钥文件权限 {mode:04o} 不是 600：{path}（只检查权限，不读取内容）"
            )
        if expected_uid is not None and info.st_uid != expected_uid:
            raise CredentialError(
                f"密钥文件属主 uid={info.st_uid} 不是运行用户 {expected_uid}：{path}"
            )


def api_key_env(mode: str) -> str:
    """返回该模式使用的 API Key 环境变量名。"""
    return "BINANCE_DEMO_API_KEY" if mode == "paper" else "BINANCE_API_KEY"


def api_secret_env(mode: str) -> str:
    """返回该模式使用的 API Secret 环境变量名。"""
    return "BINANCE_DEMO_API_SECRET" if mode == "paper" else "BINANCE_API_SECRET"


def _private_key_material() -> str | None:
    """读取 `BINANCE_PRIVATE_KEY_PATH` 指向的私钥文本（仅 live 使用）。"""
    raw_path = os.environ.get("BINANCE_PRIVATE_KEY_PATH")
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        raise CredentialError(f"私钥文件不存在：{path}（只检查路径，不输出内容）")
    return path.read_text(encoding="utf-8").strip()


def build_client(mode: str) -> ExchangeClient:
    """按运行模式构造交易所客户端。

    Args:
        mode: `paper` / `shadow` / `live`。

    Returns:
        带环境端点与密钥的 ExchangeClient。

    Raises:
        CredentialError: 缺少 API Key 或签名材料（消息含变量名）。
    """
    endpoints = demo_endpoints() if mode == "paper" else endpoints_for("mainnet")
    if mode == "shadow":
        # shadow 只用公开行情：不签名、不携带真实密钥；交易调用由 ReadOnlyMarketClient 拦截
        return ExchangeClient(
            endpoints=endpoints,
            api_key=os.environ.get("BINANCE_API_KEY") or "shadow-public",
            api_secret="shadow-public-only",
        )
    api_key = os.environ.get(api_key_env(mode), "")
    if not api_key:
        raise CredentialError(f"缺少环境变量 {api_key_env(mode)}")
    if api_key.startswith("-----BEGIN"):
        raise CredentialError("API Key 是 PEM 公钥；这里必须填币安生成的 API Key 字符串（09 §5.1）")
    secret: str | None = None
    private_key: str | None = None
    rsa_pem: str | None = None
    material = _private_key_material() if mode != "paper" else None
    if material is not None:
        if material.startswith("-----BEGIN"):
            rsa_pem = material
        else:
            private_key = material
    else:
        secret = os.environ.get(api_secret_env(mode))
        if not secret:
            raise CredentialError(
                f"缺少环境变量 {api_secret_env(mode)} 或 BINANCE_PRIVATE_KEY_PATH"
            )
    return ExchangeClient(
        endpoints=endpoints,
        api_key=api_key,
        api_secret=secret,
        private_key_hex=private_key,
        rsa_private_key_pem=rsa_pem,
    )
