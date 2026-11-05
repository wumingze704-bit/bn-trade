"""T4-3 第 1 步：出场与止损参数搜索（用 M0 LightGBM 评估）。

规格：docs/05-模型与训练.md §6.1（先定出场与止损，再调模型）。只在调参期
（训练 M1~M9、验证 M10~M12）上搜索，6 组出场/止损参数各重建 `exit_sim` 标签、
训练 M0、在验证段选阈值、算 `base` 情景净收益与 PF，取 PF 最高且净收益 > 0 的一组。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import BacktestConfig, TradeRule, precompute_trades, simulate_portfolio
from trade.backtest.metrics import compute_metrics
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    month_index,
    rebalance_ms_of,
    segment_ids,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import load_universe_config
from trade.features import FeaturesConfig, compute_features, enabled_features
from trade.labels.exit_sim import exit_sim
from trade.labels.prefilter import is_sample_mask
from trade.labels.triple_barrier import uniqueness_weights
from trade.models.lightgbm import tabular_features_matrix, train_lgbm

# 6 组出场/止损参数（05 §6.1 第 1 步）。H_max/k_trail 固定 120/1.5，sl_max 固定 2.5%，
# 在 k_sl 2.5/3.0 与 sl_min 1.0%~1.8% 上各取 3 组。
_EXIT_GRID: list[dict[str, float]] = [
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.010, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.012, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.014, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.012, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.015, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.018, "sl_max": 0.025},
]

# 阈值网格（05 §7.2：0.30~0.80，步长 0.02）
_THETA_GRID = [round(0.30 + 0.02 * i, 2) for i in range(26)]

# 训练样本上限：超过则按固定种子下采样，控制内存（可复现；LightGBM 直方图对样本数不敏感）。
_MAX_SAMPLES = 2_000_000


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_rules_by_alias(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读 rules.json + aliases.json，构造 alias -> TradeRule（与回测 key 一致）。"""
    rules_raw = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    result: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = rules_raw.get(symbol)
        if rule is not None:
            result[alias] = TradeRule(
                tick_size=float(rule["tick_size"]),
                step_size=float(rule["step_size"]),
                min_notional=float(rule.get("min_notional", 5.0)),
            )
    return result


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="T4-3 第 1 步：出场/止损搜索")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--exits", default="configs/exits.yaml")
    parser.add_argument("--sizing", default="configs/sizing.yaml")
    parser.add_argument("--backtest", default="configs/backtest.yaml")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个币（冒烟用）")
    parser.add_argument("--groups", type=int, default=6, help="跑前 N 组出场参数")
    args = parser.parse_args(argv)

    base_cfg = load_config(BaseConfig, _resolve(root, args.base))
    universe_cfg = load_universe_config(_resolve(root, args.universe))
    features_cfg = load_config(FeaturesConfig, _resolve(root, args.features))
    exits_cfg = load_config(ExitConfig, _resolve(root, args.exits))
    sizing_cfg = load_config(SizingConfig, _resolve(root, args.sizing))
    backtest_cfg = load_config(BacktestConfig, _resolve(root, args.backtest))

    data_root = base_cfg.paths.data_root
    months = universe_months(base_cfg)
    tuning_months = base_cfg.data_window.tuning_months
    tuning_months_list = months[:tuning_months]  # M1~M12
    tuning_end_ms = rebalance_ms_of(months[tuning_months])  # M13 起点，调参期右边界

    coins = collect_universe_coins(data_root, tuning_months_list)
    if args.limit is not None:
        coins = coins[: args.limit]

    wb = window_bounds(base_cfg)
    membership = load_universe_membership(data_root, months)
    delay = universe_cfg.rule.new_entry_delay_days
    btc_bars = load_bars(
        data_root,
        universe_cfg.btc_context,
        wb.start_ms,
        tuning_end_ms,
        holdout_start_ms=wb.holdout_start_ms,
        allow_holdout=False,
    )
    features = enabled_features(features_cfg)
    feature_names = [f.name for f in features]

    # —— 数据准备（features 与样本位置只算一次，不依赖出场参数）——
    per_coin: dict[str, dict] = {}
    bars_by_alias: dict[str, pd.DataFrame] = {}
    for coin in coins:
        alias = coin.alias
        bars = load_bars(
            data_root,
            alias,
            wb.start_ms,
            tuning_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        bars_by_alias[alias] = bars
        seg = segment_ids(bars["open_time"].to_numpy(dtype="int64"))
        feat_df = compute_features(bars, btc_bars, features, features_cfg, seg)
        sample = is_sample_mask(
            bars,
            features_cfg.prefilter.vol_z_60,
            features_cfg.prefilter.r1_sigma,
            features_cfg.prefilter.brk_60,
            features_cfg.warmup_bars,
            seg,
        )
        trade_mask = universe_trade_mask(
            bars["open_time"].to_numpy(dtype="int64"),
            coin.symbol,
            membership,
            delay,
        )
        sample = sample & trade_mask
        per_coin[alias] = {
            "symbol": coin.symbol,
            "role": coin.role,
            "bars": bars,
            "segments": seg,
            "x": feat_df[feature_names].to_numpy(dtype="float16"),
            "open_time": bars["open_time"].to_numpy(dtype="int64"),
            "sample_positions": np.nonzero(sample)[0],
        }

    notional_ref = sizing_cfg.initial_equity * sizing_cfg.position_frac * float(sizing_cfg.leverage)
    rules = _load_rules_by_alias(
        base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
        data_root / "universe" / "aliases.json",
    )
    groups = {coin.alias: coin.role for coin in coins}
    fees = FeeSchedule(base_cfg.fees.maker, base_cfg.fees.taker)

    results: list[dict] = []
    for gi in range(args.groups):
        grid = _EXIT_GRID[gi]
        exit_cfg = exits_cfg.model_copy(
            update={"H_max": int(grid["H_max"]), "k_trail": grid["k_trail"]}
        )
        k_sl = grid["k_sl"]
        sl_min = grid["sl_min"]
        sl_max = grid["sl_max"]

        # 重建 exit_sim 标签（调参期），并组装 M0 特征
        x_parts, y_parts, w_parts, m_parts, ot_parts, sym_parts = [], [], [], [], [], []
        for alias, info in per_coin.items():
            labels_df, kept = exit_sim(
                info["bars"],
                info["sample_positions"],
                k_sl=k_sl,
                sl_min=sl_min,
                sl_max=sl_max,
                H_label=features_cfg.label.H_label,
                entry_latency_ms=features_cfg.label.entry_latency_ms,
                y_min_r=features_cfg.label.y_min_r,
                exit_cfg=exit_cfg,
                fees_taker=base_cfg.fees.taker,
                fees_maker=base_cfg.fees.maker,
                half_spread_min=backtest_cfg.costs.half_spread_min,
                slip_min=backtest_cfg.costs.slip_min,
                Y=backtest_cfg.costs.Y,
                notional_ref=notional_ref,
                max_chase=backtest_cfg.risk.max_chase,
                tick_size=rules[alias].tick_size,
                segments=info["segments"],
            )
            n = kept.size
            if n == 0:
                continue
            weights = uniqueness_weights(kept, labels_df["bars_held"].to_numpy(dtype="int64"))
            row_idx = kept.astype("int64")
            x_parts.append(tabular_features_matrix(info["x"], row_idx))
            y_parts.append(labels_df["y"].to_numpy(dtype="int64"))
            w_parts.append(weights)
            ot = labels_df["open_time"].to_numpy(dtype="int64")
            ot_parts.append(ot)
            m_parts.append(
                np.array(
                    [
                        month_index(int(t), base_cfg.data_window.end_month, len(months)) or 0
                        for t in ot
                    ],
                    dtype="int64",
                )
            )
            sym_parts.append(np.full(n, alias, dtype=object))

        if not x_parts:
            print(f"组 {gi + 1}：无样本", flush=True)
            continue

        # 内存保护：样本过多时先按固定种子下采样，再拼接（避免峰值内存超限）。
        n_total = sum(len(p) for p in y_parts)
        rng = np.random.default_rng(42)
        keep = (
            np.sort(rng.choice(n_total, _MAX_SAMPLES, replace=False))
            if n_total > _MAX_SAMPLES
            else None
        )
        xl, yl, wl, ml, ol, sl2 = [], [], [], [], [], []
        offset = 0
        for xp, yp, wp, mp, otp, sp in zip(
            x_parts, y_parts, w_parts, m_parts, ot_parts, sym_parts, strict=True
        ):
            n = len(yp)
            local = (
                np.arange(n)
                if keep is None
                else keep[(keep >= offset) & (keep < offset + n)] - offset
            )
            xl.append(xp[local])
            yl.append(yp[local])
            wl.append(wp[local])
            ml.append(mp[local])
            ol.append(otp[local])
            sl2.append(sp[local])
            offset += n

        x_all = np.concatenate(xl)
        y_all = np.concatenate(yl)
        w_all = np.concatenate(wl)
        m_all = np.concatenate(ml)
        ot_all = np.concatenate(ol)
        sym_all = np.concatenate(sl2)

        train_mask = (m_all >= 1) & (m_all <= 9)
        val_mask = (m_all >= 10) & (m_all <= 12)
        if train_mask.sum() == 0 or val_mask.sum() == 0:
            print(f"组 {gi + 1}：训练/验证样本为空", flush=True)
            continue

        booster = train_lgbm(
            x_all[train_mask],
            y_all[train_mask],
            w_all[train_mask],
            x_all[val_mask],
            y_all[val_mask],
            w_all[val_mask],
        )
        p_val = booster.predict(x_all[val_mask], num_iteration=booster.best_iteration)

        val_signals = pd.DataFrame(
            {
                "symbol": sym_all[val_mask],
                "open_time": ot_all[val_mask],
                "p": p_val,
                "m": np.zeros(int(val_mask.sum())),
                "is_sample": np.ones(int(val_mask.sum()), dtype="int64"),
            }
        )
        # 逐笔预计算一次（最低阈值 0.30），各 θ 只过滤 + 组合模拟，避免重复 precompute。
        cfg_min = backtest_cfg.model_copy(update={"threshold": _THETA_GRID[0]})
        scen = scenario("base")
        all_trades = []
        all_skipped = []
        for alias, bars in bars_by_alias.items():
            sym_sig = val_signals[val_signals["symbol"] == alias]
            if sym_sig.empty:
                continue
            trades, skipped = precompute_trades(
                bars,
                sym_sig,
                symbol=alias,
                group=groups[alias],
                rule=rules[alias],
                scen=scen,
                fees=fees,
                backtest_cfg=cfg_min,
                exit_cfg=exit_cfg,
                sizing_cfg=sizing_cfg,
                k_sl=k_sl,
                sl_min=sl_min,
                sl_max=sl_max,
                H_label=features_cfg.label.H_label,
            )
            all_trades.extend(trades)
            all_skipped.extend(skipped)

        best_theta, best_pf, best_net = 0.5, 0.0, float("-inf")
        for theta in _THETA_GRID:
            filtered = [t for t in all_trades if t.p >= theta]
            result = simulate_portfolio(
                filtered,
                all_skipped,
                sizing_cfg=sizing_cfg,
                backtest_cfg=backtest_cfg,
                fees=fees,
                initial_equity=backtest_cfg.reference.initial_equity,
                bars_by_symbol=bars_by_alias,
            )
            metrics = compute_metrics(result, backtest_cfg.reference.initial_equity)
            pf = float(metrics.get("profit_factor") or 0.0)
            net = float(metrics.get("net_return_usdt") or 0.0)
            n_trades = float(metrics.get("n_trades") or 0.0)
            # 约束：验证段（M10~M12 约 90 天）日均交易 ≥ 3 笔 → 总 ≥ 270
            if n_trades < 270:
                continue
            if pf > best_pf:
                best_theta, best_pf, best_net = theta, pf, net

        results.append(
            {
                "group": gi + 1,
                "H_max": int(grid["H_max"]),
                "k_trail": grid["k_trail"],
                "k_sl": k_sl,
                "theta": best_theta,
                "pf": best_pf,
                "net_return_usdt": best_net,
                "n_train": int(train_mask.sum()),
                "n_val": int(val_mask.sum()),
            }
        )
        print(
            f"组 {gi + 1}: H_max={int(grid['H_max'])} k_trail={grid['k_trail']} k_sl={k_sl} "
            f"→ θ={best_theta} PF={best_pf:.3f} 净收益={best_net:.2f}",
            flush=True,
        )

    print("\n==== 结果汇总 ====")
    for r in results:
        print(
            f"组 {r['group']}: H_max={r['H_max']} k_trail={r['k_trail']} k_sl={r['k_sl']} "
            f"θ={r['theta']} PF={r['pf']:.3f} 净收益={r['net_return_usdt']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
