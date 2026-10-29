"""numba 类型化装饰器包装。

numba 无类型存根，直接 ``@numba.njit`` 会触发 mypy --strict 的
``disallow-untyped-decorators``；此处提供带类型签名的包装（AGENTS.md §3.1）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

import numba

_F = TypeVar("_F", bound=Callable[..., Any])


def njit(func: _F) -> _F:
    """numba.njit 的类型化包装（cache=False，避免 pytest 下的缓存不一致）。"""
    return cast(_F, numba.njit(cache=False)(func))
