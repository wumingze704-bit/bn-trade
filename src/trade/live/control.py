"""运行中仓位调节（白名单覆盖）。

规格：docs/07-实盘与风控.md §7.1.1
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from trade.common.sizing import SizingConfig, validate_sizing
from trade.live.store import Store

# 可覆盖键白名单（07 §7.1.1）
OVERRIDE_KEYS = frozenset(
    {
        "mode",
        "position_frac",
        "max_positions",
        "leverage",
        "risk_per_trade",
        "max_risk_per_trade",
        "daily_loss_stop",
        "daily_loss_halt",
        "max_drawdown_halt",
        "max_consecutive_losses",
    }
)

_INT_KEYS = frozenset({"max_positions", "leverage", "max_consecutive_losses"})


class ControlError(Exception):
    """控制操作校验失败（键不在白名单或未通过 06 §5.4 校验）。"""


def effective_sizing(sizing: SizingConfig, overrides: Mapping[str, str]) -> SizingConfig:
    """把白名单覆盖应用到 sizing.yaml 得到生效配置（07 §7.1.1）。

    Args:
        sizing: `configs/sizing.yaml` 的基础配置。
        overrides: `runtime_overrides` 表中的字符串覆盖项。

    Returns:
        合并后的 `SizingConfig`。
    """
    values = sizing.model_dump()
    for key, raw in overrides.items():
        if key not in OVERRIDE_KEYS:
            continue
        values[key] = _parse_value(key, raw)
    return SizingConfig.model_validate(values)


def _parse_value(key: str, raw: str) -> Any:
    if key == "mode":
        return raw
    if key in _INT_KEYS:
        return int(raw)
    return float(raw)


def apply_override(
    store: Store,
    *,
    key: str,
    value: str,
    source: str,
    sizing: SizingConfig,
    equity: float,
    min_notional_median: float,
    sl_max: float,
    max_leverage: int,
) -> SizingConfig:
    """校验并写入一条白名单覆盖（07 §7.1.1）。

    写入前执行与 06 §5.4 相同的校验（用当前实时权益），不通过则拒绝。

    Args:
        store: 状态库。
        key: 白名单键。
        value: 覆盖值（字符串）。
        source: `cli` / `dashboard`。
        sizing: 基础仓位配置。
        equity: 当前实时权益（06 §5.4 校验 2）。
        min_notional_median: 交易币 minNotional 中位数（06 §5.4 校验 2）。
        sl_max: 最大止损幅度（06 §5.4 校验 3）。
        max_leverage: 杠杆分层上限（06 §5.4 校验 3）。

    Returns:
        应用覆盖后的生效配置。

    Raises:
        ControlError: 键不在白名单或合并结果未通过校验。
    """
    if key not in OVERRIDE_KEYS:
        raise ControlError(f"不允许覆盖的键：{key}（07 §7.1.1 白名单）")
    overrides = store.get_overrides()
    overrides = {**overrides, key: value}
    try:
        merged = effective_sizing(sizing, overrides)
    except ValidationError as error:
        raise ControlError(str(error)) from error
    errors = validate_sizing(
        merged,
        min_notional_median=min_notional_median,
        equity=equity,
        sl_max=sl_max,
        max_leverage=max_leverage,
    )
    if errors:
        raise ControlError("；".join(errors))
    store.set_override(key=key, value=value, source=source)
    return merged


def reset_overrides(store: Store) -> None:
    """清除所有覆盖，恢复 sizing.yaml 的值（07 §7.1.1）。"""
    store.reset_overrides()


def effective_sizing_from_store(store: Store, sizing: SizingConfig) -> SizingConfig:
    """从状态库读取覆盖并计算生效配置。"""
    return effective_sizing(sizing, store.get_overrides())
