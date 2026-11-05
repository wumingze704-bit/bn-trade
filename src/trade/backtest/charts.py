"""回测报告静态图片（06 §8）。

所有图表数据同时以 Parquet/JSON 保存（06 §8）；本模块只负责由这些文件生成 PNG。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无 GUI 环境（服务器/测试）也能出图

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # type: ignore[import-untyped]  # noqa: E402

_DARK = "#0B0B10"
_GREEN = "#30D158"
_BLUE = "#0A84FF"
_GRID = "rgba(255,255,255,0.08)"


def _style(axis: object) -> None:
    axis.set_facecolor(_DARK)  # type: ignore[attr-defined]
    axis.grid(True, color="#2C2C2E", linewidth=0.6)  # type: ignore[attr-defined]
    axis.tick_params(colors="#8E8E93")  # type: ignore[attr-defined]


def write_all_charts(report_dir: Path) -> None:
    """由 `equity.parquet` / `trades.parquet` 生成 06 §8 要求的 PNG。"""
    equity_path = report_dir / "equity.parquet"
    trades_path = report_dir / "trades.parquet"
    if equity_path.is_file():
        equity = pd.read_parquet(equity_path)
        if not equity.empty:
            _plot_equity(equity, report_dir / "equity.png")
    if trades_path.is_file():
        trades = pd.read_parquet(trades_path)
        if not trades.empty:
            _plot_monthly(trades, report_dir / "monthly.png")
            _plot_by_symbol(trades, report_dir / "by_symbol.png")
            if "p" in trades.columns:
                _plot_p_calibration(trades, report_dir / "p_calibration.png")


def _plot_equity(equity: pd.DataFrame, path: Path) -> None:
    figure, axis = plt.subplots(figsize=(10, 4), facecolor=_DARK)
    _style(axis)
    for (run, scen), group in equity.groupby(["run", "scenario"]):
        ordered = group.sort_values("ts_ms")
        axis.plot(
            pd.to_datetime(ordered["ts_ms"], unit="ms", utc=True),
            ordered["equity"],
            label=f"{run}/{scen}",
            color=_GREEN if scen == "base" else _BLUE,
            linewidth=1.6,
        )
    axis.set_title("Equity curve", color="#F5F5F7")
    axis.legend(facecolor=_DARK, labelcolor="#8E8E93")
    figure.tight_layout()
    figure.savefig(path, dpi=120, facecolor=_DARK)
    plt.close(figure)


def _monthly_pnl(trades: pd.DataFrame) -> pd.Series:
    moments = pd.to_datetime(trades["exit_time"], unit="ms", utc=True)
    return trades.assign(month=moments.dt.strftime("%Y-%m")).groupby("month")["net_pnl"].sum()


def _plot_monthly(trades: pd.DataFrame, path: Path) -> None:
    monthly = _monthly_pnl(trades)
    figure, axis = plt.subplots(figsize=(10, 3.5), facecolor=_DARK)
    _style(axis)
    colors = [_GREEN if value >= 0 else "#FF453A" for value in monthly]
    axis.bar(monthly.index, monthly.values, color=colors)
    axis.set_title("Monthly net PnL (USDT)", color="#F5F5F7")
    figure.tight_layout()
    figure.savefig(path, dpi=120, facecolor=_DARK)
    plt.close(figure)


def _plot_by_symbol(trades: pd.DataFrame, path: Path) -> None:
    by_symbol = trades.groupby("symbol")["net_pnl"].sum().sort_values()
    figure, axis = plt.subplots(figsize=(8, 4), facecolor=_DARK)
    _style(axis)
    colors = [_GREEN if value >= 0 else "#FF453A" for value in by_symbol]
    axis.barh(by_symbol.index, by_symbol.values, color=colors)
    axis.set_title("Net PnL by symbol (USDT)", color="#F5F5F7")
    figure.tight_layout()
    figure.savefig(path, dpi=120, facecolor=_DARK)
    plt.close(figure)


def _plot_p_calibration(trades: pd.DataFrame, path: Path) -> None:
    """Calibration图：p 分箱后的Win rate。"""
    bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    frame = trades.copy()
    frame["win"] = (frame["net_pnl"] > 0).astype("float64")
    frame["bin"] = pd.cut(frame["p"], bins=bins, include_lowest=True)
    grouped = frame.groupby("bin", observed=True).agg(p=("p", "mean"), win=("win", "mean"))
    figure, axis = plt.subplots(figsize=(5, 4), facecolor=_DARK)
    _style(axis)
    axis.plot([0, 1], [0, 1], linestyle="--", color="#8E8E93", linewidth=1)
    axis.plot(grouped["p"], grouped["win"], marker="o", color=_BLUE)
    axis.set_xlabel("Predicted p", color="#8E8E93")
    axis.set_ylabel("Win rate", color="#8E8E93")
    axis.set_title("Calibration", color="#F5F5F7")
    figure.tight_layout()
    figure.savefig(path, dpi=120, facecolor=_DARK)
    plt.close(figure)
