"""健全性检验测试（06 §7）。"""

from __future__ import annotations

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.sanity import deflated_sharpe, run_sanity_checks, s6_concentration, s9_compare


def _result(trades):
    return PortfolioResult(trades=trades)


def _trade(symbol="A", net=1.0) -> ExecutedTrade:
    return ExecutedTrade(
        symbol=symbol,
        group="train",
        signal_open_time=0,
        entry_open_time=60_000,
        exit_open_time=120_000,
        p=1.0,
        m=0.0,
        p_fill=100.0,
        sl=0.008,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        notional=10.0,
        qty=0.1,
        margin=2.0,
        bars_held=2,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.0,
        exit_fee=0.0,
        gross_pnl=net,
        net_pnl=net,
        capped_by=None,
    )


def test_run_sanity_checks标记未运行项():
    r = run_sanity_checks(s8="not_run_待真实数据", s10="not_run_盲测币未抽取", s11="not_run")
    assert r["S8"] == "not_run_待真实数据"
    assert r["S10"] == "not_run_盲测币未抽取"
    assert r["S2"] == "not_applicable"


def test_deflated_sharpe():
    dsr = deflated_sharpe(sharpe=1.0, n_trials=100, skew=0.0, kurtosis=3.0, n_obs=500)
    assert dsr < 1.0  # 多次试验惩罚后低于名义夏普


def test_s6_concentration():
    result = _result([_trade("A", 5.0), _trade("B", -1.0), _trade("C", -1.0)])
    out = s6_concentration(result, initial_equity=10.0)
    # 去掉最佳单币 A 后净收益 -2 < 0
    assert out["remove_best_coin_positive"] is False
    # 去掉收益最高 1% 交易（A）后仍为负
    assert out["remove_top1pct_positive"] is False


def test_s9_compare一致与不一致():
    a = _result([_trade("A", 1.0)])
    b = _result([_trade("A", 1.0)])
    assert s9_compare(a, b) is True
    c = _result([_trade("B", 2.0)])
    assert s9_compare(a, c) is False
