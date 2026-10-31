"""规则化月度选币命令行工具（03 §4.6）。

用法（在东京服务器，有全市场日线 data/daily/ 时）：
    python scripts/select_coins.py --build --month 2024-10
    python scripts/select_coins.py --build --all
    python scripts/select_coins.py --report
"""

from __future__ import annotations

import argparse
import json
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from trade.common.config import BaseConfig, load_config
from trade.data.loader import (
    list_daily_symbols,
    load_daily_bars,
    rebalance_ms_of,
    universe_months,
)
from trade.data.universe import (
    SymbolMeta,
    UniverseConfig,
    UniverseSnapshot,
    assign_aliases,
    build_universe,
    load_aliases,
    load_universe_config,
    read_universe_snapshot,
    rule_hash,
    save_aliases,
    write_universe_snapshot,
)

_META_FIELDS = {field.name for field in dataclass_fields(SymbolMeta)}


def _resolve(root: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_symbol_meta(path: Path | None) -> dict[str, SymbolMeta]:
    """从 rules.json 读取逐币分类与交易规则（含已下架近似值，03 §4.3）。"""
    if path is None or not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, SymbolMeta] = {}
    for symbol, fields in raw.items():
        if isinstance(fields, dict):
            values = {key: value for key, value in fields.items() if key in _META_FIELDS}
        else:
            values = {}
        result[symbol] = SymbolMeta(symbol=symbol, **values)
    return result


def _load_quality(path: Path | None) -> pd.DataFrame | None:
    if path is None or not path.is_file():
        return None
    return pd.read_parquet(path)


def _build_month(
    month: str,
    daily: dict[str, pd.DataFrame],
    meta: dict[str, SymbolMeta],
    cfg: UniverseConfig,
    quality: pd.DataFrame | None,
    aliases: dict[str, str],
    out_dir: Path,
) -> dict[str, str]:
    """构建单月快照（规则哈希保护），返回更新后的别名映射。"""
    snapshot_path = out_dir / f"{month}.json"
    current_hash = rule_hash(cfg)
    if snapshot_path.is_file():
        existing = read_universe_snapshot(snapshot_path)
        if existing.rule_hash != current_hash:
            raise SystemExit(
                f"{month} 快照已存在但规则哈希不同"
                f"（{existing.rule_hash[:8]}… vs {current_hash[:8]}…），"
                "拒绝覆盖（03 §4.6）；如需重建请先删除该快照或恢复原规则"
            )
        return aliases
    snapshot = build_universe(rebalance_ms_of(month), daily, meta, cfg, quality_cache=quality)
    snapshot, aliases = assign_aliases(snapshot, aliases)
    write_universe_snapshot(snapshot_path, snapshot)
    print(f"已生成 {month}：{len(snapshot.coins)} 个币")
    return aliases


def _render_report(snapshots: dict[str, UniverseSnapshot], cfg: UniverseConfig) -> str:
    lines = ["# 选币快照报告（universe_report.md）", ""]
    lines.append(f"- 生成时间：{datetime.now(UTC).isoformat()}Z")
    lines.append(f"- 规则哈希：{rule_hash(cfg)}")
    lines.append(f"- reviewed：{'true' if cfg.reviewed else 'false'}（使用者审阅后手动改为 true）")
    lines.append("")
    lines.append("## 各月入选概况")
    lines.append("")
    lines.append(
        "| 月份 | 入选数 | 训练组 | 留出组 | 排除(candidate/排除/上线/流动性/可交易/质量) |"
    )
    lines.append("| :--- | ---: | ---: | ---: | :--- |")
    for month, snapshot in snapshots.items():
        train = sum(1 for coin in snapshot.coins if coin.role == "train")
        holdout = sum(1 for coin in snapshot.coins if coin.role == "holdout")
        ec = snapshot.excluded_counts
        lines.append(
            f"| {month} | {len(snapshot.coins)} | {train} | {holdout} | "
            f"{ec.get('candidates', 0)}/{ec.get('excluded', 0)}/{ec.get('listing', 0)}/"
            f"{ec.get('liquidity', 0)}/{ec.get('tradability', 0)}/{ec.get('quality', 0)} |"
        )
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    for month, snapshot in snapshots.items():
        lines.append(f"### {month}")
        lines.append("")
        if not snapshot.coins:
            lines.append("（无入选币）")
            lines.append("")
            continue
        lines.append(
            "| 排名 | symbol | alias | 角色 | 30 天日成交额中位数 | tick_rel | step_notional |"
        )
        lines.append("| ---: | :--- | :--- | :--- | ---: | ---: | ---: |")
        for coin in snapshot.coins:
            tick_rel = "—" if coin.tick_rel is None else f"{coin.tick_rel:.6f}"
            step_notional = "—" if coin.step_notional is None else f"{coin.step_notional:.4f}"
            lines.append(
                f"| {coin.rank} | {coin.symbol} | {coin.alias} | {coin.role} | "
                f"{coin.daily_quote_median:.0f} | {tick_rel} | {step_notional} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """select_coins.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="规则化月度选币（03 §4.6）")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--data-root", default=None, help="默认取 base.yaml paths.data_root")
    parser.add_argument("--rules", default=None, help="逐币分类与交易规则 JSON（03 §4.3）")
    parser.add_argument("--quality", default=None, help="T0.17 质量缓存 parquet（可选）")
    parser.add_argument("--report-out", default="universe_report.md")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--report", action="store_true")
    parser.add_argument("--month", default=None, help="--build 单月：YYYY-MM")
    parser.add_argument("--all", action="store_true", help="--build 全部月份 M1..M{months}")
    args = parser.parse_args(argv)

    base = load_config(BaseConfig, _resolve(root, args.base))
    cfg = load_universe_config(_resolve(root, args.universe))
    data_root = _resolve(root, args.data_root) or base.paths.data_root
    months = [args.month] if args.month else universe_months(base)

    if args.build:
        if args.month and args.all:
            parser.error("--month 与 --all 不能同时使用")
        if not args.month and not args.all:
            parser.error("--build 需要 --month 或 --all")
        symbols = list_daily_symbols(data_root)
        if not symbols:
            raise SystemExit("未找到全市场日线目录 data_root/daily/，请先完成 T1-3a 日线下载")
        daily = {symbol: load_daily_bars(data_root, symbol) for symbol in symbols}
        meta = _load_symbol_meta(_resolve(root, args.rules))
        quality = _load_quality(_resolve(root, args.quality))
        out_dir = data_root / "universe"
        aliases = load_aliases(out_dir / "aliases.json")
        for month in months:
            aliases = _build_month(month, daily, meta, cfg, quality, aliases, out_dir)
        save_aliases(out_dir / "aliases.json", aliases)
        return 0

    # --report
    out_dir = data_root / "universe"
    snapshots = {month: read_universe_snapshot(out_dir / f"{month}.json") for month in months}
    report_path = _resolve(root, args.report_out)
    assert report_path is not None
    report_path.write_text(_render_report(snapshots, cfg), encoding="utf-8")
    print(f"已生成报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
