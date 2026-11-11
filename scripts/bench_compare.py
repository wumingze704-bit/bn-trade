"""回测基准快照对拍：逐文件比较两次运行的结果（红线 15 的等价性判据）。

规格：docs/06-回测引擎.md §3.4；基准快照由 `scripts/bench_backtest.py` 产出。

比较规则（与 AGENTS.md §6 红线 15 一致）：
- 整数、字符串、布尔字段必须完全相同；
- 浮点字段 `rtol=1e-12`、`atol=1e-9`（NaN 与 NaN 视为相同）；
- 行数、列集合、列类型不同即判为差异；
- `timing.json`、`profile.txt` 是测量产物（耗时每次不同），不参与比较。

用法（仓库根目录）：
    python scripts/bench_compare.py artifacts/bench/baseline_5x1 artifacts/bench/_repeat
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

# 测量产物：每次运行都不同，不参与数值对拍
IGNORED_FILES = frozenset({"timing.json", "profile.txt"})

# 浮点比较容差（红线 15）
RTOL = 1e-12
ATOL = 1e-9

# 每条差异的展示上限
MAX_DIFFS = 20


def _is_nan(value: object) -> bool:
    """判断标量是否为 NaN（None 与字符串返回 False）。"""
    return isinstance(value, float) and np.isnan(value)


def _floats_close(left: float, right: float) -> bool:
    """浮点比较：先处理 NaN 与完全相等，再按红线 15 的容差。"""
    if _is_nan(left) and _is_nan(right):
        return True
    if left == right:  # 含 ±inf 与 ±0.0
        return True
    return bool(np.isclose(left, right, rtol=RTOL, atol=ATOL))


def _scalar_equal(left: object, right: object) -> bool:
    """非浮点标量比较：NaN 与 NaN 相同，其余严格相等。"""
    if _is_nan(left) or _is_nan(right):
        return _is_nan(left) and _is_nan(right)
    return bool(left == right)


def _scalar_repr(value: object) -> str:
    """差异信息里的取值文本：numpy 标量先转成 Python 标量。"""
    if isinstance(value, np.generic):
        return repr(value.item())
    return repr(value)


def _compare_column(old: pd.Series, new: pd.Series, name: str, column: str) -> list[str]:
    """比较单列取值；整型/字符串严格相等，浮点按容差。"""
    diffs: list[str] = []
    if old.dtype.kind == "f":
        old_values = old.to_numpy(dtype="float64")
        new_values = new.to_numpy(dtype="float64")
        both_nan = np.isnan(old_values) & np.isnan(new_values)
        close = np.isclose(old_values, new_values, rtol=RTOL, atol=ATOL) | both_nan
        bad = np.nonzero(~close)[0]
    else:
        old_values = old.to_numpy(dtype=object)
        new_values = new.to_numpy(dtype=object)
        equal = [_scalar_equal(a, b) for a, b in zip(old_values, new_values, strict=True)]
        bad = np.nonzero([not flag for flag in equal])[0]
    for index in bad[: max(0, MAX_DIFFS - len(diffs))]:
        row = int(index)
        diffs.append(
            f"{name}: 第 {row} 行 {column} 不同"
            f"（基准 {_scalar_repr(old.iloc[row])}，新 {_scalar_repr(new.iloc[row])}）"
        )
    return diffs


def _compare_frame(left: pd.DataFrame, right: pd.DataFrame, name: str) -> list[str]:
    """逐列比较两个 DataFrame，返回差异描述（最多 MAX_DIFFS 条）。"""
    if left.shape[0] != right.shape[0]:
        return [f"{name}: 行数不同（基准 {left.shape[0]}，新 {right.shape[0]}）"]
    if list(left.columns) != list(right.columns):
        return [f"{name}: 列不同（基准 {list(left.columns)}，新 {list(right.columns)}）"]
    diffs: list[str] = []
    for column in left.columns:
        old = left[column]
        new = right[column]
        if old.dtype.kind != new.dtype.kind:
            diffs.append(f"{name}: 列 {column} 类型不同（{old.dtype} vs {new.dtype}）")
            continue
        if len(diffs) >= MAX_DIFFS:
            break
        diffs.extend(_compare_column(old, new, name, column))
    return diffs[:MAX_DIFFS]


def _compare_json(old: Any, new: Any, name: str, path: str = "") -> list[str]:
    """递归比较 JSON 值（dict/list/标量）。"""
    label = f"{name}{path}"
    if isinstance(old, dict) and isinstance(new, dict):
        diffs: list[str] = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                diffs.append(f"{label}: 键 {key} 只在一边出现")
                continue
            diffs.extend(_compare_json(old[key], new[key], name, f"{path}.{key}"))
            if len(diffs) >= MAX_DIFFS:
                break
        return diffs[:MAX_DIFFS]
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{label}: 列表长度不同（{len(old)} vs {len(new)}）"]
        diffs = []
        for index, (left, right) in enumerate(zip(old, new, strict=True)):
            diffs.extend(_compare_json(left, right, name, f"{path}[{index}]"))
            if len(diffs) >= MAX_DIFFS:
                break
        return diffs[:MAX_DIFFS]
    if isinstance(old, float) and isinstance(new, float):
        return [] if _floats_close(old, new) else [f"{label}: 浮点不同（{old!r} vs {new!r}）"]
    if type(old) is not type(new):
        return [f"{label}: 类型不同（{type(old).__name__} vs {type(new).__name__}）"]
    return [] if _scalar_equal(old, new) else [f"{label}: 值不同（{old!r} vs {new!r}）"]


def _compare_file(baseline: Path, candidate: Path, name: str) -> list[str]:
    """按扩展名分派比较（parquet / json / 其他文本）。"""
    if baseline.suffix == ".parquet":
        return _compare_frame(pd.read_parquet(baseline), pd.read_parquet(candidate), name)
    if baseline.suffix == ".json":
        old = json.loads(baseline.read_text(encoding="utf-8"))
        new = json.loads(candidate.read_text(encoding="utf-8"))
        return _compare_json(old, new, name)
    old_text = baseline.read_text(encoding="utf-8")
    new_text = candidate.read_text(encoding="utf-8")
    if old_text != new_text:
        return [f"{name}: 文本不同"]
    return []


def _relative_files(root: Path) -> set[str]:
    """列出参与对拍的相对路径（跳过测量产物）。"""
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in IGNORED_FILES
    }


def main(argv: list[str] | None = None) -> int:
    """脚本入口：比较两个基准目录，差异非空时以 1 退出。"""
    parser = argparse.ArgumentParser(description="比较两个回测基准目录的结果快照")
    parser.add_argument("baseline", type=Path, help="基准目录（标准答案）")
    parser.add_argument("candidate", type=Path, help="新结果目录")
    args = parser.parse_args(argv)
    for path in (args.baseline, args.candidate):
        if not path.is_dir():
            raise SystemExit(f"目录不存在：{path}")

    baseline_files = _relative_files(args.baseline)
    candidate_files = _relative_files(args.candidate)
    diffs: list[str] = []
    for missing in sorted(baseline_files - candidate_files):
        diffs.append(f"{missing}: 新结果缺少该文件")
    for extra in sorted(candidate_files - baseline_files):
        diffs.append(f"{extra}: 新结果多出该文件")

    compared = 0
    for name in sorted(baseline_files & candidate_files):
        if len(diffs) >= MAX_DIFFS:
            break
        compared += 1
        diffs.extend(_compare_file(args.baseline / name, args.candidate / name, name))

    for line in diffs[:MAX_DIFFS]:
        print(line)
    if diffs:
        print(
            f"\n对拍未通过：{len(diffs)} 处差异（只显示前 {MAX_DIFFS} 处），"
            f"已比较 {compared} 个文件"
        )
        return 1
    print(f"对拍通过：{compared} 个文件逐字段一致（rtol={RTOL}, atol={ATOL}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
