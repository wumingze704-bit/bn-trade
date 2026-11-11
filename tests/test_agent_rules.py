"""Cline 规则摘要与 AGENTS.md 的一致性校验。

规格：AGENTS.md §1（Cline 使用 `.clinerules/` 目录，其内容是 AGENTS.md 的摘要，
冲突时以 AGENTS.md 为准）
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
CORE_RULES_PATH = REPO_ROOT / ".clinerules" / "00-core.md"
DOCS_RULES_PATH = REPO_ROOT / ".clinerules" / "10-docs.md"
QUANT_RULES_PATH = REPO_ROOT / ".clinerules" / "20-quant.md"
CLINEIGNORE_PATH = REPO_ROOT / ".clineignore"

# AGENTS.md §6 的红线编号（1~15）与本文件要求再列一次的编号
RED_LINE_NUMBERS = tuple(range(1, 16))
QUANT_RED_LINE_NUMBERS = (1, 2, 3, 4, 12, 13, 15)
QUALITY_COMMAND_COUNT = 5

NUMBERED_ITEM_RE = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


def read_rules_text(path: Path) -> str:
    """按 UTF-8 读取规范或规则文件。

    Args:
        path: 文件路径。

    Returns:
        文件全文。

    Raises:
        FileNotFoundError: 文件不存在。缺文件直接失败，不静默跳过（AGENTS.md §6 红线 13）。
    """
    return path.read_text(encoding="utf-8")


def section_body(text: str, heading_keyword: str) -> list[str]:
    """取出标题中含 heading_keyword 的小节正文。

    Args:
        text: 文件全文。
        heading_keyword: 二或三级标题中必须出现的片段。

    Returns:
        小节正文行（不含标题本身，到下一个同级或更高级标题为止）。

    Raises:
        AssertionError: 找不到该小节，说明规则文件结构被改坏。
    """
    lines = text.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) >= 2 and heading_keyword in match.group(2):
            start = index + 1
            level = len(match.group(1))
            break
    assert start is not None, f"未找到标题含“{heading_keyword}”的小节"
    body: list[str] = []
    for line in lines[start:]:
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) <= level:
            break
        body.append(line)
    return body


def numbered_items(lines: list[str]) -> dict[int, str]:
    """按行首“N.”切分编号条目，去掉首尾空白。

    Args:
        lines: 小节正文行。

    Returns:
        编号 → 条目文本。
    """
    items: dict[int, str] = {}
    for line in lines:
        match = NUMBERED_ITEM_RE.match(line)
        if match:
            items[int(match.group(1))] = match.group(2).strip()
    return items


def fenced_lines(lines: list[str]) -> list[str]:
    """取出围栏代码块内的非空行（去掉围栏与行首空白）。

    Args:
        lines: 小节正文行。

    Returns:
        代码块内的非空行，按出现顺序。
    """
    block: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and line.strip():
            block.append(line.strip())
    return block


def table_rows(lines: list[str]) -> list[tuple[str, ...]]:
    """把 Markdown 表格行切成单元格元组，忽略对齐用的空白。

    Args:
        lines: 小节正文行。

    Returns:
        每行的单元格元组列表（含表头与分隔行）。
    """
    rows: list[tuple[str, ...]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        rows.append(tuple(cell.strip() for cell in stripped.strip("|").split("|")))
    return rows


def agents_red_lines() -> dict[int, str]:
    """读取 AGENTS.md §6 的红线条目。

    Returns:
        红线编号 → 条目文本。
    """
    return numbered_items(section_body(read_rules_text(AGENTS_PATH), "量化专项红线"))


def test_核心规则照抄十五条红线() -> None:
    """00-core.md 的红线必须与 AGENTS.md §6 逐条完全相等（不改写、不删减）。"""
    agents = agents_red_lines()
    core = numbered_items(section_body(read_rules_text(CORE_RULES_PATH), "红线 1~15"))
    assert tuple(sorted(agents)) == RED_LINE_NUMBERS
    assert core == agents


def test_量化规则文件再列的红线与AGENTS一致() -> None:
    """20-quant.md 再列的红线必须与 AGENTS.md 对应条目完全相等。"""
    agents = agents_red_lines()
    quant = numbered_items(
        section_body(read_rules_text(QUANT_RULES_PATH), "写代码时最容易被忽略的红线")
    )
    assert quant == {number: agents[number] for number in QUANT_RED_LINE_NUMBERS}


def test_核心规则包含五条质量命令() -> None:
    """00-core.md 必须包含 AGENTS.md §8 的 5 条命令原文，按原顺序。"""
    agents_commands = fenced_lines(section_body(read_rules_text(AGENTS_PATH), "8. 质量检查命令"))
    core_commands = fenced_lines(section_body(read_rules_text(CORE_RULES_PATH), "质量检查命令"))
    assert len(agents_commands) == QUALITY_COMMAND_COUNT
    assert core_commands == agents_commands


def test_文档规则包含红线11与文档同步对照表() -> None:
    """10-docs.md 必须包含红线 11 原文与 §5.1 对照表原文。"""
    docs_rules = read_rules_text(DOCS_RULES_PATH)
    assert agents_red_lines()[11] in docs_rules

    agents_rows = table_rows(section_body(read_rules_text(AGENTS_PATH), "文档同步对照表"))
    docs_rows = table_rows(section_body(docs_rules, "文档同步对照表"))
    assert agents_rows
    assert docs_rows == agents_rows


def test_clineignore排除数据大文件与密钥() -> None:
    """.clineignore 必须排除封存集数据、制品目录与密钥类文件。"""
    entries = {
        line.strip()
        for line in read_rules_text(CLINEIGNORE_PATH).splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert {"data/", "artifacts/", "*.pem", ".env"} <= entries
