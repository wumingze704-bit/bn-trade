"""S8 逐笔成交重放（服务器运行，06 §7 S8）。

在服务器上用保留的 3 币 × 1 个测试月原始 aggTrades 重放持仓期，量化 K 线级回测相对
逐笔执行的乐观程度，并把结果写回正式回测的 `gate.json`。

用法（仓库根目录，正式回测之后执行）：

    python scripts/s8_replay.py --run-id <正式回测的 run_id> \
        --signals artifacts/signals/model.parquet --rules artifacts/exchange_info/rules.json

默认重放 2026-02（测试段 M17）的 XRPUSDT / ARBUSDT / STRKUSDT（选币规则见
`S8_SELECTION_RULE`，选定后不得更改）。只读测试段数据，不碰封存集 M23~M24。
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from trade.backtest.costs import FeeSchedule, scenario
from trade.backtest.engine import (
    BacktestConfig,
    PrecomputedTrade,
    load_trade_rules,
    precompute_trades,
)
from trade.backtest.replay import (
    BarsArrays,
    ReplayResult,
    TickArrays,
    compare_s8,
    load_day_ticks,
    replay_month,
    s8_payload,
    s8_status_text,
)
from trade.backtest.report import write_gate_s8
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import load_bars, load_universe_snapshot, window_bounds
from trade.data.universe import load_universe_config
from trade.features.registry import FeaturesConfig

# S8 重放范围：测试段 M13~M22 的第 5 个月（M17 = 2026-02），该月快照 48 币按
# daily_quote_median 降序取第 1、24、48 名各 1 币（高/中/低流动性分层各 1 个）。
# 选定后不得更改（06 §7 S8、08 §2 P3）。
S8_MONTH = "2026-02"
S8_SYMBOLS = ("XRPUSDT", "ARBUSDT", "STRKUSDT")
S8_SELECTION_RULE = (
    "测试段 M13~M22 的第 5 个月（M17 = 2026-02）；该月快照 48 币按 daily_quote_median "
    "降序取第 1、24、48 名各 1 币（XRPUSDT / ARBUSDT / STRKUSDT），选定后不得更改"
)

_MS_PER_DAY = 86_400_000
# 入场 K 线之后多读 1 天 bars，覆盖 H_max（120 根）可能跨过的月末。
_BARS_TAIL_DAYS = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S8 逐笔成交重放")
    parser.add_argument("--run-id", type=str, required=True, help="正式回测的 run_id")
    parser.add_argument(
        "--signals", type=Path, required=True, help="正式回测使用的 signals.parquet"
    )
    parser.add_argument("--rules", type=Path, required=True, help="exchangeInfo 规则快照 JSON")
    parser.add_argument("--month", type=str, default=S8_MONTH, help="重放月份 YYYY-MM")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(S8_SYMBOLS),
        help="逗号分隔的币种（必须在当月 U(R) 快照内）",
    )
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--skip-gate", action="store_true", help="只写 s8_replay.json，不改 gate.json"
    )
    return parser


def _month_bounds(month: str) -> tuple[int, int]:
    """返回 UTC 自然月的 `[start_ms, end_ms)`。"""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _tick_loader(raw_root: Path, alias: str, symbol: str):
    """返回 `YYYY-MM-DD` → 当日逐笔的装载函数（缺少归档即报错，红线 8）。"""

    def load(day: str) -> TickArrays:
        path = raw_root / "aggtrades" / alias / f"{symbol}-aggTrades-{day}.zip"
        if not path.is_file():
            raise FileNotFoundError(f"缺少逐笔归档：{path}（03 §2.2、06 §7 S8）")
        return load_day_ticks(path)

    return load


def _trade_rows(pairs: list[tuple[PrecomputedTrade, ReplayResult]]) -> list[dict[str, object]]:
    """把 `(K 线级预计算, 逐笔重放结果)` 展开为明细行（06 §8）。"""
    return [
        {
            "symbol": replay.symbol,
            "entry_time": replay.entry_open_time,
            "bar_exit_time": trade.exit_open_time,
            "tick_exit_time": replay.exit_time_ms,
            "bar_exit_type": trade.exit_type,
            "tick_exit_type": replay.exit_type,
            "bar_exit_price": trade.exit_price,
            "tick_exit_price": replay.exit_price,
            "old_stop_window_hit": replay.old_stop_window_hit,
            "entry_price_check_rel": replay.entry_price_check_rel,
        }
        for trade, replay in pairs
    ]


def main() -> int:
    """s8_replay.py 入口。"""
    args = build_parser().parse_args()
    base = load_config(BaseConfig, args.base)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    features = load_config(FeaturesConfig, args.features)
    load_universe_config(args.universe)  # 只校验配置可加载；S8 不重新选币
    data_root = base.paths.data_root
    report_dir = args.report_dir or base.paths.artifact_root / "reports" / args.run_id
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    month_start_ms, month_end_ms = _month_bounds(args.month)
    wb = window_bounds(base)
    if month_end_ms > wb.holdout_start_ms:
        raise ValueError(f"S8 只允许测试段月份，{args.month} 落在封存集（AGENTS.md §6 红线 2）")

    snapshot = load_universe_snapshot(data_root, args.month)
    role_by_symbol = {coin.symbol: coin.role for coin in snapshot.coins}
    alias_by_symbol = {coin.symbol: coin.alias for coin in snapshot.coins}
    outside = [symbol for symbol in symbols if symbol not in role_by_symbol]
    if outside:
        raise ValueError(f"S8 币种不在 {args.month} 的 U(R) 快照内：{outside}（03 §4.2）")

    rules = load_trade_rules(args.rules, data_root / "universe" / "aliases.json")
    signals = pd.read_parquet(args.signals)
    fees = FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate)
    scen = scenario("base")
    price_col = "p_1s" if scen.latency_ms == 1000 else "p_3s"

    # bars 从数据窗口起点读起（与正式回测一致），保证 ATR14 等递归指标与回测逐值相同。
    bars_start_ms = wb.start_ms
    bars_end_ms = month_end_ms + _BARS_TAIL_DAYS * _MS_PER_DAY
    all_pairs: list[tuple[PrecomputedTrade, ReplayResult]] = []
    for symbol in symbols:
        alias = alias_by_symbol[symbol]
        bars = load_bars(
            data_root,
            alias,
            bars_start_ms,
            bars_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        # 正式回测与 R0 的 signals.parquet 用 alias 作 symbol 键（03 §4.5），这里同时接受
        # 真实 symbol，避免误用其他来源的信号文件。
        sym_signals = signals[
            signals["symbol"].isin([alias, symbol])
            & (signals["open_time"] >= month_start_ms)
            & (signals["open_time"] < month_end_ms)
        ]
        if sym_signals.empty:
            raise ValueError(f"{symbol} 在 {args.month} 没有信号（疑似月份或信号文件不对）")
        trades, _skipped = precompute_trades(
            bars,
            sym_signals,
            # S8 报告用真实 symbol（正式回测用 alias 作键），便于与 03 §2.2 的选币说明对照。
            symbol=symbol,
            group=role_by_symbol[symbol],
            rule=rules[alias],
            scen=scen,
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=sizing_cfg,
            k_sl=features.label.k_sl,
            sl_min=features.label.sl_min,
            sl_max=features.label.sl_max,
            H_label=features.label.H_label,
        )
        # 只统计入场与 K 线级出场都在该月内的交易：跨月交易的逐笔落在次月归档，
        # 而 S8 的原始逐笔只保留到该月最后一天（03 §2.2）。两侧同时排除，不影响比较。
        month_trades = [
            trade
            for trade in trades
            if month_start_ms <= trade.entry_open_time < month_end_ms
            and trade.exit_open_time < month_end_ms
        ]
        pairs = replay_month(
            BarsArrays.from_frame(bars, price_col),
            month_trades,
            tick_loader=_tick_loader(args.raw_root, alias, symbol),
            exit_cfg=exit_cfg,
            fees=fees,
            scen=scen,
            entry_delay_ms=scen.latency_ms,
        )
        print(f"{symbol}（{alias}）：候选 {len(month_trades)} 笔，完成逐笔重放 {len(pairs)} 笔")
        all_pairs.extend(pairs)

    if not all_pairs:
        raise ValueError(f"S8 没有可重放的交易（{args.month}，{symbols}）")
    summary = compare_s8(args.month, all_pairs, entry_fee_rate=fees.taker)
    payload = s8_payload(summary, run_id=args.run_id, selection_rule=S8_SELECTION_RULE)

    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "s8_replay.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    pd.DataFrame(_trade_rows(all_pairs)).to_parquet(report_dir / "s8_replay.parquet", index=False)
    gate_path = report_dir / "gate.json"
    if not args.skip_gate:
        if gate_path.is_file():
            write_gate_s8(gate_path, payload)
        else:
            print(f"gate.json 不存在（{gate_path}），S8 结果只写入 s8_replay.json")

    print(f"S8：{s8_status_text(summary)}")
    print(f"K 线级净收益 {summary.bar_net_usdt:.4f} USDT，逐笔级 {summary.tick_net_usdt:.4f} USDT")
    print(f"旧止损窗口内触发出场 {summary.old_stop_window_hits} 笔")
    print(f"结果目录：{report_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
