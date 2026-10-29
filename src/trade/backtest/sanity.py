"""回测健全性检验 S1~S11（06 §7）。

P3（R0 驱动）可离线验证 S1/S4/S5/S6/S9/S11 的骨架；S2/S3 依赖模型训练（P4）、
S7 依赖实盘增量特征（P5）、S8/S10 依赖服务器真实数据与盲测币，均如实标记为未运行。
"""

from __future__ import annotations

import math

import numpy as np

from trade.backtest.engine import PortfolioResult


def deflated_sharpe(
    sharpe: float,
    n_trials: int,
    skew: float,
    kurtosis: float,
    n_obs: int,
) -> float:
    """Deflated Sharpe Ratio（Bailey & López de Prado，06 §7 S11）。"""
    if n_trials <= 0 or n_obs <= 1:
        return 0.0
    euler = 0.5772156649015329
    expected = math.sqrt(2.0 * math.log(n_trials))
    variance = 1.0 - euler
    vr = (1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2) * variance
    vr = max(vr, 1e-12)
    return float((sharpe - expected) / math.sqrt(vr))


def s6_concentration(
    result: PortfolioResult,
    initial_equity: float,
    *,
    pf_threshold: float = 0.0,
) -> dict[str, bool]:
    """S6 集中度：去掉最佳单币 / 去掉收益最高 1% 交易后净收益仍为正。"""
    trades = result.trades
    if not trades:
        return {"remove_best_coin_positive": False, "remove_top1pct_positive": False}
    per_symbol: dict[str, float] = {}
    for t in trades:
        per_symbol[t.symbol] = per_symbol.get(t.symbol, 0.0) + t.net_pnl
    best_symbol = max(per_symbol, key=lambda s: per_symbol[s])
    without_best = sum(p for s, p in per_symbol.items() if s != best_symbol)
    by_pnl = sorted(trades, key=lambda t: t.net_pnl, reverse=True)
    k = max(1, round(len(trades) * 0.01))
    without_top1 = sum(t.net_pnl for t in by_pnl[k:])
    return {
        "remove_best_coin_positive": without_best > pf_threshold,
        "remove_top1pct_positive": without_top1 > pf_threshold,
    }


def s9_compare(fast: PortfolioResult, event: PortfolioResult, *, tol: float = 1e-9) -> bool:
    """S9 事件驱动对拍：快速模拟器与 event_sim 交易列表完全一致。"""

    def key(result: PortfolioResult) -> list[tuple[str, int, int, str, float, float, float, float]]:
        return [
            (
                t.symbol,
                t.entry_open_time,
                t.exit_open_time,
                t.exit_type,
                round(t.p_fill, 12),
                round(t.exit_price, 12),
                round(t.notional, 9),
                round(t.net_pnl, 9),
            )
            for t in result.trades
        ]

    fast_trades = key(fast)
    event_trades = key(event)
    if len(fast_trades) != len(event_trades):
        return False
    for a, b in zip(fast_trades, event_trades, strict=True):
        if a[:4] != b[:4]:
            return False
        if abs(a[4] - b[4]) > tol or abs(a[5] - b[5]) > tol:
            return False
    return True


def run_sanity_checks(
    *,
    s1: str = "not_run",
    s2: str = "not_applicable",
    s3: str = "not_applicable",
    s4: str = "not_run",
    s5: str = "not_run",
    s6: dict[str, bool] | None = None,
    s7: str = "not_run",
    s8: str = "not_run",
    s9: bool = False,
    s10: str = "not_run",
    s11: str = "not_run",
) -> dict[str, str]:
    """汇总健全性检验结果（06 §7）。"""
    return {
        "S1": s1,
        "S2": s2,
        "S3": s3,
        "S4": s4,
        "S5": s5,
        "S6": "passed" if s6 and all(s6.values()) else "failed",
        "S7": s7,
        "S8": s8,
        "S9": "passed" if s9 else "failed",
        "S10": s10,
        "S11": s11,
    }


def _random_baseline(
    candidate_pnls: list[float],
    n_picks: int,
    n_iters: int,
    rng: np.random.Generator,
) -> float:
    """S1 辅助：随机抽取 n_picks 笔的净收益和的分位数（返回策略需超越的 95 分位）。"""
    candidate = np.asarray(candidate_pnls, dtype="float64")
    if candidate.size == 0 or n_picks == 0:
        return 0.0
    sums = np.empty(n_iters, dtype="float64")
    for i in range(n_iters):
        idx = rng.integers(0, candidate.size, size=n_picks)
        sums[i] = candidate[idx].sum()
    return float(np.quantile(sums, 0.95))


def random_entry_baseline(
    result: PortfolioResult,
    candidate_pnls: list[float],
    *,
    n_iters: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """S1 随机入场基准：策略净收益 > 随机抽样的 95 分位（06 §7 S1）。

    Args:
        result: 策略组合结果。
        candidate_pnls: 全部候选（含被组合约束放弃的）在 reference 仓位下的净收益。
        n_iters: 重复次数（正式回测取 1000，fixture 可调小）。
        seed: 随机种子。

    Returns:
        {"strategy_net": ..., "p95": ..., "passed": 0/1}
    """
    rng = np.random.default_rng(seed)
    strategy_net = sum(t.net_pnl for t in result.trades)
    p95 = _random_baseline(candidate_pnls, len(result.trades), n_iters, rng)
    return {"strategy_net": strategy_net, "p95": p95, "passed": float(strategy_net > p95)}
