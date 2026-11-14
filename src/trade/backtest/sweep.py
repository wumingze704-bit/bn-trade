"""Position fraction sensitivity扫描（06 §5.5）与图表。

规格：docs/06-回测引擎.md §5.5、§8
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.costs import FeeSchedule
from trade.backtest.engine import (
    BacktestConfig,
    ExecutedTrade,
    PreparedSymbol,
    TradeRule,
    run_backtest,
)
from trade.backtest.metrics import compute_metrics
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig, equity_floor_hit
from trade.features.registry import FeaturesConfig


def sizing_sweep(
    bars_by_symbol: Mapping[str, PreparedSymbol] | Mapping[str, pd.DataFrame],
    signals: pd.DataFrame,
    rules: dict[str, TradeRule],
    groups: dict[str, str],
    *,
    fees: FeeSchedule,
    backtest_cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    sizing_cfg: SizingConfig,
    features_cfg: FeaturesConfig,
    fractions: list[float] | None = None,
    initial_equity: float = 100.0,
    scenario_name: str = "base",
    trade_masks: dict[str, object] | None = None,
) -> pd.DataFrame:
    """按 `sizing_sweep.position_frac` 重跑组合模拟（06 §5.5）。

    每个比例都重新运行完整组合模拟（不能按比例缩放收益），并记录：
    期末权益、净收益率、最大回撤、最大单日亏损、最长连续亏损、触发日亏损停止
    天数、放弃开仓次数（按原因）与是否触及权益下限。
    """
    fractions = fractions or list(backtest_cfg.sizing_sweep.position_frac)
    min_notional = min((rule.min_notional for rule in rules.values()), default=5.0)
    rows: list[dict[str, object]] = []
    for fraction in fractions:
        max_positions = min(3, max(1, math.floor(0.95 / fraction)))
        variant = sizing_cfg.model_copy(
            update={
                "position_frac": fraction,
                "max_positions": max_positions,
                "initial_equity": initial_equity,
                "mode": "fixed_fraction",
                "base": "equity",
            }
        )
        result, _ = run_backtest(
            bars_by_symbol,
            signals,
            rules,
            groups,
            scen_name=scenario_name,
            fees=fees,
            backtest_cfg=backtest_cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=variant,
            k_sl=features_cfg.label.k_sl,
            sl_min=features_cfg.label.sl_min,
            sl_max=features_cfg.label.sl_max,
            H_label=features_cfg.label.H_label,
            initial_equity=initial_equity,
            trade_masks=trade_masks,  # type: ignore[arg-type]
        )
        metrics = compute_metrics(result, initial_equity)
        skipped = pd.Series([item.reason for item in result.skipped], dtype="object")
        daily = _daily_pnl(result.trades)
        rows.append(
            {
                "position_frac": fraction,
                "max_positions": max_positions,
                "final_equity": float(metrics["final_equity"]),
                "net_return_pct": float(metrics["net_return_pct"]),
                "max_drawdown": float(metrics["max_drawdown"]),
                "max_daily_loss": float(daily.min()) if not daily.empty else 0.0,
                "max_consecutive_losses": _max_consecutive_losses(result.trades),
                "daily_stop_days": int((daily <= -variant.daily_loss_stop * initial_equity).sum()),
                "skipped_below_min_notional": int((skipped == "below_min_notional").sum()),
                "skipped_step_rounding": int((skipped == "step_rounding").sum()),
                "skipped_max_positions": int((skipped == "max_positions").sum()),
                "equity_floor_hit": equity_floor_hit(
                    float(metrics["final_equity"]), variant, min_notional
                ),
            }
        )
    return pd.DataFrame(rows)


def write_sizing_sweep(report_dir: Path, frame: pd.DataFrame) -> None:
    """写 `sizing_sweep.parquet` / `sizing_sweep.md`（06 §8）。"""
    if frame.empty:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(report_dir / "sizing_sweep.parquet", index=False)
    columns = [
        "position_frac",
        "max_positions",
        "final_equity",
        "net_return_pct",
        "max_drawdown",
        "max_daily_loss",
        "max_consecutive_losses",
        "daily_stop_days",
        "skipped_below_min_notional",
        "skipped_step_rounding",
        "skipped_max_positions",
        "equity_floor_hit",
    ]
    lines = ["# Position fraction sensitivity扫描（06 §5.5）", ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(":--" for _ in columns) + " |")
    for row in frame[columns].itertuples(index=False):
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    (report_dir / "sizing_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plot_sizing_sweep(frame: pd.DataFrame, path: Path) -> None:
    """画出比例-收益/回撤曲线（06 §8 PNG）。"""
    if frame.empty:
        return
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(6, 4), facecolor="#0B0B10")
    axis.set_facecolor("#0B0B10")
    axis.plot(frame["position_frac"], frame["net_return_pct"], marker="o", color="#30D158")
    axis.plot(frame["position_frac"], -frame["max_drawdown"], marker="s", color="#FF453A")
    axis.set_xlabel("position_frac", color="#8E8E93")
    axis.set_ylabel("Return / Drawdown", color="#8E8E93")
    axis.grid(True, color="#2C2C2E", linewidth=0.6)
    axis.tick_params(colors="#8E8E93")
    axis.set_title("Position fraction sensitivity", color="#F5F5F7")
    figure.tight_layout()
    figure.savefig(path, dpi=120, facecolor="#0B0B10")
    plt.close(figure)


def _daily_pnl(trades: list[ExecutedTrade]) -> pd.Series:
    frames = [
        {
            "day": pd.Timestamp(trade.exit_open_time, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
            "net_pnl": trade.net_pnl,
        }
        for trade in trades
    ]
    if not frames:
        return pd.Series(dtype="float64")
    return pd.DataFrame(frames).groupby("day")["net_pnl"].sum()


def _max_consecutive_losses(trades: list[ExecutedTrade]) -> int:
    longest = current = 0
    for trade in sorted(trades, key=lambda item: item.exit_open_time):
        if trade.net_pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
