"""全量回测（流式，低内存）：逐币预计算 → 精简数组落盘 memmap → 组合模拟。

规格：docs/06-回测引擎.md §3.4、§8；逐笔预计算与组合模拟全部复用
`trade.backtest.engine` 的同一份实现（红线 4），本脚本只做"按币进出"的编排。

与 `scripts/backtest.py` 的差别（已知、且在报告中标注）：
- 逐笔预计算按币进行并立即释放，组合层只保留 `SlimSymbol`（open_time/close/p_1s/资金费），
  因此可在 3.6 GB 内存的服务器上跑全量 237 币；
- `breakeven` 情景、`sizing_sweep`、S1 随机入场、S4 阈值扰动、S8 逐笔重放、S9 事件对拍
  未运行（它们需要重复预计算或完整 bars），报告中标记为 not_run。

用法（仓库根目录）：
    python scripts/backtest_stream.py --signals artifacts/signals/r0_full_test.parquet \\
        --rules artifacts/exchange_info/rules.json --run-id r0_full_test
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    PRECOMPUTE_BAR_COLUMNS,
    BacktestConfig,
    PortfolioResult,
    SlimSymbol,
    load_trade_rules,
    precompute_trades,
    prepare_symbols,
    simulate_portfolio,
    slim_from_prepared,
)
from trade.backtest.metrics import compute_metrics, monthly_return_stats
from trade.backtest.report import trades_to_frame
from trade.common.config import BaseConfig, load_config
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
from trade.features import FeaturesConfig


def build_parser() -> argparse.ArgumentParser:
    """命令行参数（缺省值指向仓库内的配置与快照）。"""
    parser = argparse.ArgumentParser(description="全量回测（流式）")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--limit-coins", type=int, default=None)
    parser.add_argument(
        "--months",
        type=str,
        default=None,
        help="评估月份 START:END（如 2025-01:2025-12）；缺省=测试段 M13~M22",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/cache/bt_slim"))
    return parser


def _slim_cache_write(root: Path, payload: SlimSymbol) -> SlimSymbol:
    """把精简数组写到磁盘并返回 memmap 版本（RSS 只随实际访问的页增长）。"""
    root.mkdir(parents=True, exist_ok=True)
    stem = root / payload.symbol
    np.save(f"{stem}.open_time.npy", payload.open_time)
    np.save(f"{stem}.close.npy", payload.close)
    np.save(f"{stem}.p_1s.npy", payload.p_1s)
    return SlimSymbol(
        symbol=payload.symbol,
        open_time=np.load(f"{stem}.open_time.npy", mmap_mode="r"),
        close=np.load(f"{stem}.close.npy", mmap_mode="r"),
        p_1s=np.load(f"{stem}.p_1s.npy", mmap_mode="r"),
        funding_time=payload.funding_time,
        funding_rate=payload.funding_rate,
    )


def _precompute_streaming(
    *,
    coins: list,
    signals: pd.DataFrame,
    rules: dict[str, object],
    groups: dict[str, str],
    data_root: Path,
    start_ms: int,
    end_ms: int,
    holdout_start_ms: int,
    membership: dict[str, frozenset[str]],
    delay_days: int,
    scen_names: list[str],
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    features: FeaturesConfig,
    new_entry_aliases: set[str],
    cache_root: Path,
    slim_start_ms: int,
    slim_end_ms: int,
    p_floor: float,
) -> tuple[dict[str, SlimSymbol], dict[str, list], dict[str, list]]:
    """逐币加载 → 预计算（每个成本情景一次）→ 压缩为 `SlimSymbol`（06 §3.4）。

    Returns:
        (alias -> SlimSymbol, 情景 -> 逐笔结果, 情景 -> 放弃信号)。
    """
    codes, alias_order = pd.factorize(signals["symbol"].astype(str))
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    open_time_all = signals["open_time"].to_numpy(dtype="int64")[order]
    p_all = signals["p"].to_numpy(dtype="float64")[order]
    theta_all = signals["theta"].to_numpy(dtype="float64")[order]
    m_all = (
        signals["m"].to_numpy(dtype="float64")[order]
        if "m" in signals.columns
        else np.zeros(open_time_all.shape[0], dtype="float64")
    )
    bounds = np.searchsorted(sorted_codes, np.arange(len(alias_order) + 1))
    range_of: dict[str, tuple[int, int]] = {
        str(alias_order[index]): (int(bounds[index]), int(bounds[index + 1]))
        for index in range(len(alias_order))
    }
    del signals
    slim: dict[str, SlimSymbol] = {}
    trades: dict[str, list] = {name: [] for name in scen_names}
    skipped: dict[str, list] = {name: [] for name in scen_names}
    total = len(coins)
    for index, coin in enumerate(coins, start=1):
        alias = coin.alias
        span = range_of.get(alias)
        if span is None or span[1] <= span[0]:
            continue
        lo, hi = span
        frame = pd.DataFrame(
            {
                "symbol": alias,
                "open_time": open_time_all[lo:hi],
                "p": p_all[lo:hi],
                "m": m_all[lo:hi],
                "theta": theta_all[lo:hi],
            }
        )
        bars = load_bars(
            data_root,
            alias,
            start_ms,
            end_ms,
            holdout_start_ms=holdout_start_ms,
            allow_holdout=False,
            columns=PRECOMPUTE_BAR_COLUMNS,
        )
        times = bars["open_time"].to_numpy(dtype="int64")
        trade_mask = universe_trade_mask(times, coin.symbol, membership, delay_days)
        prepared = prepare_symbols({alias: bars})[alias]
        del bars, times
        for name in scen_names:
            scen_trades, scen_skipped = precompute_trades(
                prepared,
                frame,
                symbol=alias,
                group=groups[alias],
                rule=rules[alias],  # type: ignore[arg-type]
                scen=scenario(name),
                fees=fees,
                backtest_cfg=backtest_cfg,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing_cfg,
                k_sl=features.label.k_sl,
                sl_min=features.label.sl_min,
                sl_max=features.label.sl_max,
                H_label=features.label.H_label,
                trade_mask=trade_mask,
                new_entry=alias in new_entry_aliases,
                p_min=p_floor,
            )
            trades[name].extend(scen_trades)
            skipped[name].extend(scen_skipped)
        slim[alias] = _slim_cache_write(
            cache_root, slim_from_prepared(prepared, start_ms=slim_start_ms, end_ms=slim_end_ms)
        )
        del prepared, trade_mask
        if index % 20 == 0 or index == total:
            gc.collect()
            print(f"[stream] 预计算 {index}/{total} 币完成", flush=True)
    return slim, trades, skipped


def _theta_floor(signals: pd.DataFrame) -> float:
    """逐笔预计算的下限：所有行 θ 的最小值（低于它的候选不会被任何行选中）。"""
    return float(np.min(signals["theta"].to_numpy(dtype="float64")))


def _filter_by_row_theta(cached: tuple[list, list]) -> tuple[list, list]:
    """按每行 θ 过滤候选（与 `engine._filter_by_threshold` 同一语义，06 §3.4）。"""
    trades = [trade for trade in cached[0] if trade.p >= trade.theta]
    skipped = [item for item in cached[1] if item.p >= item.theta]
    return trades, skipped


def _run_scenarios(
    *,
    slim: dict[str, SlimSymbol],
    trades: dict[str, list],
    skipped: dict[str, list],
    runs: dict[str, tuple[SizingConfig, float]],
    scen_names: list[str],
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
) -> dict[str, dict[str, PortfolioResult]]:
    """按 (运行, 情景) 复用同一份 `simulate_portfolio`（06 §3.4 第 2 步）。"""
    results: dict[str, dict[str, PortfolioResult]] = {}
    for run_name, (sizing, equity) in runs.items():
        results[run_name] = {}
        for name in scen_names:
            filt_trades, filt_skipped = _filter_by_row_theta((trades[name], skipped[name]))
            results[run_name][name] = simulate_portfolio(
                filt_trades,
                filt_skipped,
                sizing_cfg=sizing,
                backtest_cfg=backtest_cfg,
                fees=fees,
                initial_equity=equity,
                bars_by_symbol=slim,
                entry_maker=scenario(name).entry_maker or backtest_cfg.entry_mode == "maker",
            )
    return results


def _write_outputs(
    *,
    out_dir: Path,
    run_id: str,
    results: dict[str, dict[str, PortfolioResult]],
    metrics: dict[str, dict[str, dict[str, float]]],
    user_stats: dict[str, object],
    n_coins: int,
    kind: str,
) -> None:
    """写出 summary.md / summary.json / trades.parquet / equity.parquet。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    all_trades = []
    equities = []
    for run_name, per_scen in results.items():
        for scen_name, result in per_scen.items():
            all_trades.append(trades_to_frame(result.trades, run_name, scen_name))
            curve = result.equity_curve.copy()
            curve["run"] = run_name
            curve["scenario"] = scen_name
            equities.append(curve)
    pd.concat(all_trades, ignore_index=True).to_parquet(out_dir / "trades.parquet", index=False)
    pd.concat(equities, ignore_index=True).to_parquet(out_dir / "equity.parquet", index=False)
    payload = {
        "run_id": run_id,
        "signals_kind": kind,
        "n_coins": n_coins,
        "mode": "streaming",
        "not_run": [
            "breakeven 情景",
            "sizing_sweep",
            "S1 随机入场",
            "S4 阈值扰动",
            "S8 逐笔重放",
            "S9 事件对拍",
        ],
        "metrics": metrics,
        "g2_user": user_stats,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# 全量回测（流式）{run_id}",
        "",
        f"- 币数：**{n_coins}**；信号来源：**{kind}**；数据来源：**real**",
        "- 逐笔预计算与组合模拟复用 `trade.backtest.engine` 的同一份实现（红线 4）",
        "- 未运行（流式模式，见 summary.json 的 not_run）：breakeven、sizing_sweep、S1、S4、S8、S9",
        "",
        "## 各运行 / 情景核心指标",
        "",
        "| 运行 | 情景 | 净收益 USDT | 收益率 | PF | 笔数 | 胜率 | 最大回撤 | 单笔期望 bp |",
        "| :-- | :-- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for run_name, per_scen in metrics.items():
        for scen_name, item in per_scen.items():
            lines.append(
                f"| {run_name} | {scen_name} | {item['net_return_usdt']:.4f} | "
                f"{item['net_return_pct'] * 100:.2f}% | {item['profit_factor']:.3f} | "
                f"{int(item['n_trades'])} | {item['win_rate'] * 100:.1f}% | "
                f"{item['max_drawdown'] * 100:.2f}% | {item.get('avg_pnl_bp', float('nan')):.2f} |"
            )
    lines.extend(
        [
            "",
            "## G2-U（user 运行，08 §3）",
            "",
            f"- 月收益中位数：{float(user_stats['median_monthly_return']) * 100:.2f}%"
            if user_stats.get("median_monthly_return") is not None
            else "- 月收益中位数：n/a",
            f"- 最差月：{float(user_stats['worst_monthly_return']) * 100:.2f}%"
            if user_stats.get("worst_monthly_return") is not None
            else "- 最差月：n/a",
            f"- 亏损月数：{user_stats.get('losing_months')}",
            f"- 最大回撤：{float(user_stats['max_drawdown']) * 100:.2f}%",
            f"- stress 净收益：{float(user_stats['stress_net_return_usdt']):.4f} USDT",
            f"- 判定：{'通过' if user_stats.get('passed') else '不通过'}",
        ]
    )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    """入口：流式全量回测（06 §3.4、§8）。"""
    args = build_parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]

    def resolve(value: Path) -> Path:
        """相对路径按仓库根解析。"""
        return value if value.is_absolute() else root / value

    base = load_config(BaseConfig, resolve(args.base))
    backtest_cfg = load_config(BacktestConfig, resolve(args.backtest))
    exit_cfg = load_config(ExitConfig, resolve(args.exits))
    sizing_cfg = load_config(SizingConfig, resolve(args.sizing))
    features = load_config(FeaturesConfig, resolve(args.features))
    universe_cfg = load_universe_config(resolve(args.universe))

    wb = window_bounds(base)
    months = universe_months(base)
    dev_months = months[: len(months) - base.data_window.holdout_months]
    test_months = months[
        base.data_window.tuning_months : len(months) - base.data_window.holdout_months
    ]
    eval_months = test_months
    if args.months is not None:
        start, _, end = args.months.partition(":")
        end = end or start
        eval_months = [month for month in months if start <= month <= end]
        if not eval_months:
            raise ValueError(f"--months {args.months} 不在数据窗口内（红线 13）")
    coins = collect_universe_coins(base.paths.data_root, dev_months)
    if args.limit_coins is not None:
        first_key = months[0].replace("-", "")
        coins = [
            coin
            for coin in coins
            if (base.paths.data_root / "bars" / coin.alias / f"{first_key}.parquet").is_file()
        ][: args.limit_coins]
    membership = load_universe_membership(base.paths.data_root, dev_months)
    rules = load_trade_rules(args.rules, base.paths.data_root / "universe" / "aliases.json")
    groups = {coin.alias: coin.role for coin in coins}

    signals = pd.read_parquet(args.signals)
    signals = signals[signals["symbol"].isin(set(groups))].copy()
    if "theta" not in signals.columns:
        signals = signals.assign(theta=float(backtest_cfg.threshold))
    test_start_ms = int(pd.Timestamp(eval_months[0] + "-01", tz="UTC").timestamp() * 1000)
    test_end_ms = int(
        (pd.Timestamp(eval_months[-1] + "-01", tz="UTC") + pd.offsets.MonthBegin(1)).timestamp()
        * 1000
    )
    in_test = (signals["open_time"] >= test_start_ms) & (signals["open_time"] < test_end_ms)
    signals = signals[in_test].reset_index(drop=True)
    if signals.empty:
        raise ValueError(f"信号在评估段 {eval_months[0]}~{eval_months[-1]} 内为空（红线 13）")
    print(f"[stream] 币数={len(coins)} 评估段信号={len(signals)}", flush=True)

    fees = FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate)
    scen_names = [name for name in backtest_cfg.scenarios if name != "breakeven"]
    ne_symbols = new_entry_symbols(membership, base.data_window.tuning_months)
    new_entry_aliases = {coin.alias for coin in coins if coin.symbol in ne_symbols}
    cache_root = resolve(args.cache_dir) / args.run_id

    slim, trades, skipped = _precompute_streaming(
        coins=coins,
        signals=signals,
        rules=rules,
        groups=groups,
        data_root=base.paths.data_root,
        start_ms=wb.start_ms,
        end_ms=wb.holdout_start_ms,
        holdout_start_ms=wb.holdout_start_ms,
        membership=membership,
        delay_days=universe_cfg.rule.new_entry_delay_days,
        scen_names=scen_names,
        fees=fees,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        features=features,
        new_entry_aliases=new_entry_aliases,
        cache_root=cache_root,
        slim_start_ms=test_start_ms - 86_400_000,
        slim_end_ms=test_end_ms,
        p_floor=_theta_floor(signals),
    )
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
    results = _run_scenarios(
        slim=slim,
        trades=trades,
        skipped=skipped,
        runs=runs,
        scen_names=scen_names,
        fees=fees,
        backtest_cfg=backtest_cfg,
    )
    metrics: dict[str, dict[str, dict[str, float]]] = {}
    for run_name, per_scen in results.items():
        metrics[run_name] = {}
        equity = runs[run_name][1]
        for scen_name, result in per_scen.items():
            metrics[run_name][scen_name] = compute_metrics(result, equity)
    user_base = results["user"][scen_names[0]]
    stats = monthly_return_stats(user_base.equity_curve, sizing_cfg.initial_equity)
    max_dd = (
        float(user_base.equity_curve["drawdown"].max()) if not user_base.equity_curve.empty else 0.0
    )
    stress = results["user"].get("stress")
    stress_net = float(sum(t.net_pnl for t in stress.trades)) if stress is not None else 0.0
    user_stats = {
        **stats,
        "max_drawdown": max_dd,
        "stress_net_return_usdt": stress_net,
        "passed": bool(
            stats["median_monthly_return"] > 0.0
            and max_dd <= 0.20
            and stats["losing_months"] <= 4
            and stats["worst_monthly_return"] >= -0.12
            and stress_net > 0.0
        ),
    }
    _write_outputs(
        out_dir=root / "artifacts" / "reports" / args.run_id,
        run_id=args.run_id,
        results=results,
        metrics=metrics,
        user_stats=user_stats,
        n_coins=len(slim),
        kind="model"
        if ("fold_id" in signals.columns and (signals["fold_id"].astype(str) != "R0").any())
        else "r0",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
