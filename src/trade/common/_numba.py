"""numba 类型化装饰器包装。

numba 无类型存根，直接 ``@numba.njit`` 会触发 mypy --strict 的
``disallow-untyped-decorators``；此处提供带类型签名的包装（AGENTS.md §3.1）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar, cast

import numba

_F = TypeVar("_F", bound=Callable[..., Any])

# 编译缓存开关（02 §7）：默认关闭，保持 pytest 下不写 __pycache__、不与旧缓存混用；
# 长时脚本设 TRADE_NUMBA_CACHE=1 时启用 cache=True，跨进程复用编译结果。
_CACHE_ENABLED = os.environ.get("TRADE_NUMBA_CACHE", "0") == "1"


def njit(func: _F) -> _F:
    """numba.njit 的类型化包装（cache 由环境变量 TRADE_NUMBA_CACHE 决定）。"""
    return cast(_F, numba.njit(cache=_CACHE_ENABLED)(func))
