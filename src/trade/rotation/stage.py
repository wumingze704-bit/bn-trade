"""阶段守卫：research / confirm / final 的评估区间与前置文件检查。

规格：docs/11-轮动策略.md §7

守卫条件写死在代码里，不能由命令行参数改上限（研究任务指令第四部分）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

STAGES = ("research", "confirm", "final")
_MS_DAY = 86_400_000


def _month_start(year: int, month: int) -> int:
    """返回某 UTC 月 1 日 00:00 的毫秒时间戳。"""
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


# 月份编号：M1 = 2024-10 … M12 = 2025-09，M13 = 2025-10 … M22 = 2026-07，
# M23 = 2026-08、M24 = 2026-09（11 §7）
M1_START_MS = _month_start(2024, 10)
M2_START_MS = _month_start(2024, 11)
M12_END_MS = _month_start(2025, 10)
M22_END_MS = _month_start(2026, 8)
HOLDOUT_START_MS = _month_start(2026, 8)
FINAL_EVAL_START_MS = _month_start(2026, 8)
FINAL_EVAL_END_MS = _month_start(2026, 12)
FINAL_WARMUP_START_MS = _month_start(2026, 7)

# 研究阶段预热：M1 起点前 30 天（base.yaml data_window.warmup_days 同口径）
RESEARCH_WARMUP_START_MS = M1_START_MS - 30 * _MS_DAY

_VERDICT_RE = re.compile(
    r"^[>\s*`-]*(?:\*\*)?结论(?:\*\*)?[：:]\s*[*`]*\s*(PASS|FAIL|BLOCKED|NOT_READY|CRASHED)",
    re.MULTILINE,
)
_FINAL_RUN_ID = "rot_final"


@dataclass(frozen=True)
class StageWindow:
    """某 stage 允许的评估区间与封存集开关。"""

    stage: str
    eval_start_ms: int
    eval_end_ms: int
    warmup_start_ms: int
    allow_holdout: bool


def read_verdict(path: Path) -> str | None:
    """读取阶段结论文件里的 `结论：PASS|FAIL|BLOCKED|NOT_READY|CRASHED`。

    Args:
        path: 结论文档路径（docs/rotation/phase1.md、phase2.md）。

    Returns:
        结论字符串；文件不存在或没有结论行时返回 None。
    """
    if not path.is_file():
        return None
    match = _VERDICT_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).upper() if match else None


def final_run_recorded(path: Path) -> bool:
    """检查 FINAL_RUNS.log 中是否已有 run_id=rot_final 的完成记录（11 §7）。"""
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("run_id") == _FINAL_RUN_ID:
                if str(payload.get("status", "")).lower() in ("completed", "done", "success"):
                    return True
                continue
        if f"run_id={_FINAL_RUN_ID}" in text and re.search(
            r"status=(completed|done|success)", text
        ):
            return True
    return False


def stage_window(stage: str, *, docs_dir: Path, artifact_root: Path) -> StageWindow:
    """返回某 stage 的评估区间，并检查前置文件（11 §7）。

    Args:
        stage: research / confirm / final。
        docs_dir: docs 目录（读取 docs/rotation/phase1.md、phase2.md）。
        artifact_root: artifacts 目录（读取 reports/FINAL_RUNS.log）。

    Returns:
        StageWindow。

    Raises:
        ValueError: stage 未知、前置结论不满足或封存集已运行过。
    """
    if stage not in STAGES:
        raise ValueError(f"未知 stage：{stage}（只能是 {STAGES}）")
    if stage == "research":
        return StageWindow(
            stage=stage,
            eval_start_ms=M2_START_MS,
            eval_end_ms=M12_END_MS,
            warmup_start_ms=RESEARCH_WARMUP_START_MS,
            allow_holdout=False,
        )
    phase1 = read_verdict(docs_dir / "rotation" / "phase1.md")
    phase2 = read_verdict(docs_dir / "rotation" / "phase2.md")
    if stage == "confirm":
        if phase1 != "PASS":
            raise ValueError("confirm 阶段要求 docs/rotation/phase1.md 结论为 PASS（11 §7）")
        if phase2 == "PASS":
            raise ValueError("docs/rotation/phase2.md 已为 PASS，不得再跑 confirm（11 §7）")
        return StageWindow(
            stage=stage,
            eval_start_ms=M2_START_MS,
            eval_end_ms=M22_END_MS,
            warmup_start_ms=RESEARCH_WARMUP_START_MS,
            allow_holdout=False,
        )
    if phase2 != "PASS":
        raise ValueError("final 阶段要求 docs/rotation/phase2.md 结论为 PASS（11 §7）")
    if final_run_recorded(artifact_root / "reports" / "FINAL_RUNS.log"):
        raise ValueError("FINAL_RUNS.log 已有 run_id=rot_final 的完成记录，不得重跑（11 §7）")
    return StageWindow(
        stage=stage,
        eval_start_ms=FINAL_EVAL_START_MS,
        eval_end_ms=FINAL_EVAL_END_MS,
        warmup_start_ms=FINAL_WARMUP_START_MS,
        allow_holdout=True,
    )


def check_window(window: StageWindow, start_ms: int, end_ms: int) -> None:
    """校验请求的评估区间不越过 stage 上限（11 §7）。

    Raises:
        ValueError: 区间越界或与封存集重叠。
    """
    if start_ms < window.eval_start_ms:
        raise ValueError(f"{window.stage} 阶段的评估起点不得早于 {window.eval_start_ms}（11 §7）")
    if end_ms > window.eval_end_ms:
        raise ValueError(f"{window.stage} 阶段的评估终点不得超过 {window.eval_end_ms}（11 §7）")
    if end_ms > HOLDOUT_START_MS and not window.allow_holdout:
        raise ValueError(f"{window.stage} 阶段不得读取封存集（end_ms={end_ms}，11 §7）")
