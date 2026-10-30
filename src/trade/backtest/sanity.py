"""回测健全性检验 S1~S11（06 §7）。

P3（R0 驱动）可离线验证 S1/S4/S5/S6/S9/S11 的骨架；S2/S3 依赖模型训练（P4）、
S7 依赖实盘增量特征（P5）、S8/S10 依赖服务器真实数据与盲测币，均如实标记为未运行。
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    PortfolioResult,
    TradeRule,
    precompute_trades,
    reference_trade_net_pnl,
)
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig


def expected_max_sharpe_ratio(mean: float, std: float, n_trials: int) -> float:
    """计算多次试验下夏普比率的期望最大值 SR0（Bailey & López de Prado 2014，06 §7 S11）。

    SR0 = mean + std × E[max_N]，其中
    E[max_N] = (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e))，
    γ = Euler–Mascheroni 常数 ≈ 0.5772156649，Φ⁻¹ 为标准正态分布分位函数。
    单次试验（N = 1）时 E[max_1] = 0，SR0 退化为试验均值。

    Args:
        mean: 各试验夏普比率的均值（与 trial_sharpes 同周期单位）。
        std: 各试验夏普比率的标准差（样本标准差，ddof=1）。
        n_trials: 独立试验次数 N。

    Returns:
        期望最大夏普比率 SR0，与 mean/std 同周期单位。
    """
    if n_trials <= 0:
        return 0.0
    euler = 0.5772156649015329
    normal = NormalDist()
    max_z = 0.0
    if n_trials > 1:
        max_z = (1.0 - euler) * normal.inv_cdf(1.0 - 1.0 / n_trials) + euler * normal.inv_cdf(
            1.0 - 1.0 / (n_trials * math.e)
        )
    return mean + std * max_z


def deflated_sharpe(
    sharpe: float,
    trial_sharpes: list[float],
    skew: float,
    kurtosis: float,
    n_obs: int,
) -> float:
    """计算 Deflated Sharpe Ratio 概率（Bailey & López de Prado 2014，06 §7 S11）。

    DSR = PSR(SR0)，即以试验期望最大夏普 SR0 为基准的 Probabilistic Sharpe Ratio：
        z = (sharpe − SR0)·√(n_obs − 1) / √(1 − γ₃·sharpe + (γ₄ − 1)/4·sharpe²)
        DSR = Φ(z)
    其中 SR0 = mean(trial_sharpes) + std(trial_sharpes, ddof=1) × E[max_N]。

    周期单位约定：`sharpe` 与 `trial_sharpes` 必须是同一周期单位（本项目均为
    日收益年化夏普，`metrics.sharpe()` 对日收益乘 √365）；`n_obs` 为该周期的观测
    数（日数）；`skew`/`kurtosis` 无量纲（基于日收益）。偏度、峰度、观测数与夏普
    必须来自同一组收益，避免周期错配。

    Args:
        sharpe: 观测到的夏普比率（本项目为日收益年化值）。
        trial_sharpes: 测试段上各配置对应的夏普比率（与 sharpe 同周期单位）。
        skew: 测试段收益偏度（无量纲）。
        kurtosis: 测试段收益峰度（正态分布为 3，无量纲）。
        n_obs: 测试段收益观测数（须 > 1）。

    Returns:
        观测夏普超过多试验期望最大夏普的概率，范围为 [0, 1]。
        空试验列表或 n_obs ≤ 1 时返回 0.0（数据不足，视为不显著）。

    Raises:
        无；无效输入返回 0.0 而非抛出异常。
    """
    n_trials = len(trial_sharpes)
    if n_trials == 0 or n_obs <= 1:
        return 0.0

    variance_factor = 1.0 - skew * sharpe + (kurtosis - 1.0) / 4.0 * sharpe**2
    if variance_factor <= 0.0:
        return 0.0

    mean = float(np.mean(trial_sharpes))
    std = float(np.std(trial_sharpes, ddof=1)) if n_trials > 1 else 0.0
    sr0 = expected_max_sharpe_ratio(mean, std, n_trials)
    z_score = (sharpe - sr0) * math.sqrt(n_obs - 1) / math.sqrt(variance_factor)
    return float(NormalDist().cdf(z_score))


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
                t.p_fill,
                t.exit_price,
                t.notional,
                t.net_pnl,
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
        if not all(
            math.isclose(x, y, rel_tol=0.0, abs_tol=tol) for x, y in zip(a[4:], b[4:], strict=True)
        ):
            return False
    return True


def _bool_status(value: bool | None) -> str:
    """bool|None → passed/failed/not_run。"""
    if value is None:
        return "not_run"
    return "passed" if value else "failed"


def _features_status(features: dict[str, bool] | None) -> str:
    """dict[str, bool]|None → 全部为真即 passed。"""
    if features is None:
        return "not_run"
    return "passed" if all(features.values()) else "failed"


def s4_threshold_perturb(
    pf_below: float,
    pf_above: float,
    *,
    pf_threshold: float = 1.1,
) -> bool:
    """S4 阈值扰动：θ ± Δ 两边的 `base` 情景 PF 均 ≥ 1.1（06 §7 S4）。

    Args:
        pf_below: 阈值下调后 `base` 情景的 Profit Factor。
        pf_above: 阈值上调后 `base` 情景的 Profit Factor。
        pf_threshold: PF 下限（1.1）。

    Returns:
        两边均通过时返回 True。
    """
    return pf_below >= pf_threshold and pf_above >= pf_threshold


def s5_time_stability(
    monthly_net: list[float],
    fold_net: list[float],
    *,
    min_months_positive: int = 6,
    min_folds_positive: int = 4,
) -> dict[str, bool]:
    """S5 时间稳定性：正收益月数与正收益折数达标（06 §7 S5）。

    Args:
        monthly_net: 各测试月的净收益（USDT）。
        fold_net: 各折的净收益（USDT）。
        min_months_positive: 至少正收益月数。
        min_folds_positive: 至少正收益折数。

    Returns:
        {"months_positive": bool, "folds_positive": bool}。
    """
    months_positive = sum(1 for v in monthly_net if v > 0)
    folds_positive = sum(1 for v in fold_net if v > 0)
    return {
        "months_positive": months_positive >= min_months_positive,
        "folds_positive": folds_positive >= min_folds_positive,
    }


def run_sanity_checks(
    *,
    s1: str = "not_run",
    s2: str = "not_applicable",
    s3: str = "not_applicable",
    s4: bool | None = None,
    s5: dict[str, bool] | None = None,
    s6: dict[str, bool] | None = None,
    s7: str = "not_run",
    s8: str = "not_run",
    s9: bool | None = None,
    s10: str = "not_run",
    s11: str = "not_run",
) -> dict[str, str]:
    """汇总健全性检验结果（06 §7）。

    S9 在 P5 之前只覆盖组合层，其结果附注“仅覆盖组合层（06 §3.4）”。
    """
    s9_status = _bool_status(s9)
    if s9 is not None:
        s9_status = f"{s9_status}（仅覆盖组合层，06 §3.4）"
    return {
        "S1": s1,
        "S2": s2,
        "S3": s3,
        "S4": _bool_status(s4),
        "S5": _features_status(s5),
        "S6": _features_status(s6),
        "S7": s7,
        "S8": s8,
        "S9": s9_status,
        "S10": s10,
        "S11": s11,
    }


def compute_random_entry_candidates(
    bars_by_symbol: dict[str, pd.DataFrame],
    prefiltered_samples: pd.DataFrame,
    rules: dict[str, TradeRule],
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    H_label: int,
) -> list[tuple[str, int, float]]:
    """S1 预过滤样本候选的 reference 净盈亏（06 §7 S1）。

    对每个币，把预过滤样本（is_sample=1）全部视为候选入场点（p 强制置 1），复用
    `precompute_trades`（含 `simulate_exit` 出场逻辑）与 reference 仓位计算单笔净盈亏，
    得到 (symbol, signal_open_time, net_pnl) 列表，供随机入场基准抽样。

    Args:
        bars_by_symbol: 币种 → 分钟 bars。
        prefiltered_samples: 预过滤样本（至少含 symbol、open_time 列）。
        rules: 交易规则快照。
        fees: 费率。
        backtest_cfg/exit_cfg/sizing_cfg: 回测/出场/仓位配置（sizing_cfg 为 reference 口径）。
        k_sl/sl_min/sl_max/H_label: 止损与标签参数（06 §2、04 §7）。

    Returns:
        [(symbol, signal_open_time, net_pnl), ...]。
    """
    scen = scenario("base")
    out: list[tuple[str, int, float]] = []
    for symbol, bars in bars_by_symbol.items():
        sym_samples = prefiltered_samples[prefiltered_samples["symbol"] == symbol]
        if sym_samples.empty:
            continue
        signals = pd.DataFrame(
            {
                "open_time": sym_samples["open_time"].to_numpy(dtype="int64"),
                "p": np.ones(len(sym_samples), dtype="float64"),
            }
        )
        trades, _ = precompute_trades(
            bars,
            signals,
            symbol=symbol,
            group="train",
            rule=rules[symbol],
            scen=scen,
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=H_label,
        )
        for tr in trades:
            out.append(
                (
                    symbol,
                    tr.signal_open_time,
                    reference_trade_net_pnl(tr, sizing_cfg=sizing_cfg, fees=fees),
                )
            )
    return out


def random_entry_baseline(
    reference: PortfolioResult,
    candidate_net_pnls: list[tuple[str, int, float]],
    *,
    n_iters: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """S1 随机入场基准（06 §7 S1）：策略净收益 > 随机入场的 95 分位。

    在预过滤样本候选里随机抽取入场点，保持每币每月笔数与策略相同、出场规则相同
    （候选已由 `compute_random_entry_candidates` 用同一出场逻辑算出单笔净盈亏），
    固定种子可复现。

    Args:
        reference: 策略 reference 组合结果（统计每币每月笔数与策略净收益）。
        candidate_net_pnls: [(symbol, signal_open_time, net_pnl), ...]。
        n_iters: 重复次数（正式回测 1000，fixture 可调小）。
        seed: 随机种子。

    Returns:
        {"strategy_net": ..., "p95": ..., "passed": 0/1}。
    """
    actual_counts: dict[tuple[str, str], int] = {}
    for t in reference.trades:
        key = (t.symbol, _utc_month(t.signal_open_time))
        actual_counts[key] = actual_counts.get(key, 0) + 1

    strategy_net = sum(t.net_pnl for t in reference.trades)

    pools: dict[tuple[str, str], list[float]] = {}
    for symbol, ts, pnl in candidate_net_pnls:
        pools.setdefault((symbol, _utc_month(ts)), []).append(pnl)

    rng = np.random.default_rng(seed)
    sums = np.empty(n_iters, dtype="float64")
    for i in range(n_iters):
        total = 0.0
        for key, count in actual_counts.items():
            pool = pools.get(key)
            if not pool:
                continue  # 该币×月无预过滤候选（罕见），随机基准记 0
            pick = rng.choice(
                np.asarray(pool, dtype="float64"),
                size=count,
                replace=len(pool) < count,
            )
            total += float(pick.sum())
        sums[i] = total

    p95 = float(np.quantile(sums, 0.95))
    return {
        "strategy_net": strategy_net,
        "p95": p95,
        "passed": float(strategy_net > p95),
    }


def _utc_month(ts_ms: int) -> str:
    """UTC 月份（06 §7 S1 保持每币每月笔数）。"""
    from datetime import UTC, datetime

    month = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{month.year:04d}-{month.month:02d}"
