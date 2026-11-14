"""bookTicker 点差画像：统计每币相对点差的 P50/P75/P95（06 §4.3）。

规格：docs/06-回测引擎.md §4.3

输入 `artifacts/live/book/{alias}/{YYYYMMDD}.parquet`（由 `scripts/record_book_ticker.py`
在服务器录制，字段 E/bid/ask/bid_qty/ask_qty），输出
`artifacts/costs/spread_profile.parquet` 与 `spread_profile.md`。

只做测量与报告：`base` 情景不变；`spread_measured` 情景由 `scripts/backtest.py`
读取本脚本产出的 parquet 构造（每币 half_spread_min = 该币信号分钟 P75 / 2）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

# 相对点差的分位（06 §4.3）
_QUANTILES = (0.50, 0.75, 0.95)


def relative_spread(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """相对点差 `(ask − bid) / mid`（无量纲，0.0002 = 2bp；06 §4.3）。"""
    bid = np.asarray(bid, dtype="float64")
    ask = np.asarray(ask, dtype="float64")
    mid = 0.5 * (bid + ask)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mid > 0.0, (ask - bid) / mid, np.nan)


def spread_quantiles(spread: np.ndarray) -> dict[str, float]:
    """相对点差的 P50/P75/P95（忽略非有限值）；无有效样本时返回 NaN。"""
    values = np.asarray(spread, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"p50": float("nan"), "p75": float("nan"), "p95": float("nan")}
    quantiles = np.quantile(values, _QUANTILES)
    return {"p50": float(quantiles[0]), "p75": float(quantiles[1]), "p95": float(quantiles[2])}


def half_spread_overrides(profile: pd.DataFrame) -> dict[str, float]:
    """由点差画像构造每币最小半价差 = 该币信号分钟 P75 / 2（06 §4.3）。

    Args:
        profile: 至少含 `alias` 与 `signal_p75` 两列的画像表。

    Returns:
        alias → 最小半价差（比例）。

    Raises:
        ValueError: 画像为空，或 `signal_p75` 全部为 NaN（无法确定口径）。
    """
    if profile.empty:
        raise ValueError("点差画像为空，无法构造 spread_measured 情景（06 §4.3）")
    missing = [name for name in ("alias", "signal_p75") if name not in profile.columns]
    if missing:
        raise ValueError(f"点差画像缺少列 {missing}（06 §4.3）")
    fallback = float(profile["signal_p75"].median())
    if not np.isfinite(fallback):
        raise ValueError("点差画像的 signal_p75 全部为 NaN，无法构造 spread_measured 情景")
    out: dict[str, float] = {}
    for row in profile.itertuples(index=False):
        value = float(row.signal_p75)
        out[str(row.alias)] = 0.5 * (value if np.isfinite(value) else fallback)
    return out


def load_half_spread_overrides(path: Path, aliases: Sequence[str]) -> dict[str, float]:
    """读取点差画像并给出全部交易币的半价差覆盖（06 §4.3）。

    Args:
        path: `artifacts/costs/spread_profile.parquet`。
        aliases: 本次回测涉及的币别名。

    Returns:
        alias → 最小半价差；未录到的币用全币 `signal_p75` 中位数 / 2。

    Raises:
        ValueError: 文件不存在或缺少必需列（不静默回退到 base 口径）。
    """
    if not path.is_file():
        raise ValueError(f"缺少点差画像文件：{path}（先运行 scripts/spread_profile.py）")
    frame = pd.read_parquet(path)
    base = half_spread_overrides(frame)
    fallback = 0.5 * float(frame["signal_p75"].median())
    return {alias: base.get(alias, fallback) for alias in aliases}


def _book_files(book_root: Path, alias: str) -> list[Path]:
    """某币的 bookTicker 录制文件（按日分段，文件名 `{YYYYMMDD}[-{HHMMSS}].parquet`）。"""
    return sorted((book_root / alias).glob("*.parquet"))


def _load_book(book_root: Path, dir_names: Sequence[str]) -> pd.DataFrame:
    """读取若干录制目录下全部可读的 bookTicker（E/bid/ask；按事件时间排序去重）。

    录制进程可能正在写某个分段文件；无法解析的文件跳过并在 stderr 提示，不静默丢失。
    """
    files: list[Path] = []
    for name in dir_names:
        files.extend(sorted((book_root / name).glob("*.parquet")))
    if not files:
        raise ValueError(f"没有 bookTicker 录制：{book_root / dir_names[0]}")
    frames: list[pd.DataFrame] = []
    skipped: list[str] = []
    for path in files:
        try:
            frames.append(pd.read_parquet(path, columns=["E", "bid", "ask"]))
        except (OSError, ValueError):  # pyarrow 对未完成文件抛 ArrowInvalid(ValueError)
            skipped.append(path.name)
    if skipped:
        print(f"[跳过未完成的录制文件] {dir_names[0]} {len(skipped)} 个", file=sys.stderr)
    if not frames:
        raise ValueError(f"bookTicker 录制文件都不可读：{book_root / dir_names[0]}")
    frame = pd.concat(frames, ignore_index=True)
    return frame.sort_values("E").drop_duplicates("E").reset_index(drop=True)


def build_profile(
    book_root: Path,
    groups: Mapping[str, Sequence[str]],
    signal_minutes: Mapping[str, np.ndarray],
) -> pd.DataFrame:
    """统计每别名全部分钟与信号分钟的相对点差分位（06 §4.3）。

    Args:
        book_root: `artifacts/live/book/`。
        groups: alias → 该别名的录制目录名列表（录制目录可能是 symbol，也可能是 alias）。
        signal_minutes: alias → 该币信号分钟的毫秒时间戳数组（预过滤样本）。

    Returns:
        每别名一行：alias、minutes、all_p50/p75/p95、signal_n、signal_p50/p75/p95。
    """
    rows: list[dict[str, object]] = []
    for alias, dir_names in groups.items():
        book = _load_book(book_root, dir_names)
        spread = relative_spread(book["bid"].to_numpy(), book["ask"].to_numpy())
        all_q = spread_quantiles(spread)
        minute = (book["E"].to_numpy(dtype="int64") // 60_000) * 60_000
        wanted = np.asarray(signal_minutes.get(alias, np.empty(0)), dtype="int64")
        in_signal = (
            np.isin(minute, wanted) if wanted.size else np.zeros(minute.shape[0], dtype=bool)
        )
        signal_q = spread_quantiles(spread[in_signal])
        rows.append(
            {
                "alias": alias,
                "minutes": int(minute.shape[0]),
                "all_p50": all_q["p50"],
                "all_p75": all_q["p75"],
                "all_p95": all_q["p95"],
                "signal_n": int(in_signal.sum()),
                "signal_p50": signal_q["p50"],
                "signal_p75": signal_q["p75"],
                "signal_p95": signal_q["p95"],
            }
        )
    return pd.DataFrame(rows)


def write_profile(frame: pd.DataFrame, out_dir: Path, *, recorded_days: Sequence[str]) -> None:
    """写 `spread_profile.parquet` 与 markdown 摘要（06 §4.3）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_dir / "spread_profile.parquet", index=False)
    first = recorded_days[0] if recorded_days else "unknown"
    last = recorded_days[-1] if recorded_days else "unknown"
    lines = [
        "# 实测点差画像（06 §4.3）",
        "",
        f"- 录制日：{first} 至 {last}（UTC）",
        "- 录制期与测试段不是同一时期，只能作为量级参考。",
        "",
        "| 币 | 分钟数 | 全部 P50 | P75 | P95 | 信号分钟数 | 信号 P50 | P75 | P95 |",
        "| :-- | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.alias} | {row.minutes} | {row.all_p50 * 1e4:.2f}bp "
            f"| {row.all_p75 * 1e4:.2f}bp | {row.all_p95 * 1e4:.2f}bp | {row.signal_n} "
            f"| {row.signal_p50 * 1e4:.2f}bp | {row.signal_p75 * 1e4:.2f}bp "
            f"| {row.signal_p95 * 1e4:.2f}bp |"
        )
    (out_dir / "spread_profile.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _signal_minutes_from_parquet(path: Path | None) -> dict[str, np.ndarray]:
    """从 signals.parquet 取每币信号分钟（`is_sample = 1` 的行；06 §4.3）。

    Args:
        path: 信号文件；None 表示没有信号分钟样本，只统计全部分钟。

    Returns:
        symbol/alias → 信号分钟毫秒时间戳数组。
    """
    if path is None:
        return {}
    frame = pd.read_parquet(path)
    if "open_time" not in frame.columns or "symbol" not in frame.columns:
        raise ValueError(f"signals 缺少 symbol/open_time 列：{path}")
    if "is_sample" in frame.columns:
        frame = frame[frame["is_sample"] == 1]
    return {
        str(symbol): group["open_time"].to_numpy(dtype="int64")
        for symbol, group in frame.groupby("symbol", sort=False)
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="bookTicker 点差画像（06 §4.3）")
    parser.add_argument("--book-root", type=Path, default=Path("artifacts/live/book"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/costs"))
    parser.add_argument(
        "--signals",
        type=Path,
        default=None,
        help="信号文件（取 is_sample=1 的分钟作为“信号分钟”；缺省只统计全部分钟）",
    )
    parser.add_argument(
        "--aliases-file",
        type=Path,
        default=None,
        help="data/universe/aliases.json；把 symbol 命名的录制目录归并到 alias（C001…）",
    )
    return parser


def _group_dirs(book_root: Path, aliases_path: Path | None) -> dict[str, list[str]]:
    """把录制目录按别名归并（目录名可能是 symbol，也可能是 alias）。"""
    dirs = sorted(path.name for path in book_root.iterdir() if path.is_dir())
    if not dirs:
        raise ValueError(f"bookTicker 录制目录为空：{book_root}")
    if aliases_path is None:
        return {name: [name] for name in dirs}
    symbol_to_alias: dict[str, str] = json.loads(aliases_path.read_text(encoding="utf-8"))
    known_aliases = set(symbol_to_alias.values())
    groups: dict[str, list[str]] = {}
    for name in dirs:
        alias = name if name in known_aliases else symbol_to_alias.get(name, name)
        groups.setdefault(alias, []).append(name)
    return groups


def main(argv: list[str] | None = None) -> int:
    """入口：读录制 → 统计分位 → 写 parquet 与 markdown。"""
    root = Path(__file__).resolve().parents[1]
    args = build_parser().parse_args(argv)

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    book_root = resolve(args.book_root)
    if not book_root.is_dir():
        raise ValueError(f"没有 bookTicker 录制目录：{book_root}")
    groups = _group_dirs(
        book_root, resolve(args.aliases_file) if args.aliases_file is not None else None
    )
    signal_minutes = _signal_minutes_from_parquet(
        resolve(args.signals) if args.signals is not None else None
    )
    profile = build_profile(book_root, groups, signal_minutes)
    days = sorted(
        {
            path.stem.split("-")[0]
            for dirs in groups.values()
            for name in dirs
            for path in _book_files(book_root, name)
        }
    )
    write_profile(profile, resolve(args.out), recorded_days=days)
    print(f"点差画像：{len(profile)} 个币 → {resolve(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
