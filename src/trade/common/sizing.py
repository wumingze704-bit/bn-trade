"""仓位与资金计算（回测与实盘共用，唯一规格来源）。

规格：docs/06-回测引擎.md §5
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trade.common._numba import njit

# 截断原因编码（06 §5.3）
_CAP_NONE = 0
_CAP_RISK = 1
_CAP_LIQUIDITY = 2
_CAP_MARGIN = 3

_CAP_TO_STR = {
    _CAP_NONE: None,
    _CAP_RISK: "risk_cap",
    _CAP_LIQUIDITY: "liquidity",
    _CAP_MARGIN: "margin",
}

# 放弃原因编码（06 §5.3）
_STATUS_OK = 0
_STATUS_BELOW_MIN_NOTIONAL = 1
_STATUS_STEP_ROUNDING = 2

_STATUS_TO_STR = {
    _STATUS_OK: "accepted",
    _STATUS_BELOW_MIN_NOTIONAL: "below_min_notional",
    _STATUS_STEP_ROUNDING: "step_rounding",
}

# 仓位模式编码（06 §5.2）
_MODE_FIXED_FRACTION = 0
_MODE_FIXED_RISK = 1


class SizingConfig(BaseModel):
    """configs/sizing.yaml 的 pydantic 模型（06 §5.4）。"""

    model_config = ConfigDict(extra="forbid")

    initial_equity: float = Field(gt=0.0)
    mode: str = Field(pattern=r"^(fixed_fraction|fixed_risk)$")
    position_frac: float = Field(ge=0.01, le=0.95)
    leverage: int = Field(ge=1, le=125)
    max_positions: int = Field(ge=1)
    base: str = Field(pattern=r"^(equity|initial)$")
    risk_per_trade: float = Field(ge=0.001, le=0.05)
    max_risk_per_trade: float = Field(ge=0.005, le=0.10)
    daily_loss_stop: float = Field(gt=0.0)
    daily_loss_halt: float = Field(gt=0.0)
    max_drawdown_halt: float = Field(gt=0.0, le=1.0)
    max_consecutive_losses: int = Field(ge=1)
    liq_cap: float = Field(gt=0.0)
    maint_margin_rate: float = Field(ge=0.0, lt=1.0)

    @model_validator(mode="after")
    def _check_invariants(self) -> SizingConfig:
        """仓位内部校验（06 §5.4 校验 1、4）。"""
        if self.position_frac * self.max_positions > 0.95:
            raise ValueError("position_frac × max_positions 不得超过 0.95（06 §5.4 校验 1）")
        if self.daily_loss_stop >= self.daily_loss_halt:
            raise ValueError("daily_loss_stop 必须小于 daily_loss_halt（06 §5.4 校验 4）")
        return self


@dataclass(frozen=True)
class SizingResult:
    """单笔仓位计算结果（06 §5.3）。"""

    notional: float
    qty: float
    margin: float
    notional_raw: float
    capped_by: str | None
    status: str

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@njit
def _calc_notional_nb(
    base: float,
    position_frac: float,
    leverage: float,
    mode: int,
    risk_per_trade: float,
    max_risk_per_trade: float,
    liq_cap: float,
    mean_qv_60: float,
    available_margin: float,
    loss_rate: float,
    entry_price: float,
    step_size: float,
    min_notional: float,
) -> tuple[float, float, float, int, int, float]:
    """numba 版单笔仓位计算（06 §5.2、§5.3），与纯 Python 参考逐字段一致。"""
    if mode == _MODE_FIXED_FRACTION:
        notional_raw = base * position_frac * leverage
    else:
        notional_raw = base * risk_per_trade / loss_rate

    risk_cap = base * max_risk_per_trade / loss_rate
    liq_amount = liq_cap * mean_qv_60
    margin_amount = available_margin * 0.95 * leverage

    notional = notional_raw
    capped = _CAP_NONE
    if risk_cap < notional:
        notional = risk_cap
        capped = _CAP_RISK
    if liq_amount < notional:
        notional = liq_amount
        capped = _CAP_LIQUIDITY
    if margin_amount < notional:
        notional = margin_amount
        capped = _CAP_MARGIN

    qty = math.floor(notional / entry_price / step_size + 1e-9) * step_size
    qty_price = qty * entry_price
    if qty_price < min_notional * 1.1:
        status = _STATUS_BELOW_MIN_NOTIONAL
    elif qty_price < 0.9 * notional:
        status = _STATUS_STEP_ROUNDING
    else:
        status = _STATUS_OK

    margin = notional / leverage
    return notional, qty, margin, capped, status, notional_raw


def calc_notional(
    *,
    base: float,
    sl: float,
    loss_rate: float,
    entry_price: float,
    mean_qv_60: float,
    available_margin: float,
    step_size: float,
    min_notional: float,
    cfg: SizingConfig,
    use_numba: bool = True,
) -> SizingResult:
    """按仓位配置计算单笔名义价值、数量与保证金（06 §5.2、§5.3）。

    规格：docs/06-回测引擎.md §5（回测与实盘共用，禁止另写一份）。

    Args:
        base: 资金基准 B，USDT（base=initial 为初始本金，base=equity 为当前权益）。
        sl: 止损幅度，比例（0.008 = 0.8%）。
        loss_rate: 初始止损出场时每 1 USDT 名义价值的预计亏损（06 §5.2）。
        entry_price: 开仓成交价 P_fill。
        mean_qv_60: 信号 K 线 mean_60(quote_volume)，用于流动性上限。
        available_margin: 当前可用保证金（扣除其他持仓占用后）。
        step_size: 数量步长（exchangeInfo）。
        min_notional: 最小下单额（exchangeInfo）。
        cfg: 仓位配置，见 06 §5.4。
        use_numba: 是否使用 numba 版（测试比对时可传 False）。

    Returns:
        SizingResult，含 notional/qty/margin/capped_by/status。
    """
    if entry_price <= 0 or step_size <= 0:
        raise ValueError(f"非法交易规则：entry_price={entry_price}, step_size={step_size}")
    mode = _MODE_FIXED_RISK if cfg.mode == "fixed_risk" else _MODE_FIXED_FRACTION
    args = (
        base,
        cfg.position_frac,
        float(cfg.leverage),
        mode,
        cfg.risk_per_trade,
        cfg.max_risk_per_trade,
        cfg.liq_cap,
        mean_qv_60,
        available_margin,
        loss_rate,
        entry_price,
        step_size,
        min_notional,
    )
    if use_numba:
        notional, qty, margin, capped, status, raw = _calc_notional_nb(*args)
    else:
        notional, qty, margin, capped, status, raw = _calc_notional_py(*args)
    return SizingResult(
        notional=float(notional),
        qty=float(qty),
        margin=float(margin),
        notional_raw=float(raw),
        capped_by=_CAP_TO_STR[int(capped)],
        status=_STATUS_TO_STR[int(status)],
    )


def equity_floor_hit(base: float, cfg: SizingConfig, min_notional: float) -> bool:
    """判断是否触及权益下限（06 §5.3）。

    当 B × position_frac × leverage < minNotional × 1.1 时任何币都无法开仓。
    """
    return base * cfg.position_frac * float(cfg.leverage) < min_notional * 1.1


def loss_rate(
    *,
    sl: float,
    slip_stop: float,
    half_spread: float,
    fee_taker: float,
) -> float:
    """初始止损出场时每 1 USDT 名义价值的预计亏损（06 §5.2）。"""
    return sl + slip_stop + half_spread + 2.0 * fee_taker


def validate_sizing(
    cfg: SizingConfig,
    *,
    min_notional_median: float,
    equity: float,
    sl_max: float,
    max_leverage: int,
) -> list[str]:
    """跨配置仓位校验（06 §5.4 校验 2、3），返回错误文案列表（空表示通过）。"""
    errors: list[str] = []
    if equity * cfg.position_frac * float(cfg.leverage) < min_notional_median * 1.1:
        errors.append(
            "当前权益 × position_frac × leverage < 交易币中位数 minNotional × 1.1"
            "（06 §5.4 校验 2，触及权益下限）"
        )
    if cfg.leverage > max_leverage:
        errors.append(f"leverage 超过交易币杠杆分层上限 {max_leverage}（06 §5.4 校验 3）")
    limit = 0.5 * (1.0 / float(cfg.leverage) - cfg.maint_margin_rate)
    if sl_max > limit:
        errors.append(f"sl_max={sl_max} 超过强平距离上限 {limit:.4f}（06 §5.4 校验 3）")
    return errors


def _calc_notional_py(
    base: float,
    position_frac: float,
    leverage: float,
    mode: int,
    risk_per_trade: float,
    max_risk_per_trade: float,
    liq_cap: float,
    mean_qv_60: float,
    available_margin: float,
    loss_rate: float,
    entry_price: float,
    step_size: float,
    min_notional: float,
) -> tuple[float, float, float, int, int, float]:
    """纯 Python 参考实现（与 numba 版逐字段一致，AGENTS.md §6）。"""
    if mode == _MODE_FIXED_FRACTION:
        notional_raw = base * position_frac * leverage
    else:
        notional_raw = base * risk_per_trade / loss_rate

    risk_cap = base * max_risk_per_trade / loss_rate
    liq_amount = liq_cap * mean_qv_60
    margin_amount = available_margin * 0.95 * leverage

    notional = notional_raw
    capped = _CAP_NONE
    if risk_cap < notional:
        notional = risk_cap
        capped = _CAP_RISK
    if liq_amount < notional:
        notional = liq_amount
        capped = _CAP_LIQUIDITY
    if margin_amount < notional:
        notional = margin_amount
        capped = _CAP_MARGIN

    qty = math.floor(notional / entry_price / step_size + 1e-9) * step_size
    qty_price = qty * entry_price
    if qty_price < min_notional * 1.1:
        status = _STATUS_BELOW_MIN_NOTIONAL
    elif qty_price < 0.9 * notional:
        status = _STATUS_STEP_ROUNDING
    else:
        status = _STATUS_OK

    margin = notional / leverage
    return notional, qty, margin, capped, status, notional_raw
