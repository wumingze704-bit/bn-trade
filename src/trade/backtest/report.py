"""回测报告产物与 gate.json（06 §8）。

所有图表数据同时以 Parquet/JSON 保存（06 §8）；图片在正式回测（P4）时由看板生成。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.engine import ExecutedTrade, PortfolioResult, SkippedSignal
from trade.backtest.metrics import compute_metrics


def compute_experiment_hash(
    effective_configs: Mapping[str, object], input_files: Mapping[str, Path]
) -> str:
    """计算生效配置与输入文件内容的 SHA256（06 §8）。

    Args:
        effective_configs: 按配置名映射的 JSON 兼容配置值。
        input_files: 按输入名映射的配置快照、规则快照或信号文件路径。

    Returns:
        完整的小写 SHA256 十六进制摘要。

    Raises:
        OSError: 输入文件无法读取。
        TypeError: 配置值不能编码为 JSON。
    """
    input_hashes = {name: _sha256_file(path) for name, path in sorted(input_files.items())}
    payload = {"configs": effective_configs, "input_files": input_hashes}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def experiments_log_path(artifact_root: Path) -> Path:
    """返回统一的试验日志路径（06 §8）。"""
    return artifact_root / "reports" / "EXPERIMENTS.log"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def trades_to_frame(trades: list[ExecutedTrade], run: str, scenario: str) -> pd.DataFrame:
    """把交易列表转为 trades.parquet 的 DataFrame（06 §8）。"""
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "run": run,
            "scenario": scenario,
            "symbol": [t.symbol for t in trades],
            "group": [t.group for t in trades],
            "signal_time": [t.signal_open_time for t in trades],
            "entry_time": [t.entry_open_time for t in trades],
            "exit_time": [t.exit_open_time for t in trades],
            "p": [t.p for t in trades],
            "m": [t.m for t in trades],
            "entry_price": [t.p_fill for t in trades],
            "initial_stop": [t.p_fill * (1.0 - t.sl) for t in trades],
            "exit_price": [t.exit_price for t in trades],
            "exit_type": [t.exit_type for t in trades],
            "stop_updates": [t.stop_updates for t in trades],
            "mfe_r": [t.mfe_r for t in trades],
            "mae_r": [t.mae_r for t in trades],
            "bars_held": [t.bars_held for t in trades],
            "notional": [t.notional for t in trades],
            "qty": [t.qty for t in trades],
            "margin": [t.margin for t in trades],
            "entry_fee": [t.entry_fee for t in trades],
            "exit_fee": [t.exit_fee for t in trades],
            "gross_pnl": [t.gross_pnl for t in trades],
            "net_pnl": [t.net_pnl for t in trades],
            "size_capped_by": [t.capped_by for t in trades],
        }
    )


def skipped_to_frame(skipped: list[SkippedSignal], run: str, scenario: str) -> pd.DataFrame:
    """被放弃的候选信号表（06 §8 skipped.parquet，含真实 run/scenario）。"""
    if not skipped:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "run": [run] * len(skipped),
            "scenario": [scenario] * len(skipped),
            "symbol": [s.symbol for s in skipped],
            "open_time": [s.open_time for s in skipped],
            "p": [s.p for s in skipped],
            "reason": [s.reason for s in skipped],
        }
    )


def gate_payload(
    run_id: str,
    reference_metrics: dict[str, float],
    sanity_results: dict[str, str],
    *,
    source: str,
    g2_bootstrap: dict[str, dict[str, float]] | None = None,
) -> dict[str, object]:
    """构造 gate.json 内容（06 §8，基于 reference 运行）。

    G2 统计显著性（单笔净期望的 UTC 日分块 bootstrap）以 `g2_bootstrap` 写入，
    含全部/训练组/留出组的下限、上限、次数、种子与是否通过（08 §3 G2）。P3 的
    R0 回测只写指标与健全性检验结果，gate 字段为 null。
    """
    return {
        "run_id": run_id,
        "run": "reference",
        "scenario": "base",
        "source": source,
        "metrics": reference_metrics,
        "sanity": sanity_results,
        "g2_bootstrap": g2_bootstrap,
        "gate": None,
    }


def write_report(
    report_dir: Path,
    run_id: str,
    *,
    source: str,
    run_results: dict[str, dict[str, PortfolioResult]],
    reference_metrics: dict[str, float],
    sanity_results: dict[str, str],
    initial_equity: float,
    g2_bootstrap: dict[str, dict[str, float]] | None = None,
) -> None:
    """写出 report.md / summary.json / equity / trades / skipped / gate.json（06 §8）。

    `run_results` 为 run → scenario → PortfolioResult；每笔交易与放弃信号保留真实的
    run 与 scenario（06 §8，T0.11）。
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    trade_frames: list[pd.DataFrame] = []
    skipped_frames: list[pd.DataFrame] = []
    equity_frames: list[pd.DataFrame] = []
    summary: dict[str, dict[str, dict[str, float]]] = {}

    for run, scenario_results in run_results.items():
        for scen, result in scenario_results.items():
            summary.setdefault(scen, {})[run] = compute_metrics(result, initial_equity)
            trade_frames.append(trades_to_frame(result.trades, run, scen))
            skipped_frames.append(skipped_to_frame(result.skipped, run, scen))
            if not result.equity_curve.empty:
                frame = result.equity_curve.copy()
                frame["run"] = run
                frame["scenario"] = scen
                equity_frames.append(frame)

    (report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    pd.concat(trade_frames, ignore_index=True).to_parquet(report_dir / "trades.parquet")
    pd.concat(skipped_frames, ignore_index=True).to_parquet(report_dir / "skipped.parquet")
    if equity_frames:
        pd.concat(equity_frames, ignore_index=True).to_parquet(report_dir / "equity.parquet")

    gate = gate_payload(
        run_id, reference_metrics, sanity_results, source=source, g2_bootstrap=g2_bootstrap
    )
    (report_dir / "gate.json").write_text(
        json.dumps(gate, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (report_dir / "report.md").write_text(
        _render_report_md(run_id, source, summary, reference_metrics, sanity_results),
        encoding="utf-8",
    )


def _render_report_md(
    run_id: str,
    source: str,
    summary: dict[str, dict[str, dict[str, float]]],
    reference_metrics: dict[str, float],
    sanity_results: dict[str, str],
) -> str:
    lines = [
        f"# 回测报告 {run_id}",
        "",
        f"- 数据来源：**{source}**（fixture 结果不得冒充真实行情回测）",
        f"- 生成时间：{datetime.now(UTC).isoformat()}",
        "",
        "## 1. 摘要（reference / base）",
        "",
    ]
    for key in ("net_return_usdt", "net_return_pct", "profit_factor", "n_trades", "win_rate"):
        lines.append(f"- {key}: {reference_metrics.get(key)}")
    lines += [
        "",
        "## 2. 各情景核心指标（reference / user）",
        "",
        "| 情景 | 运行 | 净收益 USDT | 净收益率 | PF | 笔数 |",
        "| :-- | :-- | :-- | :-- | :-- | :-- |",
    ]
    for scen, runs in summary.items():
        for run in sorted(runs):
            m = runs.get(run, {})
            lines.append(
                f"| {scen} | {run} | {m.get('net_return_usdt', 0):.4f} "
                f"| {m.get('net_return_pct', 0):.4%} | {m.get('profit_factor', 0):.3f} "
                f"| {int(m.get('n_trades', 0))} |"
            )
    lines += ["", "## 3. 健全性检验（06 §7）", "", "| 编号 | 结果 |", "| :-- | :-- |"]
    for code, status in sanity_results.items():
        lines.append(f"| {code} | {status} |")
    return "\n".join(lines) + "\n"


def append_experiments_log(
    log_path: Path,
    run_id: str,
    config_hash: str,
    metrics: dict[str, float],
) -> None:
    """在 EXPERIMENTS.log 追加一行（06 §8，用于 S11）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "config_hash": config_hash,
        "net_return_usdt": metrics.get("net_return_usdt"),
        "profit_factor": metrics.get("profit_factor"),
        "n_trades": metrics.get("n_trades"),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
