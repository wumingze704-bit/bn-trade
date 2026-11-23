"""systemd 实例命名（deploy/ 下的模板与看门狗共用）。

- shadow / paper：`trade-runner@<候选ID>-<模式>.service`（MemoryMax=400M）
- live：`trade-runner-live@<候选ID>.service`（MemoryMax=500M）

规格：docs/07-实盘与风控.md §6.9；docs/02-系统架构与部署.md §6
"""

from __future__ import annotations


def unit_name(candidate: str, mode: str) -> str:
    """返回实例对应的 systemd 单元名（不含 `.service`）。

    Args:
        candidate: 候选 ID。
        mode: `shadow` / `paper` / `live`。

    Returns:
        单元名，如 `trade-runner@C1-shadow` 或 `trade-runner-live@C1`。

    Raises:
        ValueError: 未知模式。
    """
    if mode == "live":
        return f"trade-runner-live@{candidate}"
    if mode in {"shadow", "paper"}:
        return f"trade-runner@{candidate}-{mode}"
    raise ValueError(f"未知运行模式：{mode}")
