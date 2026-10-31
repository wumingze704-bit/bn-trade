"""拉取并保存币安 exchangeInfo / leverageBracket 快照，生成 rules.json（服务器运行）。

规格：docs/03-数据与选币.md §4.2、§4.3；docs/09-币安数据与API.md §3.1、§3.2
用法（在东京服务器）：
    python scripts/snapshot_rules.py --out artifacts/exchange_info \\
        --rules artifacts/exchange_info/rules.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from trade.data.snapshot import (
    _DEFAULT_STABLE_BASES,
    build_rules,
    fetch_exchange_info,
    fetch_leverage_bracket,
    save_timestamped_snapshot,
    write_rules_json,
)


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
    write_rules_json(rules, rules_path)
    print(f"已保存快照：{exchange_path}")
    print(f"已保存快照：{bracket_path}")
    print(f"已生成 rules.json：{rules_path}（{len(rules)} 个 USDT 永续）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
