"""研究页只读数据：交易范围快照、质量缓存、数据集 manifest（只读文件）。

规格：docs/10-可视化看板.md §5.5；数据契约：docs/03-数据与选币.md §4、§2.4、§6
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trade.data.loader import load_universe_snapshot
from trade.data.quality import load_quality_cache
from trade.data.universe import load_universe_config


def list_universe_months(data_dir: Path | None) -> list[str]:
    """列出 `data/universe/` 中的月份快照（`YYYY-MM.json`，03 §4.2）。"""
    if data_dir is None:
        return []
    universe_dir = data_dir / "universe"
    if not universe_dir.is_dir():
        return []
    return sorted(path.stem for path in universe_dir.glob("*.json") if _is_month_key(path.stem))


def load_universe_month(data_dir: Path | None, month: str) -> dict[str, Any] | None:
    """读取某月交易范围快照，并补充角色统计（10 §5.5）。

    Args:
        data_dir: `data/` 目录。
        month: `YYYY-MM`。

    Returns:
        快照字典（含 `coins`、`role_counts`、`excluded_counts`）；文件不存在或
        月份格式非法时返回 None。
    """
    if data_dir is None or not _is_month_key(month):
        return None
    path = data_dir / "universe" / f"{month}.json"
    if not path.is_file():
        return None
    snapshot = load_universe_snapshot(data_dir, month)
    coins = [coin.model_dump() for coin in snapshot.coins]
    role_counts: dict[str, int] = {}
    for coin in coins:
        role = str(coin["role"])
        role_counts[role] = role_counts.get(role, 0) + 1
    return {
        "month": snapshot.month,
        "rebalance_ms": snapshot.rebalance_ms,
        "rule_hash": snapshot.rule_hash,
        "coins": coins,
        "role_counts": role_counts,
        "excluded_counts": snapshot.excluded_counts,
    }


def universe_changes(data_dir: Path | None, months: list[str]) -> list[dict[str, Any]]:
    """计算相邻月份的进出名单（10 §5.5“各月入选数量与进出名单”）。"""
    previous: set[str] | None = None
    changes: list[dict[str, Any]] = []
    for month in months:
        snapshot = load_universe_month(data_dir, month)
        current = {str(coin["symbol"]) for coin in snapshot["coins"]} if snapshot else set()
        changes.append(
            {
                "month": month,
                "n_coins": len(current),
                "entered": sorted(current - previous) if previous is not None else [],
                "exited": sorted(previous - current) if previous is not None else [],
            }
        )
        previous = current
    return changes


def load_universe_rule(path: Path | None) -> dict[str, Any] | None:
    """读取 `configs/universe.yaml` 的规则参数与审阅状态（10 §5.5）。"""
    if path is None or not path.is_file():
        return None
    config = load_universe_config(path)
    return {
        "reviewed": config.reviewed,
        "btc_context": config.btc_context,
        "rule": config.rule.model_dump(),
        "roles": config.roles.model_dump(),
        "quality": config.quality.model_dump(),
    }


def read_universe_report(path: Path | None) -> str | None:
    """读取 `universe_report.md` 原文（只读文本，前端用 x-text 转义）。"""
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_quality(path: Path | None, *, limit: int = 2000) -> dict[str, Any] | None:
    """读取质量缓存并汇总（03 §2.4、10 §5.5）。

    只读 `artifacts/quality/quality_cache.parquet`，不重新计算质量。

    Args:
        path: 质量缓存路径。
        limit: 最多返回的行数（按 symbol、window_end_ms 排序）。

    Returns:
        `{"rows", "summary", "symbols"}`；缓存不存在时返回 None。
    """
    if path is None or not path.is_file():
        return None
    try:
        frame = load_quality_cache(path)
    except (OSError, ValueError):
        return None
    summary = {
        "n_rows": int(len(frame)),
        "n_symbols": int(frame["symbol"].nunique()),
        "n_qualified": int(frame["qualified"].sum()),
        "window_start_ms": int(frame["window_start_ms"].min()) if len(frame) else None,
        "window_end_ms": int(frame["window_end_ms"].max()) if len(frame) else None,
    }
    rows = json.loads(frame.head(limit).to_json(orient="records", date_format="iso"))
    return {
        "rows": rows if isinstance(rows, list) else [],
        "summary": summary,
        "symbols": sorted(str(symbol) for symbol in frame["symbol"].unique()),
    }


def list_datasets(data_dir: Path | None) -> list[dict[str, Any]]:
    """列出数据集 manifest 摘要（`data/features/{run_id}/manifest.json`，05 §5）。

    Returns:
        每个数据集一条：run_id、特征哈希、标签模式、样本合计、保留率与正样本率
        的均值/区间（与 04 §8、10 §5.5 一致）。
    """
    if data_dir is None:
        return []
    features_root = data_dir / "features"
    if not features_root.is_dir():
        return []
    datasets: list[dict[str, Any]] = []
    for manifest_path in sorted(features_root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        coins = manifest.get("coins") or []
        retentions = [float(c["retention"]) for c in coins if c.get("retention") is not None]
        positives = [float(c["positive_rate"]) for c in coins if c.get("positive_rate") is not None]
        datasets.append(
            {
                "run_id": manifest.get("run_id", manifest_path.parent.name),
                "include_holdout": bool(manifest.get("include_holdout", False)),
                "feature_hash": manifest.get("feature_hash"),
                "exit_policy_hash": manifest.get("exit_policy_hash"),
                "universe_rule_hash": manifest.get("universe_rule_hash"),
                "label_mode": (manifest.get("label") or {}).get("mode"),
                "n_coins": len(coins),
                "n_samples": int(sum(int(c.get("samples", 0)) for c in coins)),
                "retention_mean": _mean(retentions),
                "retention_min": min(retentions) if retentions else None,
                "retention_max": max(retentions) if retentions else None,
                "positive_rate_mean": _mean(positives),
                "warnings": manifest.get("warnings") or [],
            }
        )
    return datasets


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _is_month_key(value: str) -> bool:
    """判断是否为 `YYYY-MM`。"""
    if len(value) != 7 or value[4] != "-":
        return False
    year, month = value[:4], value[5:]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12
