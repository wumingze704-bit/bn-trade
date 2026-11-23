"""重建研究期月度选币快照 `data/universe_hist/{YYYY-MM}.json`（docs/13 §1）。

与 `scripts/select_coins.py` 的区别：
- 输入为研究数据目录 `data/klines_1d/`（月度 parquet），不使用旧 `data/daily/`；
- 第 7 步质量检查改为“R 前一个自然月的 1h K 线缺失比例 ≤ 1%”；
- 输出到 `data/universe_hist/`，整个研究期同一口径。
"""

from __future__ import annotations

import argparse
import json
from calendar import monthrange
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pyarrow.parquet as pq

from trade.data.universe import (
    SymbolMeta,
    UniverseConfig,
    assign_aliases,
    build_universe,
    load_aliases,
    load_universe_config,
    read_universe_snapshot,
    rule_hash,
    save_aliases,
    write_universe_snapshot,
)
from trade.research.data import load_rules

_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _month_list(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(start, "%Y-%m")
    end_dt = datetime.strptime(end, "%Y-%m")
    months: list[str] = []
    year, month = start_dt.year, start_dt.month
    while (year, month) <= (end_dt.year, end_dt.month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def _month_start_ms(month: str) -> int:
    year, mon = (int(part) for part in month.split("-"))
    return int(datetime(year, mon, 1, tzinfo=UTC).timestamp() * 1000)


def _previous_month(month: str) -> str:
    year, mon = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def _load_daily(root: Path, symbol: str) -> pd.DataFrame:
    frames = [
        pq.read_table(path, columns=["open_time", "close", "quote_volume"]).to_pandas()
        for path in sorted((root / symbol).glob("*.parquet"))
    ]
    frame = pd.concat(frames, ignore_index=True)
    return frame.sort_values("open_time").drop_duplicates("open_time", keep="last")


def _quality_table(
    klines_1h: Path, symbols: Sequence[str], months: Sequence[str], threshold: float
) -> pd.DataFrame:
    """R 前一个自然月的 1h 覆盖率门槛（缺失比例 ≤ threshold）。"""
    rows: list[dict[str, object]] = []
    for month in months:
        previous = _previous_month(month)
        year, mon = (int(part) for part in previous.split("-"))
        expected = monthrange(year, mon)[1] * 24
        rebalance_ms = _month_start_ms(month)
        for symbol in symbols:
            path = klines_1h / symbol / f"{previous}.parquet"
            actual = int(pq.ParquetFile(path).metadata.num_rows) if path.is_file() else 0
            missing_ratio = 1.0 - actual / expected
            rows.append(
                {
                    "symbol": symbol,
                    "window_end_ms": rebalance_ms,
                    "missing_ratio": missing_ratio,
                    "qualified": missing_ratio <= threshold,
                }
            )
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    """build_universe_hist.py 入口。"""
    parser = argparse.ArgumentParser(description="重建研究期月度选币快照（docs/13 §1）")
    parser.add_argument("--start", default="2020-01")
    parser.add_argument("--end", default="2026-07")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--rules", default="artifacts/exchange_info/rules.json")
    parser.add_argument("--out", default="data/universe_hist")
    parser.add_argument("--missing-ratio-max", type=float, default=0.01)
    args = parser.parse_args(argv)

    data_root = _resolve(args.data_root)
    cfg: UniverseConfig = load_universe_config(_resolve(args.universe))
    rules: dict[str, SymbolMeta] = load_rules(_resolve(args.rules))
    klines_1d = data_root / "klines_1d"
    klines_1h = data_root / "klines_1h"
    symbols = sorted(path.name for path in klines_1d.iterdir() if path.is_dir())
    if not symbols:
        raise SystemExit(f"没有 1d 数据：{klines_1d}（先运行 scripts/fetch_history.py）")
    missing_rules = sorted(symbol for symbol in symbols if symbol not in rules)
    # 缺少交易规则的币（多为已下架且逐笔数据已清理的老合约）无法取整与计算可交易性，
    # 统一标记 excluded，不进入 U(R)；数量与名单写入 summary.json 与 decisions.md。
    for symbol in missing_rules:
        rules[symbol] = SymbolMeta(symbol=symbol, excluded=True)
    if missing_rules:
        print(f"缺少交易规则、标记 excluded 的币 {len(missing_rules)} 个：{missing_rules[:10]}…")
    daily = {symbol: _load_daily(klines_1d, symbol) for symbol in symbols}
    months = _month_list(args.start, args.end)
    quality = _quality_table(klines_1h, symbols, months, args.missing_ratio_max)
    out_dir = _resolve(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    aliases = load_aliases(out_dir / "aliases.json")
    summary: dict[str, int] = {}
    for month in months:
        path = out_dir / f"{month}.json"
        if path.is_file():
            snapshot = read_universe_snapshot(path)
            if snapshot.rule_hash != rule_hash(cfg):
                raise SystemExit(f"{month} 快照规则哈希不同，拒绝覆盖（03 §4.6）")
            summary[month] = len(snapshot.coins)
            continue
        snapshot = build_universe(_month_start_ms(month), daily, rules, cfg, quality_cache=quality)
        snapshot, aliases = assign_aliases(snapshot, aliases)
        write_universe_snapshot(path, snapshot)
        summary[month] = len(snapshot.coins)
        print(f"{month}: {len(snapshot.coins)} 个币")
    save_aliases(out_dir / "aliases.json", aliases)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {"months": summary, "missing_rules": missing_rules},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    counts = [count for count in summary.values()]
    print(f"完成 {len(summary)} 个月；入选数最少 {min(counts)}、最多 {max(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
