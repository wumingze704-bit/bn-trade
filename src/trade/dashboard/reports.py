"""回测报告与模型产物读取（只读文件）。

规格：docs/06-回测引擎.md §8；docs/10-可视化看板.md §5.5
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

_TRADES_LIMIT = 2000


def list_reports(reports_dir: Path) -> list[dict[str, Any]]:
    """列出报告目录（按修改时间倒序），标记 `--final` 运行（06 §8）。

    Args:
        reports_dir: `artifacts/reports` 目录。

    Returns:
        报告摘要列表；目录不存在或为空时返回空列表。
    """
    if not reports_dir.is_dir():
        return []
    final_ids = _final_run_ids(reports_dir)
    reports: list[dict[str, Any]] = []
    for child in reports_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime_ms = int(child.stat().st_mtime * 1000)
        except OSError:
            continue
        summary = _read_json(child / "summary.json")
        gate = _read_json(child / "gate.json")
        reports.append(
            {
                "run_id": child.name,
                "mtime_ms": mtime_ms,
                "final": child.name in final_ids,
                "source": gate.get("source") if gate else None,
                "reference_base": _reference_base_metrics(summary),
            }
        )
    reports.sort(key=lambda item: item["mtime_ms"], reverse=True)
    return reports


def load_report(reports_dir: Path, run_id: str) -> dict[str, Any] | None:
    """读取单个报告的完整内容（06 §8）。

    Args:
        reports_dir: `artifacts/reports` 目录。
        run_id: 报告目录名。

    Returns:
        报告内容字典；目录不存在时返回 None。
    """
    report_dir = reports_dir / run_id
    if not report_dir.is_dir():
        return None
    gate = _read_json(report_dir / "gate.json")
    return {
        "run_id": run_id,
        "final": run_id in _final_run_ids(reports_dir),
        "source": gate.get("source") if gate else None,
        "summary": _read_json(report_dir / "summary.json"),
        "gate": gate,
        "report_md": _read_text(report_dir / "report.md"),
        "equity": _read_equity(report_dir / "equity.parquet", 2000),
        "trades": _read_table(report_dir / "trades.parquet", _TRADES_LIMIT),
        "skipped": _read_table(report_dir / "skipped.parquet", _TRADES_LIMIT),
        "files": sorted(path.name for path in report_dir.iterdir() if path.is_file()),
    }


def list_models(models_dir: Path) -> list[dict[str, Any]]:
    """列出模型目录并读取 `model_meta.json`（10 §5.5）。

    Args:
        models_dir: `artifacts/models` 目录。

    Returns:
        模型摘要列表；目录不存在或为空时返回空列表。
    """
    if not models_dir.is_dir():
        return []
    models: list[dict[str, Any]] = []
    for child in models_dir.iterdir():
        if not child.is_dir():
            continue
        models.append(
            {
                "model_id": child.name,
                "meta": _read_json(child / "model_meta.json"),
            }
        )
    models.sort(key=lambda item: item["model_id"], reverse=True)
    return models


def load_model(models_dir: Path, model_id: str) -> dict[str, Any] | None:
    """读取单个模型的元数据与训练曲线（10 §5.5）。

    Args:
        models_dir: `artifacts/models` 目录。
        model_id: 模型目录名。

    Returns:
        模型内容字典；目录不存在时返回 None。
    """
    model_dir = models_dir / model_id
    if not model_dir.is_dir():
        return None
    return {
        "model_id": model_id,
        "meta": _read_json(model_dir / "model_meta.json"),
        "train_log": _read_table(model_dir / "train_log.csv", _TRADES_LIMIT),
        "files": sorted(path.name for path in model_dir.iterdir() if path.is_file()),
    }


def _final_run_ids(reports_dir: Path) -> set[str]:
    """解析 `FINAL_RUNS.log`，返回封存集运行的 run_id 集合（06 §8）。"""
    path = reports_dir / "FINAL_RUNS.log"
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = record.get("run_id") if isinstance(record, dict) else None
        if isinstance(run_id, str):
            ids.add(run_id)
    return ids


def _reference_base_metrics(summary: dict[str, Any] | None) -> dict[str, Any]:
    """提取 reference/base 情景核心指标，用于报告列表预览（06 §8）。"""
    if not summary:
        return {}
    base = summary.get("base")
    metrics = base.get("reference", {}) if isinstance(base, dict) else {}
    keys = ("net_return_usdt", "net_return_pct", "profit_factor", "n_trades", "win_rate")
    return {key: metrics.get(key) for key in keys}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_equity(path: Path, max_points: int) -> list[dict[str, Any]]:
    """读取权益曲线并按（run, scenario）降采样到最多 max_points 点（10 §10）。"""
    if not path.is_file():
        return []
    try:
        frame = pd.read_parquet(path)
    except OSError:
        return []
    if frame.empty or "ts_ms" not in frame.columns:
        return []
    series: list[dict[str, Any]] = []
    if {"run", "scenario"} <= set(frame.columns):
        for (run, scenario), group in frame.groupby(["run", "scenario"]):
            series.append(
                {"run": run, "scenario": scenario, "points": _downsample_equity(group, max_points)}
            )
    else:
        series.append({"points": _downsample_equity(frame, max_points)})
    return series


def _downsample_equity(group: pd.DataFrame, max_points: int) -> list[dict[str, Any]]:
    """把单条权益序列降采样到最多 max_points 点（10 §10）。"""
    ordered = group.sort_values("ts_ms")
    step = max(1, math.ceil(len(ordered) / max_points))
    has_drawdown = "drawdown" in ordered.columns
    return [
        {
            "ts_ms": int(row.ts_ms),
            "equity": float(row.equity),
            "drawdown": float(row.drawdown) if has_drawdown else 0.0,
        }
        for row in ordered.iloc[::step].itertuples(index=False)
    ]


def _read_table(path: Path, limit: int) -> list[dict[str, Any]]:
    """读取 Parquet/CSV 明细，限制行数并转为 JSON 兼容对象。"""
    if not path.is_file():
        return []
    try:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    except (OSError, ValueError):
        return []
    if frame.empty:
        return []
    records = json.loads(frame.head(limit).to_json(orient="records", date_format="iso"))
    return records if isinstance(records, list) else []
