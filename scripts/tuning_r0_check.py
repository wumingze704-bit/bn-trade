"""T3-9a 调参期 R0 毛期望早期检查（执行顺序第 10 条）。

在 M1~M12 上跑 R0（阈值固定），统计成本前后单笔期望与笔数，与往返成本约 15 bp 比较。
结果只用于决定是否继续投入，不得据此调参（红线 3）。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from trade.backtest.costs import FeeSchedule  # noqa: E402
from trade.backtest.engine import BacktestConfig, TradeRule, run_backtest  # noqa: E402
from trade.backtest.metrics import compute_metrics  # noqa: E402
from trade.backtest.r0 import TrainConfig, r0_signals_for_symbol  # noqa: E402
from trade.common.config import BaseConfig, load_config  # noqa: E402
from trade.common.exits import ExitConfig  # noqa: E402
from trade.common.sizing import SizingConfig  # noqa: E402
from trade.data.loader import (  # noqa: E402
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    new_entry_symbols,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import load_aliases, load_universe_config  # noqa: E402
from trade.features.registry import FeaturesConfig  # noqa: E402


def _load_rules(rules_path: Path, aliases: dict[str, str]) -> dict[str, TradeRule]:
    """把 symbol → 完整字段的 rules.json 转成 alias → TradeRule。"""
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    return {
        aliases[symbol]: TradeRule(
            tick_size=fields["tick_size"],
            step_size=fields["step_size"],
            min_notional=fields["min_notional"],
        )
        for symbol, fields in raw.items()
        if symbol in aliases
    }


def main() -> int:
    base = load_config(BaseConfig, repo_root / "configs" / "base.yaml")
    backtest_cfg = load_config(BacktestConfig, repo_root / "configs" / "backtest.yaml")
    exit_cfg = load_config(ExitConfig, repo_root / "configs" / "exits.yaml")
    sizing_cfg = load_config(SizingConfig, repo_root / "configs" / "sizing.yaml")
    features = load_config(FeaturesConfig, repo_root / "configs" / "features.yaml")
    train_cfg = load_config(TrainConfig, repo_root / "configs" / "train.yaml")
    universe_cfg = load_universe_config(repo_root / "configs" / "universe.yaml")

    months = universe_months(base)
    tuning_months = base.data_window.tuning_months
    # 只用调参期 M1~M12 的入选币（T3-9a 不碰测试段/封存集的币）
    coins = collect_universe_coins(base.paths.data_root, months[:tuning_months])
    # 早期检查抽样前 40 币（快速证伪，控制内存；全量留待 G2 正式回测）
    coins = coins[:40]
    aliases = load_aliases(repo_root / "data" / "universe" / "aliases.json")
    membership = load_universe_membership(base.paths.data_root, months)
    rules = _load_rules(repo_root / "artifacts" / "exchange_info" / "rules.json", aliases)
    groups = {coin.alias: coin.role for coin in coins}

    # 调参期范围：预热起点 ~ M13 月初（M13 = months[tuning_months]）
    wb = window_bounds(base)
    tuning_end_ms = int(
        datetime.strptime(months[base.data_window.tuning_months], "%Y-%m")
        .replace(tzinfo=UTC)
        .timestamp()
        * 1000
    )

    # 回测 + R0 所需的最小列集（drop 冗余原子量/3 秒延迟价，降低内存）
    keep_cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "quote_volume",
        "taker_buy_quote_volume",
        "is_filled",
        "p_1s",
        "hi_post_1s",
        "lo_post_1s",
        "funding_time",
        "funding_rate",
    ]
    signal_frames: list[pd.DataFrame] = []
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    trade_masks: dict[str, object] = {}
    for coin in coins:
        bars = load_bars(
            base.paths.data_root,
            coin.alias,
            wb.start_ms,
            tuning_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        bars = bars[[c for c in keep_cols if c in bars.columns]]
        trade_mask = universe_trade_mask(
            bars["open_time"].to_numpy(dtype="int64"),
            coin.symbol,
            membership,
            universe_cfg.rule.new_entry_delay_days,
        )
        signal_frames.append(
            r0_signals_for_symbol(
                bars,
                train_cfg.r0,
                vol_z_60_thr=features.prefilter.vol_z_60,
                r1_sigma=features.prefilter.r1_sigma,
                brk_60_thr=features.prefilter.brk_60,
                warmup_bars=features.warmup_bars,
                symbol=coin.alias,
                trade_mask=trade_mask,
            )
        )
        bars_by_symbol[coin.alias] = bars
        trade_masks[coin.alias] = trade_mask
    signals = pd.concat(signal_frames, ignore_index=True).sort_values(["open_time", "symbol"])

    fees = FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate)
    reference_sizing = sizing_cfg.model_copy(
        update={
            "initial_equity": backtest_cfg.reference.initial_equity,
            "mode": backtest_cfg.reference.mode,
            "risk_per_trade": backtest_cfg.reference.risk_per_trade,
            "base": backtest_cfg.reference.base,
        }
    )
    ne_symbols = new_entry_symbols(membership, base.data_window.tuning_months)
    ne_aliases = {coin.alias for coin in coins if coin.symbol in ne_symbols}

    result, _ = run_backtest(
        bars_by_symbol,
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
        new_entry_symbols=ne_aliases,
    )

    m = compute_metrics(result, backtest_cfg.reference.initial_equity)
    n = int(m["n_trades"])
    total_notional = sum(t.notional for t in result.trades)
    gross_bp = m["gross_pnl"] / total_notional * 10000 if total_notional else 0.0
    net_bp = m["net_return_usdt"] / total_notional * 10000 if total_notional else 0.0
    fees_bp = m["fees"] / total_notional * 10000 if total_notional else 0.0

    print(f"调参期（M1~M12）R0 早期检查（threshold={backtest_cfg.threshold}）")
    print(f"  交易笔数：{n}")
    print(f"  成本前单笔期望（毛收益/笔）：{gross_bp:.2f} bp")
    print(f"  手续费单笔成本：{fees_bp:.2f} bp")
    print(f"  成本后单笔期望（净收益/笔）：{net_bp:.2f} bp")
    print("  往返成本参考：约 15 bp（双边手续费 ~10bp + 滑点点差 ~5bp）")
    verdict = "成本后为正期望，值得继续投入" if net_bp > 0 else "成本后为负期望，需先证伪/改进"
    print(f"  结论：{verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
