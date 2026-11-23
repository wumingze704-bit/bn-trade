"""阶段守卫（docs/13 §8）。

- `--stage research`：区间必须落在 [research_start, research_end] 内。
- `--stage final`：只评估分配给指定候选、状态为 running 的窗口，且当前 UTC 时间
  ≥ available_after、最终检验结果文件不存在或结论为 WAITING / NOT_READY。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml


class StageError(RuntimeError):
    """阶段守卫拒绝本次运行。"""


def _ms(day: str) -> int:
    parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def read_ledger(path: Path) -> dict[str, object]:
    """读取总账（只读）。"""
    if not path.is_file():
        raise StageError(f"总账不存在：{path}")
    try:
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise StageError(f"总账不是有效 YAML：{path}：{error}") from error
    if not isinstance(values, dict):
        raise StageError(f"总账必须是 YAML 映射：{path}")
    return values


def enforce_research(*, ledger_path: Path, start_ms: int, end_ms: int) -> tuple[int, int]:
    """`--stage research` 守卫：返回 (research_start_ms, research_end_ms)。"""
    ledger = read_ledger(ledger_path)
    try:
        start = _ms(str(ledger["research_start"]))
        end = _ms(str(ledger["research_end"])) + 86_400_000
    except (KeyError, ValueError) as error:
        raise StageError(f"总账缺少有效的 research_start / research_end：{error}") from error
    if start_ms < start or end_ms > end:
        raise StageError(f"研究区间 [{start_ms}, {end_ms}) 超出总账 [{start}, {end})（docs/13 §8）")
    return start, end


def enforce_final(
    *,
    ledger_path: Path,
    candidate: str,
    window: str,
    final_path: Path,
    now_ms: int | None = None,
) -> tuple[int, int]:
    """`--stage final` 守卫：返回窗口 (start_ms, end_ms)。

    Raises:
        StageError: 窗口不是 running、未到 available_after、结果已存在或结论不是
            WAITING / NOT_READY、候选与窗口不匹配。
    """
    ledger = read_ledger(ledger_path)
    windows = ledger.get("final_windows", [])
    if not isinstance(windows, list):
        raise StageError("总账 final_windows 必须是列表")
    target = next(
        (item for item in windows if isinstance(item, dict) and item.get("id") == window), None
    )
    if target is None:
        raise StageError(f"总账没有窗口 {window}")
    if target.get("status") != "running":
        raise StageError(f"窗口 {window} 状态为 {target.get('status')}，只有 running 可检验")
    if target.get("candidate") != candidate:
        raise StageError(f"窗口 {window} 分配给 {target.get('candidate')}，不是 {candidate}")
    available = _ms(str(target["available_after"]))
    current = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    if current < available:
        raise StageError(f"窗口 {window} 在 {target['available_after']} 之后才可检验")
    if final_path.is_file():
        first = final_path.read_text(encoding="utf-8").splitlines()[0].strip()
        if first not in {"结论：WAITING", "结论：NOT_READY"}:
            raise StageError(f"窗口 {window} 已有结论：{first}（不得重跑，红线 2）")
    return _ms(str(target["start"])), _ms(str(target["end"])) + 86_400_000
