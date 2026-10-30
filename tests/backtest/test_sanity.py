"""健全性检验测试（06 §7）。"""

from __future__ import annotations

from dataclasses import replace

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.sanity import (
    deflated_sharpe,
    expected_max_sharpe_ratio,
    random_entry_baseline,
    run_sanity_checks,
    s4_threshold_perturb,
    s5_time_stability,
    s6_concentration,
    s9_compare,
)


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
    # 参考值来自 Bailey & López de Prado (2014) 公式（论文 Snippet 1 + Eq.2），用
    # scipy.stats.norm 独立实现逐步计算：mean=0.6, std(ddof=1)=0.316227766016838,
    # E[max_5]=1.192594001014789, SR0=0.977131336705989, z=0.417105026475396,
    # Φ(z)=0.661699205746612。
    dsr = deflated_sharpe(
        sharpe=1.0,
        trial_sharpes=[0.2, 0.4, 0.6, 0.8, 1.0],
        skew=0.0,
        kurtosis=3.0,
        n_obs=500,
    )
    assert abs(dsr - 0.661699205746612) < 1e-12


def test_expected_max_sharpe_ratio与论文Snippet1一致():
    # 论文 Snippet 1: getExpMaxSR(mu, sigma, numTrials) 返回 mu + sigma * maxZ。
    # 以 mu=0、sigma=1 代入，E[max_N] 的解析值。
    assert abs(expected_max_sharpe_ratio(0.0, 1.0, 10) - 1.574598301345750) < 1e-12
    assert abs(expected_max_sharpe_ratio(0.0, 1.0, 100) - 2.530602893201685) < 1e-12
    assert abs(expected_max_sharpe_ratio(0.0, 1.0, 1000) - 3.255121513652723) < 1e-12


def test_deflated_sharpe多次试验惩罚随试验数增加():
    one_trial = deflated_sharpe(sharpe=0.2, trial_sharpes=[0.2], skew=0.0, kurtosis=3.0, n_obs=500)
    many_trials = deflated_sharpe(
        sharpe=0.2, trial_sharpes=[0.1, 0.2, 0.3] * 34, skew=0.0, kurtosis=3.0, n_obs=500
    )
    assert many_trials < one_trial


def test_deflated_sharpe区分均值项():
    # 仅平移试验均值（标准差不变）：均值越高 SR0 越高，DSR 越低。
    base = deflated_sharpe(1.0, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 500)
    shifted = deflated_sharpe(1.0, [0.4, 0.6, 0.8, 1.0, 1.2], 0.0, 3.0, 500)
    assert shifted < base


def test_deflated_sharpe区分试验间标准差项():
    # 仅增大试验间标准差（均值不变）：std 越高 SR0 越高，DSR 越低。
    base = deflated_sharpe(1.0, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 500)
    wider = deflated_sharpe(1.0, [0.0, 0.3, 0.6, 0.9, 1.2], 0.0, 3.0, 500)
    assert wider < base


def test_deflated_sharpe区分样本量项():
    # 相同 Sharpe 与 SR0，观测数越多统计显著性越强，DSR 越高。
    short = deflated_sharpe(1.0, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 100)
    long = deflated_sharpe(1.0, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 1000)
    assert short < long


def test_deflated_sharpe单调概率变换且范围0到1():
    low = deflated_sharpe(0.5, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 500)
    high = deflated_sharpe(1.5, [0.2, 0.4, 0.6, 0.8, 1.0], 0.0, 3.0, 500)
    assert low < high
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0


def test_deflated_sharpe空试验列表返回零():
    assert deflated_sharpe(1.0, [], 0.0, 3.0, 500) == 0.0


def test_deflated_sharpe单次试验():
    # 单次试验 E[max_1]=0，SR0 退化为试验均值；DSR 仍为合法概率。
    assert expected_max_sharpe_ratio(0.2, 0.1, 1) == 0.2
    dsr = deflated_sharpe(0.2, [0.2], 0.0, 3.0, 500)
    assert 0.0 <= dsr <= 1.0


def test_deflated_sharpe无效观测数返回零():
    assert deflated_sharpe(1.0, [0.1, 0.2, 0.3], 0.0, 3.0, 1) == 0.0
    assert deflated_sharpe(1.0, [0.1, 0.2, 0.3], 0.0, 3.0, 0) == 0.0
    assert deflated_sharpe(1.0, [0.1, 0.2, 0.3], 0.0, 3.0, -5) == 0.0


def test_deflated_sharpe方差项非正返回零():
    # 方差项 1 - skew*sharpe + (kurtosis-1)/4*sharpe^2 = 1 - 20 + 2 = -17 <= 0
    assert deflated_sharpe(2.0, [0.1, 0.2, 0.3], 10.0, 3.0, 500) == 0.0


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


def test_s9_compare检测名义价值与净盈亏不一致():
    fast = _result([_trade("A", 1.0)])
    different_notional = _result([replace(_trade("A", 1.0), notional=10.1)])
    different_net_pnl = _result([replace(_trade("A", 1.0), net_pnl=1.1)])

    assert s9_compare(fast, different_notional) is False
    assert s9_compare(fast, different_net_pnl) is False


def test_run_sanity_checks_S9标注仅覆盖组合层():
    r = run_sanity_checks(s9=True, s4=True, s5={"months_positive": True, "folds_positive": True})
    assert r["S9"] == "passed（仅覆盖组合层，06 §3.4）"
    assert r["S4"] == "passed"
    assert r["S5"] == "passed"
    assert run_sanity_checks(s9=None)["S9"] == "not_run"


def test_s4_threshold_perturb():
    assert s4_threshold_perturb(1.2, 1.3) is True
    assert s4_threshold_perturb(1.05, 1.3) is False


def test_s5_time_stability():
    assert s5_time_stability([1, 1, 1, 1, 1, 1, -1, -1, -1, -1], [1, 1, 1, 1, -1]) == {
        "months_positive": True,
        "folds_positive": True,
    }
    assert s5_time_stability([1, 1, 1, 1, 1, -1, -1, -1, -1, -1], [1, 1, 1, -1, -1]) == {
        "months_positive": False,
        "folds_positive": False,
    }


def test_random_entry_baseline固定种子可复现():
    reference = _result([_trade("A", 2.0)])
    candidates = [("A", 0, 1.0), ("A", 0, -1.0)]
    a = random_entry_baseline(reference, candidates, n_iters=200, seed=3)
    b = random_entry_baseline(reference, candidates, n_iters=200, seed=3)
    assert a == b
    assert a["passed"] == 1.0
