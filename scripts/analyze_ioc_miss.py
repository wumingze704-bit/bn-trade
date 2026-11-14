"""IOC 未成交率评估（只报告，不改变 base 成本；06 §4.3）。

用法（在东京服务器，有原始逐笔与信号文件时）：

    python scripts/analyze_ioc_miss.py --signals artifacts/signals/r0.parquet \
        --raw-root data/raw --rules artifacts/exchange_info/rules.json \
        --run-id ioc_m6 --month 2025-08

对每个信号：以 `p_1s` 为读价时刻，取其后 `[lat, lat + costs.order_rtt_ms]` 内的主动买
成交价；用实盘限价公式判断是否“可能未成交”，并对比两组出场净收益（R 与 bp）。
只用调参期数据，不碰测试段与封存集（红线 2、3）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    TradeRule,
    load_trade_rules,
    precompute_trades,
)
from trade.backtest.ioc import ioc_missed, window_taker_buy_high
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import collect_universe_coins, load_bars, universe_months, window_bounds
from trade.features.registry import FeaturesConfig
from trade.live.orders import compute_limit_price


def build_parser() -> argparse.ArgumentParser:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="IOC 未成交率评估（06 §4.3，只报告）")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True, help="原始 aggTrades 根目录")
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--month", type=str, required=True, help="评估月份 YYYY-MM（调参期）")
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    return parser


def _load_ticks(raw_root: Path, symbol: str, month: str) -> pd.DataFrame:
    """读某币某月的原始 aggTrades（列：transact_time、price、is_buyer_maker）。"""
    path = raw_root / "aggtrades" / symbol / f"{month.replace('-', '')}.parquet"
    if not path.is_file():
        raise ValueError(f"缺少原始逐笔：{path}（须先在服务器下载）")
    frame = pd.read_parquet(path)
    for column in ("transact_time", "price", "is_buyer_maker"):
        if column not in frame.columns:
            raise ValueError(f"逐笔缺少列 {column}：{path}")
    return frame.sort_values("transact_time").reset_index(drop=True)


def _month_bounds(month: str) -> tuple[int, int]:
    """某 UTC 月的 [起, 止) 毫秒时间戳。"""
    start = np.datetime64(month, "M")
    end = start + np.timedelta64(1, "M")
    return (
        int(start.astype("datetime64[ms]").astype("int64")),
        int(end.astype("datetime64[ms]").astype("int64")),
    )


def _coin_rows(
    *,
    alias: str,
    symbol: str,
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    ticks: pd.DataFrame,
    rule: TradeRule,
    trades: dict[int, object],
    rtt_ms: int,
    lat_ms: int,
    max_chase: float,
) -> list[dict[str, object]]:
    """单币逐信号判断“可能未成交”并记录出场净收益（06 §4.3）。"""
    open_time = bars["open_time"].to_numpy(dtype="int64")
    p_1s_all = bars["p_1s"].to_numpy(dtype="float64")
    close = bars["close"].to_numpy(dtype="float64")
    times = ticks["transact_time"].to_numpy(dtype="int64")
    prices = ticks["price"].to_numpy(dtype="float64")
    maker = ticks["is_buyer_maker"].astype("int64").to_numpy()
    rows: list[dict[str, object]] = []
    for row in signals.itertuples(index=False):
        trade = trades.get(int(row.open_time))
        if trade is None:
            continue
        pos = int(np.searchsorted(open_time, int(row.open_time), side="left"))
        if pos + 1 >= open_time.shape[0]:
            continue
        entry_ms = int(open_time[pos + 1])
        p_1s = float(p_1s_all[pos + 1])
        c_t = float(close[pos])
        if not np.isfinite(p_1s) or p_1s <= 0.0 or not np.isfinite(c_t) or c_t <= 0.0:
            continue
        tick_rel = rule.tick_size / p_1s
        limit = compute_limit_price(
            best_ask=p_1s * (1.0 + 2.0 * tick_rel),
            tick_rel=tick_rel,
            c_t=c_t,
            max_chase=max_chase,
            tick_size=rule.tick_size,
        )
        high = window_taker_buy_high(
            timestamps_ms=times,
            prices=prices,
            is_buyer_maker=maker,
            start_ms=entry_ms + lat_ms,
            end_ms=entry_ms + lat_ms + rtt_ms,
        )
        rows.append(
            {
                "alias": alias,
                "symbol": symbol,
                "open_time": int(row.open_time),
                "p": float(row.p),
                "missed": ioc_missed(limit_price=limit, taker_buy_high=high),
                "no_taker_buy": bool(not np.isfinite(high)),
                "net_r": float(trade.gross_return) / float(trade.sl),
                "net_bp": float(trade.gross_return) * 1e4,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    """入口：逐信号判断可能未成交，输出比例与两组净收益。"""
    args = build_parser().parse_args(argv)
    base = load_config(BaseConfig, args.base)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    features = load_config(FeaturesConfig, args.features)
    months = universe_months(base)
    tuning_months = months[: base.data_window.tuning_months]
    if args.month not in tuning_months:
        raise ValueError(f"只允许调参期月份（{tuning_months}），收到 {args.month}")

    wb = window_bounds(base)
    month_start, month_end = _month_bounds(args.month)
    coins = {
        coin.alias: coin for coin in collect_universe_coins(base.paths.data_root, tuning_months)
    }
    rules = load_trade_rules(args.rules, base.paths.data_root / "universe" / "aliases.json")
    signals = pd.read_parquet(args.signals)
    fees = FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate)
    lat_ms = scenario("base").latency_ms
    rows: list[dict[str, object]] = []
    for alias, coin in sorted(coins.items()):
        sym_signals = signals[signals["symbol"] == alias]
        sym_signals = sym_signals[
            (sym_signals["open_time"] >= month_start) & (sym_signals["open_time"] < month_end)
        ]
        if sym_signals.empty or alias not in rules:
            continue
        bars = load_bars(
            base.paths.data_root,
            alias,
            month_start - 2 * 86_400_000,  # 预热 2 天，保证 ATR/mean_60(qv)/rv_15 在月初可用
            month_end,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        ticks = _load_ticks(args.raw_root, coin.symbol, args.month)
        trades, _ = precompute_trades(
            bars,
            sym_signals,
            symbol=alias,
            group="train",
            rule=rules[alias],
            scen=scenario("base"),
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=features.label.k_sl,
            sl_min=features.label.sl_min,
            sl_max=features.label.sl_max,
            H_label=features.label.H_label,
        )
        rows.extend(
            _coin_rows(
                alias=alias,
                symbol=coin.symbol,
                signals=sym_signals,
                bars=bars,
                ticks=ticks,
                rule=rules[alias],
                trades={trade.signal_open_time: trade for trade in trades},
                rtt_ms=backtest_cfg.costs.order_rtt_ms,
                lat_ms=lat_ms,
                max_chase=backtest_cfg.risk.max_chase,
            )
        )
    if not rows:
        raise ValueError("没有任何可评估的信号（检查 --signals 与 --month）")
    frame = pd.DataFrame(rows)
    missed = frame[frame["missed"]]
    filled = frame[~frame["missed"]]
    summary = {
        "run_id": args.run_id,
        "month": args.month,
        "order_rtt_ms": backtest_cfg.costs.order_rtt_ms,
        "n_signals": int(len(frame)),
        "n_missed": int(len(missed)),
        "missed_ratio": float(len(missed) / len(frame)),
        "n_no_taker_buy": int(frame["no_taker_buy"].sum()),
        "missed_net_r_mean": float(missed["net_r"].mean()) if len(missed) else None,
        "filled_net_r_mean": float(filled["net_r"].mean()) if len(filled) else None,
        "missed_net_bp_mean": float(missed["net_bp"].mean()) if len(missed) else None,
        "filled_net_bp_mean": float(filled["net_bp"].mean()) if len(filled) else None,
    }
    out_dir = base.paths.artifact_root / "reports" / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_dir / "ioc_miss.parquet", index=False)
    (out_dir / "ioc_miss.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
