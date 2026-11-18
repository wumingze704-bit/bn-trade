"""运行 R0 信号回测（06 §3、§8）。

用法（在东京服务器，有 data/bars/ 与 exchangeInfo 快照时）：
    python scripts/backtest.py --signals artifacts/signals/r0.parquet \
        --rules artifacts/exchange_info/rules.json --run-id r0_20261029
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario, spread_measured_scenario
from trade.backtest.engine import (
    PRECOMPUTE_BAR_COLUMNS,
    BacktestConfig,
    PreparedSymbol,
    TradeRule,
    load_trade_rules,
    prepare_symbols,
    run_backtest,
)
from trade.backtest.event_sim import event_sim
from trade.backtest.metrics import (
    compute_metrics,
    daily_block_bootstrap_ci,
    daily_returns,
    kurtosis,
    monthly_return_stats,
    sharpe_daily,
    skew,
)
from trade.backtest.r0 import TrainConfig
from trade.backtest.replay import s8_status_from_payload
from trade.backtest.report import (
    append_experiments_log,
    compute_experiment_hash,
    experiments_log_path,
    read_experiments_log,
    write_report,
)
from trade.backtest.sanity import (
    S11_DSR_THRESHOLD,
    compute_random_entry_candidates,
    random_entry_baseline,
    run_sanity_checks,
    s2_label_shuffle,
    s3_feature_shift,
    s4_threshold_perturb,
    s5_time_stability,
    s6_concentration,
    s9_compare,
    s11_deflated_sharpe,
)
from trade.backtest.sweep import sizing_sweep
from trade.common.config import BaseConfig, load_config
from trade.common.costs import breakeven_slip
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    new_entry_symbols,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import load_universe_config
from trade.features.registry import FeaturesConfig
from trade.models.walkforward import load_walk_forward_signals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R0 信号回测")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="模型目录（读取 model_meta.json 的 threshold；模型信号时使用）",
    )
    parser.add_argument("--rules", type=Path, required=True, help="exchangeInfo 规则快照 JSON")
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--train", type=Path, default=Path("configs/train.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--final", action="store_true", help="包含封存集 M23~M24（仅 G3，06 §8）")
    parser.add_argument(
        "--s8-result",
        type=Path,
        default=None,
        help="S8 逐笔重放结果 JSON（06 §7 S8）；缺省读 reports/{run_id}/s8_replay.json",
    )
    parser.add_argument(
        "--signals-kind",
        choices=("r0", "model"),
        default=None,
        help="信号来源：R0 规则或模型 signals（T3-9 / T4-8）；给出 --model 时默认 model",
    )
    parser.add_argument(
        "--s2-result",
        type=Path,
        default=None,
        help="S2 标签打乱重训结果 JSON（含 base_profit_factor）",
    )
    parser.add_argument(
        "--s3-result",
        type=Path,
        default=None,
        help="S3 特征平移结果 JSON（含 baseline_net / shifted_net / s1_std）",
    )
    parser.add_argument(
        "--walk-forward-summary",
        type=Path,
        default=None,
        help="五折 walk_forward_summary.json：拼接各折测试段信号（按行 θ，06 §8）",
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=None,
        help="数据集 manifest.json：报告首页注明段尾丢弃样本数（04 §8、06 §9）",
    )
    parser.add_argument(
        "--features-dir",
        type=Path,
        default=None,
        help=(
            "特征目录 data/features/{run_id}：S1 随机入场基准取 is_sample=1 的预过滤样本"
            "（06 §7 S1）"
        ),
    )
    parser.add_argument(
        "--limit-coins",
        type=int,
        default=None,
        help="只处理前 N 个币（按 alias 升序）；小内存机器跑通流程用，正式运行不要给",
    )
    return parser


def _run_scenario(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    *,
    scen: str,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    features: FeaturesConfig,
    initial_equity: float,
    trade_masks: dict[str, np.ndarray] | None = None,
    new_entry_symbols: set[str] | None = None,
    scenario_override: object | None = None,
    p_floor: float | None = None,
    theta_offset: float = 0.0,
):
    """运行单个成本情景的回测（06 §3.4）。"""
    result, _ = run_backtest(
        bars_by_symbol,
        signals,
        rules,
        groups,
        scen_name=scen,
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        k_sl=features.label.k_sl,
        sl_min=features.label.sl_min,
        sl_max=features.label.sl_max,
        H_label=features.label.H_label,
        initial_equity=initial_equity,
        trade_masks=trade_masks,
        new_entry_symbols=new_entry_symbols,
        scenario_override=scenario_override,  # type: ignore[arg-type]
        p_floor=p_floor,
        theta_offset=theta_offset,
    )
    return result


def _monthly_fold_net(
    result, fold_of_month: dict[str, int] | None = None, *, test_months: list[str] | None = None
) -> tuple[list[float], list[float]]:
    """按测试月与 5 折汇总净收益（06 §7 S5、03 §6）。

    Args:
        result: 组合模拟结果。
        fold_of_month: 测试月 → 折号（来自 `walk_forward_summary.json`）；None 时按日历
            月映射到折（第 1、2 个测试月为 F1 …），不按「有交易的月份序号」分组。
        test_months: 测试段全部月份（`YYYY-MM`，升序）；用于没有交易月份的折映射。
    """
    monthly: dict[str, float] = {}
    for t in result.trades:
        month = _utc_month(t.signal_open_time)
        monthly[month] = monthly.get(month, 0.0) + t.net_pnl
    months = sorted(monthly)
    monthly_net = [monthly[m] for m in months]
    folds: dict[int, float] = {}
    for i, m in enumerate(months):
        if fold_of_month is not None:
            fold = fold_of_month.get(m)
            if fold is None:
                raise ValueError(f"测试月 {m} 不在 walk_forward_summary 的任一分折内（06 §8）")
        elif test_months is not None:
            if m not in test_months:
                raise ValueError(f"交易月份 {m} 不在测试段月份列表内（06 §7 S5）")
            fold = test_months.index(m) // 2 + 1
        else:
            fold = i // 2 + 1
        folds[fold] = folds.get(fold, 0.0) + monthly[m]
    fold_net = [folds[f] for f in sorted(folds)]
    return monthly_net, fold_net


def _utc_month(ts_ms: int) -> str:
    """UTC 月份（06 §7 S5）。"""
    month = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return f"{month.year:04d}-{month.month:02d}"


def s8_status(path: Path, run_id: str) -> str:
    """读取 S8 逐笔重放结果（06 §7 S8）。

    S8 必须在服务器上用保留的原始 aggTrades 运行，因此正式回测自身只负责把状态写入
    `gate.json`；结果文件缺失时返回带执行命令的 not_run 说明。`scripts/s8_replay.py`
    运行后会回填同一份 `gate.json`。

    Args:
        path: `s8_replay.json` 路径。
        run_id: 本次正式回测的 run_id（用于提示命令）。

    Returns:
        形如 `passed（...）` / `not_run（...）` 的状态文本。
    """
    if not path.is_file():
        return (
            "not_run（服务器执行：python scripts/s8_replay.py "
            f"--run-id {run_id} --signals <signals.parquet> --rules <rules.json>）"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return "not_run（s8_replay.json 格式不合法）"
    return s8_status_from_payload(payload)


def _tick_relative_note(
    prepared: Mapping[str, PreparedSymbol], rules: Mapping[str, TradeRule]
) -> str:
    """当前交易规则套用到历史价格的口径注记（06 §9 A14、T0 报告首页要求）。

    统计每币 `tick_size / 历史月收盘价中位数` 的最大值，列出超过 20bp 的币（06 §9）。
    """
    worst = 0.0
    offenders: list[tuple[str, float]] = []
    for symbol, item in sorted(prepared.items()):
        rule = rules.get(symbol)
        if rule is None or item.close.shape[0] == 0:
            continue
        months = item.open_time.astype("datetime64[ms]").astype("datetime64[M]")
        ratios: list[float] = []
        for month in np.unique(months):
            prices = item.close[months == month]
            median = float(np.median(prices))
            if median > 0.0:
                ratios.append(rule.tick_size / median)
        if not ratios:
            continue
        peak = max(ratios)
        worst = max(worst, peak)
        if peak > 0.002:
            offenders.append((symbol, peak))
    offenders.sort(key=lambda pair: pair[1], reverse=True)
    detail = "、".join(f"{name} {value * 1e4:.1f}bp" for name, value in offenders)
    return (
        "交易规则口径：回测用当前 exchangeInfo 快照套用到历史价格（06 §9 A14）；"
        f"tick_size / 历史月收盘价中位数 的最大值为 {worst * 1e4:.1f}bp，"
        f"超过 20bp 的币 {len(offenders)} 个" + (f"（{detail}）" if detail else "")
    )


def _segment_tail_note(manifest_path: Path | None) -> str:
    """数据段末尾丢弃的样本数注记（04 §8、06 §9）。"""
    if manifest_path is None:
        return "段尾丢弃样本数：未提供 --dataset-manifest，未读取"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    dropped = payload.get("label_dropped") or {}
    segment_tail = dropped.get("segment_tail") or {}
    total = segment_tail.get("total")
    if total is None:
        return f"段尾丢弃样本数：manifest 未记录（{manifest_path.name}）"
    return (
        f"段尾丢弃样本数：{int(total)} 条（因数据段末尾不足 H_max 或跨段，"
        f"见 {manifest_path.name} 的 label_dropped.segment_tail）"
    )


def _prefilter_sample_frame(
    features_dir: Path,
    prepared: Mapping[str, PreparedSymbol],
    trade_masks: Mapping[str, np.ndarray],
) -> pd.DataFrame:
    """读取 `is_sample = 1` 的预过滤样本并应用交易范围掩码（06 §7 S1）。

    S1 的随机入场基准必须从**预过滤样本**里抽样，而不是从策略自己的信号里抽样：
    后者会继承模型的选币结果，随机基准失去意义。样本按 `open_time` 对齐到 bars
    行位置后套用 `U(R)` 掩码，保证随机入场也只发生在可交易分钟。

    Args:
        features_dir: 数据集特征目录 `data/features/{run_id}`。
        prepared: 币种 → 已准备的 numpy 数组（提供 open_time）。
        trade_masks: 币种 → 与 bars 等长的 bool 交易范围掩码。

    Returns:
        含 `symbol`、`open_time` 两列的 DataFrame。

    Raises:
        FileNotFoundError: 任一币缺少特征文件（红线 13）。
    """
    frames: list[pd.DataFrame] = []
    for alias, item in prepared.items():
        path = features_dir / f"{alias}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"S1 需要特征文件但缺失：{path}（06 §7 S1）")
        frame = pd.read_parquet(path, columns=["open_time", "is_sample"])
        ts = frame["open_time"].to_numpy(dtype="int64")
        keep = frame["is_sample"].to_numpy(dtype="int64") == 1
        mask = trade_masks.get(alias)
        if mask is not None:
            bars_ts = item.open_time
            pos = np.searchsorted(bars_ts, ts)
            inside = pos < bars_ts.shape[0]
            inside[inside] = bars_ts[pos[inside]] == ts[inside]
            keep &= inside
            keep[inside] &= mask[pos[inside]]
        if keep.any():
            frames.append(pd.DataFrame({"symbol": alias, "open_time": ts[keep]}))
    if not frames:
        return pd.DataFrame({"symbol": pd.Series(dtype="object"), "open_time": []})
    return pd.concat(frames, ignore_index=True)


def _distinct_trial_sharpes(rows: list[dict[str, object]]) -> list[float]:
    """从 EXPERIMENTS.log 取每个 run_id 的日频夏普（06 §7 S11）。

    同一 run_id 可能被多次追加（重跑、回填），试验数 N 只应计一次；`kind` 为
    tuning/test 的行才是用于 S11 的独立试验，smoke/final 不计入。
    """
    seen: dict[str, float] = {}
    for row in rows:
        kind = str(row.get("kind") or "tuning")
        if kind not in {"tuning", "test"}:
            continue
        sharpe_value = row.get("sharpe")
        if sharpe_value is None:
            continue
        seen.setdefault(str(row.get("run_id")), float(sharpe_value))  # type: ignore[arg-type]
    return list(seen.values())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = load_config(BaseConfig, args.base)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    model_meta: dict[str, object] | None = None
    if args.model is not None:
        model_meta = json.loads((args.model / "model_meta.json").read_text(encoding="utf-8"))
        backtest_cfg = backtest_cfg.model_copy(
            update={"threshold": float(model_meta["threshold"])}  # type: ignore[arg-type]
        )
    signals_kind = args.signals_kind or ("model" if args.model is not None else "r0")
    model_id = str(model_meta["model_id"]) if model_meta is not None else None
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    features = load_config(FeaturesConfig, args.features)
    train_cfg = load_config(TrainConfig, args.train)
    universe_cfg = load_universe_config(args.universe)
    months = universe_months(base)
    # 非 --final 时排除封存集才首次入选的币：本地没有它们的 bars，红线 2 禁止加载（03 §6）。
    dev_months = months if args.final else months[: len(months) - base.data_window.holdout_months]
    coins = collect_universe_coins(base.paths.data_root, dev_months)
    if args.limit_coins is not None:
        # 小内存跑通用：只取有首个调参月数据的币（缺数据的币正式运行必须报错，红线 13）
        first_key = months[0].replace("-", "")
        coins = [
            coin
            for coin in coins
            if (base.paths.data_root / "bars" / coin.alias / f"{first_key}.parquet").is_file()
        ][: args.limit_coins]

    wb = window_bounds(base)
    run_end_ms = wb.end_ms if args.final else wb.holdout_start_ms
    test_months = months[
        base.data_window.tuning_months : len(months) - base.data_window.holdout_months
    ]
    fold_summary: list[dict[str, object]] | None = None
    if args.walk_forward_summary is not None:
        # 五折按行阈值拼接：每折用自己的 θ，禁止用某一折的 θ 交易其他折（06 §8）
        signals, fold_summary = load_walk_forward_signals(
            args.walk_forward_summary, expected_months=test_months
        )
    else:
        signals = pd.read_parquet(args.signals)
        if "theta" not in signals.columns:
            # 单模型模式用模型元数据的 θ、R0 用 backtest.yaml 的阈值填充；已有 θ 不覆盖
            fill_theta = float(backtest_cfg.threshold)
            signals = signals.assign(theta=fill_theta)
        if "fold_id" not in signals.columns:
            signals = signals.assign(fold_id="R0" if args.model is None else str(model_id))
        # 非五折模式必须把信号裁剪到测试段 M13~M22：否则权益曲线从 M1 起算，调参期
        # 的交易会混进测试段结果（06 §8、红线 3）。data_window 未划分测试段
        # （tuning_months 覆盖全部月份，仅见于冒烟 fixture）时整窗即测试段，不裁剪。
        if test_months:
            window_start = int(pd.Timestamp(f"{test_months[0]}-01", tz="UTC").timestamp() * 1000)
            window_end = int(
                (
                    pd.Timestamp(f"{test_months[-1]}-01", tz="UTC") + pd.DateOffset(months=1)
                ).timestamp()
                * 1000
            )
            before = len(signals)
            signals = signals[
                (signals["open_time"] >= window_start) & (signals["open_time"] < window_end)
            ].reset_index(drop=True)
            if signals.empty:
                raise ValueError(
                    f"信号文件 {args.signals} 在测试段 {test_months[0]}~{test_months[-1]} 内没有行"
                    "（06 §8）"
                )
            print(
                f"信号裁剪到测试段 {test_months[0]}~{test_months[-1]}：{before} → {len(signals)} 行"
            )
        else:
            print("data_window 未划分测试段（tuning_months 覆盖全部月份），信号不裁剪")

    rules = load_trade_rules(args.rules, base.paths.data_root / "universe" / "aliases.json")
    groups = {coin.alias: coin.role for coin in coins}

    months = universe_months(base)
    membership = load_universe_membership(base.paths.data_root, dev_months)
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    trade_masks: dict[str, np.ndarray] = {}
    for coin in coins:
        bars_by_symbol[coin.alias] = load_bars(
            base.paths.data_root,
            coin.alias,
            wb.start_ms,
            run_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=args.final,
            columns=PRECOMPUTE_BAR_COLUMNS,
        )
        trade_masks[coin.alias] = universe_trade_mask(
            bars_by_symbol[coin.alias]["open_time"].to_numpy(dtype="int64"),
            coin.symbol,
            membership,
            universe_cfg.rule.new_entry_delay_days,
        )

    # 每币 numpy 数组只准备一次（06 §3.4），预计算与盯市复用同一份数组。
    prepared = prepare_symbols(bars_by_symbol, atr_timeframe_min=features.label.atr_timeframe_min)

    ne_symbols = new_entry_symbols(membership, months[: base.data_window.tuning_months])
    new_entry_aliases = {coin.alias for coin in coins if coin.symbol in ne_symbols}

    fees = FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate)
    # 实测点差情景（06 §4.3）：只在配置启用时读取点差画像，缺失即报错（不静默回退 base）
    spread_override = None
    if "spread_measured" in backtest_cfg.scenarios:
        from scripts.spread_profile import load_half_spread_overrides

        spread_override = spread_measured_scenario(
            load_half_spread_overrides(
                Path(backtest_cfg.costs.spread_profile or ""), sorted(prepared)
            )
        )
    # reference 运行权益/口径来自配置（06 §5.1），user 运行按 sizing.yaml（06 §5.4）。
    reference_sizing = sizing_cfg.model_copy(
        update={
            "initial_equity": backtest_cfg.reference.initial_equity,
            "mode": backtest_cfg.reference.mode,
            "risk_per_trade": backtest_cfg.reference.risk_per_trade,
            "base": backtest_cfg.reference.base,
        }
    )
    runs = {
        "reference": (reference_sizing, backtest_cfg.reference.initial_equity),
        "user": (sizing_cfg, sizing_cfg.initial_equity),
    }
    run_results: dict[str, dict[str, object]] = {}
    normal_scenarios = [name for name in backtest_cfg.scenarios if name != "breakeven"]
    for run_name, (sizing, equity) in runs.items():
        run_results[run_name] = {}
        for scen in normal_scenarios:
            scenario_override = spread_override if scen == "spread_measured" else None
            run_results[run_name][scen] = _run_scenario(
                prepared,
                signals,
                rules,
                groups,
                scen=scen,
                fees=fees,
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing,
                features=features,
                initial_equity=equity,
                trade_masks=trade_masks,
                new_entry_symbols=new_entry_aliases,
                scenario_override=scenario_override,
            )
        if "breakeven" in backtest_cfg.scenarios:

            def _net(slip: float, sizing: object = sizing, equity: float = equity) -> float:
                result = _run_scenario(
                    prepared,
                    signals,
                    rules,
                    groups,
                    scen="base",
                    fees=fees,
                    backtest_cfg=backtest_cfg,
                    exit_cfg=exit_cfg,
                    sizing_cfg=sizing,  # type: ignore[arg-type]
                    features=features,
                    initial_equity=equity,
                    trade_masks=trade_masks,
                    new_entry_symbols=new_entry_aliases,
                    scenario_override=replace(scenario("base"), name="breakeven", fixed_slip=slip),
                )
                return float(sum(trade.net_pnl for trade in result.trades))

            breakeven = breakeven_slip(net_return=_net, hi=0.01, max_iter=10)
            run_results[run_name]["breakeven"] = _run_scenario(
                prepared,
                signals,
                rules,
                groups,
                scen="base",
                fees=fees,
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing,
                features=features,
                initial_equity=equity,
                trade_masks=trade_masks,
                new_entry_symbols=new_entry_aliases,
                scenario_override=replace(scenario("base"), name="breakeven", fixed_slip=breakeven),
            )

    reference_result = run_results["reference"]["base"]
    reference_metrics = compute_metrics(
        reference_result, backtest_cfg.reference.initial_equity, months=test_months
    )

    # G2-U（08 §3）：user 运行（实际仓位）的月度收益与回撤口径，只判定与展示，不替代 G2。
    user_base = run_results["user"]["base"]
    user_stats = monthly_return_stats(
        user_base.equity_curve, sizing_cfg.initial_equity, months=test_months
    )
    user_dd = (
        float(user_base.equity_curve["drawdown"].max()) if not user_base.equity_curve.empty else 0.0
    )
    user_stress = run_results["user"].get("stress")
    stress_net = (
        float(sum(t.net_pnl for t in user_stress.trades)) if user_stress is not None else 0.0
    )
    g2_user = {
        **user_stats,
        "max_drawdown": user_dd,
        "stress_net_return_usdt": stress_net,
        "thresholds": {
            "median_monthly_return_gt": 0.0,
            "max_drawdown_max": 0.20,
            "losing_months_max": 4,
            "worst_monthly_return_min": -0.12,
            "stress_net_return_usdt_gt": 0.0,
        },
        "passed": bool(
            user_stats["median_monthly_return"] > 0.0
            and user_dd <= 0.20
            and user_stats["losing_months"] <= 4
            and user_stats["worst_monthly_return"] >= -0.12
            and stress_net > 0.0
        ),
    }

    # 仓位比例敏感性扫描（06 §5.5）：每个比例都重跑完整组合模拟。
    sweep_frame = sizing_sweep(
        prepared,
        signals,
        rules,
        groups,
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        features_cfg=features,
        fractions=list(backtest_cfg.sizing_sweep.position_frac),
        initial_equity=sizing_cfg.initial_equity,
        trade_masks=trade_masks,
    )

    # S1 随机入场基准（06 §7 S1，固定种子可复现）：候选来自 is_sample=1 的预过滤样本，
    # 并套用交易范围掩码；不给 --features-dir 时 S1 记 not_run（不静默改用策略信号）。
    if args.features_dir is None:
        candidates: list[tuple[str, int, float]] = []
        s1_status = "not_run（未提供 --features-dir，无法取预过滤样本）"
    else:
        candidates = compute_random_entry_candidates(
            prepared,
            _prefilter_sample_frame(args.features_dir, prepared, trade_masks),
            rules,
            fees,
            backtest_cfg,
            exit_cfg,
            reference_sizing,
            features.label.k_sl,
            features.label.sl_min,
            features.label.sl_max,
            features.label.H_label,
            trade_masks=trade_masks,
        )
        s1_status = ""
    s1 = random_entry_baseline(
        reference_result,
        candidates,
        n_iters=backtest_cfg.sanity.random_iters,
        seed=backtest_cfg.sanity.random_seed,
    )

    # S4 阈值扰动（06 §7 S4）：对每行 θ 统一加减 Δ。两次扰动共用一次 p ≥ θ_min − Δ 的预计算。
    pfs: dict[str, float] = {}
    s4_floor = float(signals["theta"].min()) - backtest_cfg.sanity.threshold_perturb
    for direction, delta in (
        ("below", -backtest_cfg.sanity.threshold_perturb),
        ("above", backtest_cfg.sanity.threshold_perturb),
    ):
        presult = _run_scenario(
            prepared,
            signals,
            rules,
            groups,
            scen="base",
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=reference_sizing,
            features=features,
            initial_equity=backtest_cfg.reference.initial_equity,
            trade_masks=trade_masks,
            new_entry_symbols=new_entry_aliases,
            p_floor=s4_floor,
            theta_offset=delta,
        )
        pfs[direction] = compute_metrics(presult, backtest_cfg.reference.initial_equity)[
            "profit_factor"
        ]
    s4 = s4_threshold_perturb(pfs["below"], pfs["above"])

    # S5 时间稳定性（06 §7 S5）。有 walk_forward_summary 时按真实折号汇总。
    fold_of_month: dict[str, int] | None = None
    if fold_summary is not None:
        fold_of_month = {
            str(month): index
            for index, fold in enumerate(fold_summary, start=1)
            for month in fold["test_months"]  # type: ignore[union-attr]
        }
    monthly_net, fold_net = _monthly_fold_net(
        reference_result, fold_of_month, test_months=test_months or None
    )
    s5 = s5_time_stability(
        monthly_net,
        fold_net,
        min_months_positive=backtest_cfg.sanity.min_months_positive,
        min_folds_positive=backtest_cfg.sanity.min_folds_positive,
    )

    # S6 集中度（06 §7 S6）。
    s6 = s6_concentration(reference_result, backtest_cfg.reference.initial_equity)

    # S2 / S3 由训练侧脚本产出结果 JSON，这里只做判定与写入 gate（06 §7）。
    s2_status = "not_run"
    if args.s2_result is not None:
        payload = json.loads(args.s2_result.read_text(encoding="utf-8"))
        s2_status = s2_label_shuffle(float(payload["base_profit_factor"]))
    s3_status = "not_run"
    if args.s3_result is not None:
        payload = json.loads(args.s3_result.read_text(encoding="utf-8"))
        s3_status = s3_feature_shift(
            float(payload["baseline_net"]),
            float(payload["shifted_net"]),
            float(payload["s1_std"]),
        )

    # S9 事件驱动对拍（P5 之前仅覆盖组合层，06 §3.4）。
    event = event_sim(
        prepared,
        signals,
        rules,
        groups,
        scen_name="base",
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=reference_sizing,
        k_sl=features.label.k_sl,
        sl_min=features.label.sl_min,
        sl_max=features.label.sl_max,
        H_label=features.label.H_label,
        initial_equity=backtest_cfg.reference.initial_equity,
        trade_masks=trade_masks,
        new_entry_symbols=new_entry_aliases,
    )
    s9 = s9_compare(reference_result, event)

    # G2 统计显著性：单笔净期望的 UTC 日分块 bootstrap（08 §3 G2、06 §6）。
    g2_bootstrap: dict[str, dict[str, float]] = {}
    for label, group in (("all", None), ("train", "train"), ("holdout", "holdout")):
        group_trades = [t for t in reference_result.trades if group is None or t.group == group]
        g2_bootstrap[label] = daily_block_bootstrap_ci(
            group_trades,
            n_iters=backtest_cfg.sanity.bootstrap_iters,
            seed=backtest_cfg.sanity.bootstrap_seed,
        )

    report_dir = base.paths.artifact_root / "reports" / args.run_id
    s8 = s8_status(args.s8_result or (report_dir / "s8_replay.json"), args.run_id)
    # S10 新进入组（06 §7 S10）：笔数 < 50 只报告，否则 base 情景 PF ≥ 1.0。
    ne_trades = [t for t in reference_result.trades if t.new_entry]
    if len(ne_trades) < 50:
        s10 = f"report_only_{len(ne_trades)}笔"
    else:
        ne_pf = compute_metrics(
            reference_result, backtest_cfg.reference.initial_equity, group="new_entry"
        )["profit_factor"]
        s10 = "passed" if ne_pf >= 1.0 else "failed"

    config_hash = compute_experiment_hash(
        {
            "base": base.model_dump(mode="json"),
            "backtest": backtest_cfg.model_dump(mode="json"),
            "exits": exit_cfg.model_dump(mode="json"),
            "sizing": sizing_cfg.model_dump(mode="json"),
            "features": features.model_dump(mode="json"),
            "train": train_cfg.model_dump(mode="json"),
            "universe": universe_cfg.model_dump(mode="json"),
            "run_window": {
                "final": args.final,
                "start_ms": wb.start_ms,
                "end_ms": run_end_ms,
            },
        },
        {
            "coins_snapshot": base.paths.data_root / "universe",
            "exchange_rules_snapshot": args.rules,
            "signals": args.signals,
        },
    )

    # S11 试验次数登记（06 §7 S11）：先写入本次运行的日频夏普/skew/kurtosis/n_obs，
    # 再读取 EXPERIMENTS.log 中测试段上全部配置的夏普，计算 Deflated Sharpe 概率。
    # 周期约定：全部使用非年化日频夏普（与 n_obs = 日数同周期），并按 run_id 去重。
    reference_returns = daily_returns(
        reference_result.equity_curve, backtest_cfg.reference.initial_equity
    )
    reference_sharpe = sharpe_daily(reference_returns)
    reference_skew = skew(reference_returns)
    reference_kurtosis = kurtosis(reference_returns)
    reference_n_obs = len(reference_returns)
    log_path = experiments_log_path(base.paths.artifact_root)
    append_experiments_log(
        log_path,
        args.run_id,
        config_hash,
        {
            **reference_metrics,
            "sharpe": reference_sharpe,
            "skew": reference_skew,
            "kurtosis": reference_kurtosis,
            "n_obs": float(reference_n_obs),
        },
        kind="test" if args.walk_forward_summary is not None else "smoke",
    )
    trial_sharpes = _distinct_trial_sharpes(read_experiments_log(log_path))
    s11 = s11_deflated_sharpe(
        trial_sharpes, reference_sharpe, reference_skew, reference_kurtosis, reference_n_obs
    )
    if reference_n_obs <= 1 or not trial_sharpes:
        s11_status = "not_run"
    else:
        flag = "passed" if s11["dsr"] >= S11_DSR_THRESHOLD else "failed"
        s11_status = f"{flag}（N={int(s11['n_trials'])}，DSR={s11['dsr']:.3f}）"

    sanity = run_sanity_checks(
        s1=s1_status or ("passed" if s1["passed"] else "failed"),
        s2=s2_status,
        s3=s3_status,
        s4=s4,
        s5=s5,
        s6=s6,
        s9=s9,
        s8=s8,
        s10=s10,
        s11=s11_status,
    )
    write_report(
        report_dir,
        args.run_id,
        source="real",
        run_results=run_results,
        reference_metrics=reference_metrics,
        sanity_results=sanity,
        initial_equity=backtest_cfg.reference.initial_equity,
        g2_bootstrap=g2_bootstrap,
        s11=s11,
        sizing_sweep_frame=sweep_frame,
        signals_kind=signals_kind,
        model_id=model_id,
        g2_user=g2_user,
        fold_summary=fold_summary,
        notes=[
            _tick_relative_note(prepared, rules),
            _segment_tail_note(args.dataset_manifest),
        ],
    )
    if args.final:
        final_log = base.paths.artifact_root / "reports" / "FINAL_RUNS.log"
        final_log.parent.mkdir(parents=True, exist_ok=True)
        with final_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"ts": datetime.now(UTC).isoformat(), "run_id": args.run_id},
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"报告目录：{report_dir}")
    return 0


if __name__ == "__main__":
    main()
