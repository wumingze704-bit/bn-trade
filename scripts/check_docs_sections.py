"""设计书精简的一致性校验脚本。

规格：AGENTS.md §5.2（设计书定版、自包含，只写当前真实行为与规格）。

用法：
    python scripts/check_docs_sections.py                     # 校验 docs 下全部设计文档
    python scripts/check_docs_sections.py docs/06-回测引擎.md  # 只校验指定文档
    python scripts/check_docs_sections.py --baseline-dir <目录>  # 用指定目录的快照作基线

检查项：
    a) 标题（编号 + 标题文本 + 层级）与基线完全一致；
    b) 基线中的数字 token 在精简后不得无故消失，消失的必须出现在 docs/records.md；
    c) configs/*.yaml 的键名在基线文档出现过的，精简后仍须出现；
    d) 全仓库（docs/、src/、scripts/、AGENTS.md）的“文档 §章节”引用必须指向仍存在的章节。

任一项不通过时以非零退出码结束，并逐行输出问题。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC_ID_RE = re.compile(r"^(\d{2})-.*\.md$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S+)\s*(.*)$")
SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)[.、]?$")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:%|bp)?")
CONFIG_KEY_RE = re.compile(r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:|^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
DOC_REF_RE = re.compile(r"^(\d{2})$")
SECTION_REF_RE = re.compile(r"^§(\d+(?:\.\d+)*)")


def read_text(path: Path) -> str:
    """按 UTF-8 读取文本文件。

    Args:
        path: 文件路径。

    Returns:
        文件内容（保留原始换行）。

    Raises:
        SystemExit: 文件不存在或无法解码。
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # 文件缺失时直接报错，不做静默降级
        raise SystemExit(f"[错误] 无法读取 {path}: {exc}") from exc


def strip_fences(text: str) -> list[str]:
    """把围栏代码块（``` 包裹）替换为空行，避免把代码注释当成标题。

    Args:
        text: 文件全文。

    Returns:
        与原文行数相同的行列表，代码块内为空串。
    """
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append("")
            continue
        lines.append("" if in_fence else line)
    return lines


def extract_headings(text: str) -> list[tuple[int, str, str]]:
    """提取标题行。

    Args:
        text: 文件全文。

    Returns:
        (层级, 编号, 标题文本) 列表，按出现顺序；编号取标题首段，如 “5.6”“6”。
    """
    headings: list[tuple[int, str, str]] = []
    for line in strip_fences(text):
        match = HEADING_RE.match(line)
        if not match:
            continue
        hashes, first, rest = match.groups()
        num = SECTION_NUM_RE.match(first)
        if num:
            headings.append((len(hashes), num.group(1), rest.strip()))
        else:
            headings.append((len(hashes), "", f"{first} {rest}".strip()))
    return headings


def extract_sections(text: str) -> set[str]:
    """提取文档内所有章节号。

    Args:
        text: 文件全文。

    Returns:
        章节号集合，如 {"1", "3", "3.3", "5.6"}。
    """
    return {num for _, num, _ in extract_headings(text) if num}


def extract_numbers(text: str) -> set[str]:
    """提取全部数字 token（整数、小数、百分号、bp）。

    Args:
        text: 待扫描文本。

    Returns:
        数字 token 集合。
    """
    return set(NUMBER_RE.findall(text))


def config_keys(root: Path) -> set[str]:
    """收集 configs/*.yaml 中出现的全部键名。

    Args:
        root: 仓库根目录。

    Returns:
        键名集合（只取最后一段，如 `data_window.end_month` 取 `end_month`）。
    """
    keys: set[str] = set()
    for path in sorted((root / "configs").glob("*.yaml")):
        for line in strip_fences(read_text(path)):
            for match in CONFIG_KEY_RE.finditer(line):
                keys.add(match.group(1) or match.group(2))
    return keys


def load_baseline(path: Path, baseline_dir: Path | None) -> str:
    """读取基线内容。

    Args:
        path: 仓库内的文档路径。
        baseline_dir: 若给出，则从该目录的同名文件读取；否则用 `git show HEAD:<路径>`。

    Returns:
        基线全文。

    Raises:
        SystemExit: 基线无法获取。
    """
    if baseline_dir is not None:
        return read_text(baseline_dir / path.name)
    rel = path.as_posix()
    result = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"[错误] 无法获取基线 {rel}: {result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout.decode("utf-8")


def check_headings(path: Path, before: str, after: str) -> list[str]:
    """比对标题序列（层级 + 编号 + 标题文本）。

    Args:
        path: 文档路径，仅用于输出。
        before: 基线全文。
        after: 精简后全文。

    Returns:
        问题描述列表，空列表表示通过。
    """
    old = extract_headings(before)
    new = extract_headings(after)
    problems: list[str] = []
    for idx in range(max(len(old), len(new))):
        o = old[idx] if idx < len(old) else None
        n = new[idx] if idx < len(new) else None
        if o == n:
            continue
        problems.append(f"{path.name}: 标题不一致（第 {idx + 1} 个）：基线 {o} / 现文 {n}")
    return problems


def check_numbers(path: Path, before: str, after: str, records_numbers: set[str]) -> list[str]:
    """检查基线中的数字 token 是否无故消失。

    Args:
        path: 文档路径，仅用于输出。
        before: 基线全文。
        after: 精简后全文。
        records_numbers: docs/records.md 中的数字 token 集合。

    Returns:
        问题描述列表，空列表表示通过。
    """
    lost = sorted(extract_numbers(before) - extract_numbers(after))
    return [
        f"{path.name}: 数字 token 丢失且未出现在 docs/records.md：{num}"
        for num in lost
        if num not in records_numbers
    ]


def check_config_keys(path: Path, before: str, after: str, keys: set[str]) -> list[str]:
    """检查基线文档中出现过的配置键名是否仍然出现。

    Args:
        path: 文档路径，仅用于输出。
        before: 基线全文。
        after: 精简后全文。
        keys: configs/*.yaml 中的全部键名。

    Returns:
        问题描述列表，空列表表示通过。
    """
    used = {key for key in keys if key_in_text(key, before)}
    return [
        f"{path.name}: 配置键名丢失：{key}" for key in sorted(used) if not key_in_text(key, after)
    ]


def key_in_text(key: str, text: str) -> bool:
    """判断配置键名是否作为独立标识符出现在文本中。

    避免 `rr`、`model` 这类短键名命中 `error`、`model_meta` 等其它单词内部。

    Args:
        key: 配置键名。
        text: 待检查文本。

    Returns:
        True 表示出现。
    """
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


def iter_scan_files(root: Path) -> list[Path]:
    """列出需要检查“§”引用的文件。

    Args:
        root: 仓库根目录。

    Returns:
        文件路径列表（docs/、src/、scripts/ 与 AGENTS.md）。
    """
    paths = [root / "AGENTS.md"]
    paths.extend(sorted((root / "docs").glob("*.md")))
    paths.extend(sorted((root / "src").rglob("*.py")))
    paths.extend(sorted((root / "scripts").glob("*.py")))
    return [path for path in paths if path.is_file()]


def refs_in_line(line: str) -> list[tuple[str | None, str]]:
    """提取一行内的“文档 §章节”引用。

    Args:
        line: 单行文本。

    Returns:
        (文档号或 None, 章节号) 列表；文档号缺省时沿用本行最近一次出现的文档号。
    """
    pattern = re.compile(r"(?:(?<![\d.])(\d{2}))?\s*§\s*(\d+(?:\.\d+)*)")
    refs: list[tuple[str | None, str]] = []
    last_doc: str | None = None
    for match in pattern.finditer(line):
        doc, section = match.group(1), match.group(2)
        if doc is not None:
            last_doc = doc
        refs.append((doc or last_doc, section))
    return refs


def check_refs(
    root: Path, now_map: dict[str, set[str]], base_map: dict[str, set[str]]
) -> tuple[list[str], list[str]]:
    """检查全仓库“文档 §章节”引用是否仍指向存在的章节。

    引用目标在基线中也不存在时，属于精简之前就有的问题，只作为警告输出。

    Args:
        root: 仓库根目录。
        now_map: 当前文档号 → 章节号集合。
        base_map: 基线文档号 → 章节号集合。

    Returns:
        (错误列表, 警告列表)，错误表示本次精简使引用失效。
    """
    errors: list[str] = []
    warnings: list[str] = []
    for path in iter_scan_files(root):
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            for doc, section in refs_in_line(line):
                if doc is None or doc not in now_map:
                    continue
                if section in now_map[doc]:
                    continue
                message = f"{rel}:{lineno}: 引用了不存在的章节 {doc} §{section}"
                if doc in base_map and section in base_map[doc]:
                    errors.append(f"{message}（基线中存在，本次精简后消失）")
                else:
                    warnings.append(f"{message}（基线中同样不存在）")
    return errors, warnings


def doc_section_map(
    root: Path, baseline_dir: Path | None, *, baseline: bool = False
) -> dict[str, set[str]]:
    """建立文档号 → 章节号集合的映射。

    Args:
        root: 仓库根目录。
        baseline_dir: 基线快照目录；为 None 时从 git HEAD 读取基线。
        baseline: True 表示读取基线内容，False 表示读取当前工作区文件。

    Returns:
        如 {"06": {"1", "3", "3.3", ...}}。
    """
    mapping: dict[str, set[str]] = {}
    for path in sorted((root / "docs").glob("*.md")):
        match = DOC_ID_RE.match(path.name)
        if not match:
            continue
        text = load_baseline(path, baseline_dir) if baseline else read_text(path)
        mapping[match.group(1)] = extract_sections(text)
    return mapping


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数（不含程序名）。

    Returns:
        进程退出码，0 表示全部检查通过。
    """
    parser = argparse.ArgumentParser(description="校验设计书精简前后的章节、数字、配置键与引用")
    parser.add_argument("files", nargs="*", help="待校验文档路径；缺省为 docs 下全部设计文档")
    parser.add_argument(
        "--baseline-dir", type=Path, default=None, help="基线快照目录（缺省用 git show HEAD）"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1], help="仓库根目录"
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    files = [Path(item) for item in args.files] or sorted(
        [
            p
            for p in (root / "docs").glob("*.md")
            if DOC_ID_RE.match(p.name) or p.name == "design.md"
        ]
    )
    files = [p if p.is_absolute() else root / p for p in files]

    records_path = root / "docs" / "records.md"
    records_numbers = extract_numbers(read_text(records_path)) if records_path.is_file() else set()
    keys = config_keys(root)
    now_map = doc_section_map(root, args.baseline_dir)
    base_map = doc_section_map(root, args.baseline_dir, baseline=True)

    problems: list[str] = []
    for path in files:
        before = load_baseline(path, args.baseline_dir)
        after = read_text(path)
        problems.extend(check_headings(path, before, after))
        problems.extend(check_numbers(path, before, after, records_numbers))
        problems.extend(check_config_keys(path, before, after, keys))
    ref_errors, ref_warnings = check_refs(root, now_map, base_map)
    problems.extend(ref_errors)

    if not records_path.is_file():
        problems.append("docs/records.md: 文件不存在")

    for problem in problems:
        print(problem)
    for warning in ref_warnings:
        print(f"[警告] {warning}")
    if problems:
        print(f"\n共 {len(problems)} 处问题，另有 {len(ref_warnings)} 条基线已有警告；检查未通过。")
        return 1
    print(
        f"检查通过：{len(files)} 份文档的标题、数字、配置键与全仓库章节引用一致"
        f"（基线已有的失效引用 {len(ref_warnings)} 条，见上方警告）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
