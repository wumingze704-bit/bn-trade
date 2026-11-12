"""拉取并保存币安 exchangeInfo / leverageBracket 快照，生成 rules.json（服务器运行）。

规格：docs/03-数据与选币.md §4.2、§4.3；docs/09-币安数据与API.md §3.1、§3.2
用法（在东京服务器）：
    python scripts/snapshot_rules.py --out artifacts/exchange_info \\
        --rules artifacts/exchange_info/rules.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx
import numpy as np

from trade.data.aggregate import read_aggtrades_zip
from trade.data.snapshot import (
    _DEFAULT_STABLE_BASES,
    SymbolMeta,
    build_rules,
    fetch_exchange_info,
    fetch_leverage_bracket,
    infer_delisted_rules,
    save_timestamped_snapshot,
    write_rules_json,
)

# 推断已下架合约规则时每个文件最多读取多少行（控制内存）
_MAX_SAMPLE_ROWS = 200_000


def merge_rules(
    rules: Mapping[str, SymbolMeta], inferred: Mapping[str, SymbolMeta]
) -> dict[str, SymbolMeta]:
    """把推断出的已下架合约规则并入 rules（不覆盖已有条目，03 §4.3）。"""
    merged = dict(rules)
    for symbol, meta in inferred.items():
        merged.setdefault(symbol, meta)
    return merged


def infer_missing_rules(
    rules: Mapping[str, SymbolMeta],
    aliases: Mapping[str, str],
    raw_dir: Path,
    *,
    max_rows: int = _MAX_SAMPLE_ROWS,
) -> dict[str, SymbolMeta]:
    """为 exchangeInfo 中缺失（已下架）的 symbol 用 aggTrades 推断规则（03 §4.3）。

    Args:
        rules: 现有规则（symbol -> SymbolMeta）。
        aliases: symbol -> alias 映射（`data/universe/aliases.json`）。
        raw_dir: 原始逐笔目录 `data/raw/aggtrades`（`{alias}/{symbol}-aggTrades-*.zip`）。
        max_rows: 每个合约最多取样的成交条数。

    Returns:
        symbol -> 推断出的 SymbolMeta；没有缺失时为空字典。

    Raises:
        ValueError: 缺失合约没有任何原始逐笔文件（红线 13，不做静默跳过）。
    """
    inferred: dict[str, SymbolMeta] = {}
    for symbol, alias in sorted(aliases.items()):
        if symbol in rules:
            continue
        files = sorted((raw_dir / alias).glob(f"{symbol}-aggTrades-*.zip"))
        if not files:
            raise ValueError(
                f"已下架合约 {symbol}（{alias}）没有原始逐笔文件，无法推断交易规则：{raw_dir}"
                "（03 §4.3）"
            )
        path = files[len(files) // 2]
        prices: list[np.ndarray] = []
        quantities: list[np.ndarray] = []
        rows = 0
        for frame in read_aggtrades_zip(path):
            prices.append(frame["price"].to_numpy(dtype="float64"))
            quantities.append(frame["quantity"].to_numpy(dtype="float64"))
            rows += len(frame)
            if rows >= max_rows:
                break
        inferred[symbol] = infer_delisted_rules(
            symbol, np.concatenate(prices), np.concatenate(quantities)
        )
        print(f"已推断已下架合约规则：{symbol}（{alias}，样本 {rows} 条，文件 {path.name}）")
    return inferred


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _split_csv(value: str | None) -> frozenset[str]:
    return frozenset(part.strip() for part in (value or "").split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    """snapshot_rules.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="拉取币安交易规则快照并生成 rules.json")
    parser.add_argument("--out", default="artifacts/exchange_info", help="快照输出目录")
    parser.add_argument(
        "--rules", default="artifacts/exchange_info/rules.json", help="rules.json 输出路径"
    )
    parser.add_argument("--stable-bases", default=None, help="稳定币基础资产，逗号分隔")
    parser.add_argument("--excluded-symbols", default=None, help="显式排除 symbol，逗号分隔")
    parser.add_argument(
        "--infer-delisted",
        action="store_true",
        help="为 exchangeInfo 中缺失的已下架合约用 aggTrades 推断规则（03 §4.3）",
    )
    parser.add_argument(
        "--aliases", default="data/universe/aliases.json", help="别名映射（symbol -> alias）"
    )
    parser.add_argument(
        "--raw-aggtrades",
        default="data/raw/aggtrades",
        help="原始逐笔目录（推断已下架合约规则用）",
    )
    args = parser.parse_args(argv)

    stable_bases = _split_csv(args.stable_bases) or _DEFAULT_STABLE_BASES
    excluded = _split_csv(args.excluded_symbols)
    out_dir = _resolve(root, args.out)
    rules_path = _resolve(root, args.rules)

    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            exchange_info = fetch_exchange_info(client)
            try:
                leverage_bracket = fetch_leverage_bracket(client)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (401, 403):
                    raise
                print(
                    "警告：leverageBracket 需签名（API Key），已跳过；"
                    "max_leverage 视为满足（03 §4.3、09 §3.2）",
                    file=sys.stderr,
                )
                leverage_bracket = []
    except (httpx.HTTPError, ValueError) as error:
        print(f"拉取快照失败：{error}", file=sys.stderr)
        return 1

    exchange_path = save_timestamped_snapshot(exchange_info, out_dir, "exchangeInfo")
    bracket_path = save_timestamped_snapshot(leverage_bracket, out_dir, "leverageBracket")
    rules = build_rules(
        exchange_info,
        leverage_bracket,
        stable_bases=stable_bases,
        excluded_symbols=excluded,
    )
    if args.infer_delisted:
        aliases_path = _resolve(root, args.aliases)
        if not aliases_path.is_file():
            print(f"别名映射不存在：{aliases_path}", file=sys.stderr)
            return 1
        aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
        inferred = infer_missing_rules(rules, aliases, _resolve(root, args.raw_aggtrades))
        rules = merge_rules(rules, inferred)
        print(f"已并入 {len(inferred)} 个已下架合约规则")
    write_rules_json(rules, rules_path)
    print(f"已保存快照：{exchange_path}")
    print(f"已保存快照：{bracket_path}")
    print(f"已生成 rules.json：{rules_path}（{len(rules)} 个 USDT 永续）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
