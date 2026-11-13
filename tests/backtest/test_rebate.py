"""代理返佣口径测试（06 §4.1、§6）。

返佣在成交后另行到账：只影响报告里的“含返佣”指标，不改变交易所费率、不影响标签
（标签用毛费率）与资金费。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.metrics import compute_metrics
from trade.common.config import BaseConfig
from trade.common.costs import effective_fee


def _trade(
    *,
    net_pnl: float,
    entry_fee: float,
    exit_fee: float,
    funding_fee: float = 0.0,
    rebate: float = 0.0,
) -> ExecutedTrade:
    """构造一笔最小交易记录（其余字段取不影响指标的常量）。"""
    return ExecutedTrade(
        symbol="AAAUSDT",
        group="train",
        signal_open_time=0,
        entry_open_time=60_000,
        exit_open_time=120_000,
        p=0.6,
        m=0.1,
        p_fill=1.0,
        sl=0.01,
        exit_price=1.01,
        exit_type="trail",
        exit_type_code=2,
        notional=100.0,
        qty=100.0,
        margin=20.0,
        bars_held=1,
        stop_updates=0,
        mfe_r=2.0,
        mae_r=-0.5,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        gross_pnl=net_pnl + entry_fee + exit_fee + funding_fee,
        net_pnl=net_pnl,
        capped_by=None,
        funding_fee=funding_fee,
        rebate=rebate,
    )


def test_effective_fee按返佣比例打折() -> None:
    assert effective_fee(0.0005, 0.0) == pytest.approx(0.0005)
    assert effective_fee(0.0005, 0.4) == pytest.approx(0.0003)
    assert effective_fee(0.0002, 0.4) == pytest.approx(0.00012)


def test_fee_schedule返佣后费率() -> None:
    fees = FeeSchedule(maker=0.0002, taker=0.0005, rebate_rate=0.4)
    assert fees.taker_eff == pytest.approx(0.0003)
    assert fees.maker_eff == pytest.approx(0.00012)
    assert FeeSchedule(0.0002, 0.0005).taker_eff == pytest.approx(0.0005)


def test_返佣率为0时含返佣指标与无返佣一致() -> None:
    trade = _trade(net_pnl=1.0, entry_fee=0.05, exit_fee=0.05)
    row = compute_metrics(PortfolioResult(trades=[trade]), 100.0)
    assert row["rebate_usdt"] == pytest.approx(0.0)
    assert row["net_return_usdt_rebated"] == pytest.approx(row["net_return_usdt"])
    assert row["profit_factor_rebated"] == pytest.approx(row["profit_factor"])


def test_返佣只作用于手续费不作用于资金费() -> None:
    entry_fee, exit_fee, funding = 0.05, 0.05, 0.30
    trade = _trade(
        net_pnl=1.0 - funding,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        funding_fee=funding,
        rebate=(entry_fee + exit_fee) * 0.4,
    )
    row = compute_metrics(PortfolioResult(trades=[trade]), 100.0)
    assert row["rebate_usdt"] == pytest.approx((entry_fee + exit_fee) * 0.4)
    assert row["net_return_usdt_rebated"] == pytest.approx(
        row["net_return_usdt"] + (entry_fee + exit_fee) * 0.4
    )
    # 资金费不参与返佣：若参与，返佣会多出 0.30 × 0.4
    assert row["rebate_usdt"] < (entry_fee + exit_fee + funding) * 0.4


def test_rebate_rate超范围报错() -> None:
    base = {
        "market": "usdm",
        "quote_asset": "USDT",
        "data_window": {
            "end_month": "2026-09",
            "months": 24,
            "warmup_days": 30,
            "tuning_months": 12,
            "holdout_months": 2,
        },
        "display_tz": "UTC",
        "paths": {"data_root": "./data", "artifact_root": "./artifacts"},
        "seed": 42,
    }
    with pytest.raises(ValidationError):
        BaseConfig(**base, fees={"maker": 0.0002, "taker": 0.0005, "rebate_rate": 0.6})
    with pytest.raises(ValidationError):
        BaseConfig(**base, fees={"maker": 0.0002, "taker": 0.0005, "rebate_rate": -0.1})
    cfg = BaseConfig(**base, fees={"maker": 0.0002, "taker": 0.0005})
    assert cfg.fees.rebate_rate == 0.0
