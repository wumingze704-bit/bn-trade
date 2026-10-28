"""币种名单校验与盲测币抽样命令行工具。

规格：docs/03-数据与选币.md §4.1、§4.4；docs/08-开发计划与验收.md §2 P1
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trade.data.exchange import parse_exchange_info
from trade.data.universe import (
    CoinEntry,
    SymbolMetrics,
    draw_blind,
    dump_coins,
    eligible_blind_candidates,
    load_coins,
    load_universe,
    validate_coins,
)


def _load_metrics(path: Path) -> dict[str, SymbolMetrics]:
    """从 JSON 文件加载逐币可交易性度量。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        symbol: SymbolMetrics(
            price_median_7d=float(item["price_median_7d"]),
            max_leverage=int(item["max_leverage"]),
            daily_quote_median=float(item["daily_quote_median"]),
            monthly_quote_median_min=float(item["monthly_quote_median_min"]),
            tick_rel=float(item["tick_rel"]),
            quality_ok=bool(item.get("quality_ok", True)),
        )
        for symbol, item in raw.items()
    }


def _write_validation_markdown(path: Path, records: list[Any]) -> None:
    """输出 coins_validation.md。"""
    lines = ["# 币种名单校验报告", ""]
    lines.append(f"- 生成时间：{datetime.now(UTC).isoformat()}Z")
    lines.append("")
    lines.append("| 币种 | 角色 | 结论 | 未通过的阻断项 | 警告 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for record in records:
        verdict = "通过" if record.passed else "不通过"
        lines.append(
            f"| {record.symbol} | {record.role} | {verdict} | "
            f"{', '.join(record.blocking_failed) or '—'} | "
            f"{', '.join(record.warnings) or '—'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: list[str] | None = None) -> int:
    """select_coins.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="币种名单校验与盲测币抽样")
    parser.add_argument("--mode", choices=["validate", "draw-blind"], default="validate")
    parser.add_argument("--coins", default="configs/coins.yaml", help="coins.yaml 路径")
    parser.add_argument("--universe", default="configs/universe.yaml", help="universe.yaml 路径")
    parser.add_argument("--exchange-info", required=True, help="exchangeInfo 快照 JSON 路径")
    parser.add_argument("--metrics", help="可选：逐币可交易性度量 JSON 路径")
    parser.add_argument("--out", default="coins_validation.md", help="校验报告输出路径")
    args = parser.parse_args(argv)

    coins_path = _resolve(root, args.coins)
    universe_path = _resolve(root, args.universe)
    exchange_path = _resolve(root, args.exchange_info)

    coins = load_coins(coins_path)
    universe = load_universe(universe_path)
    symbols = parse_exchange_info(json.loads(exchange_path.read_text(encoding="utf-8")))
    symbols_by_name = {symbol.symbol: symbol for symbol in symbols}

    if args.mode == "validate":
        metrics = _load_metrics(_resolve(root, args.metrics)) if args.metrics else {}
        records = validate_coins(coins, universe, symbols_by_name, metrics)
        _write_validation_markdown(_resolve(root, args.out), records)
        coins.validation.generated_at = datetime.now(UTC).isoformat()
        coins.validation.results = records
        dump_coins(coins_path, coins)
        failed = [record.symbol for record in records if not record.passed]
        print(f"校验完成：{len(records)} 个币，阻断未通过：{failed or '无'}")
        return 0

    # draw-blind
    if coins.blind:
        print("盲测币已抽取，跳过（不得重抽，03 §4.4）")
        return 0
    metrics = _load_metrics(_resolve(root, args.metrics)) if args.metrics else {}
    candidates = eligible_blind_candidates(coins, universe, symbols, metrics)
    picked = draw_blind(candidates, universe.blind_seed, universe.blind_count)
    coins.blind = [
        CoinEntry(symbol=symbol, alias=f"B{i + 1}", sector="blind", source="blind")
        for i, symbol in enumerate(picked)
    ]
    dump_coins(coins_path, coins)
    print(f"盲测币抽取完成：{picked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
