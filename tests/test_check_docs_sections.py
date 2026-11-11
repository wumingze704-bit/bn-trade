"""scripts/check_docs_sections.py 基线读取单测（Windows 绝对路径回归）。

规格：AGENTS.md §5.2（设计书定版、自包含，只写当前真实行为与规格）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_docs_sections import load_baseline, refs_in_line


def test_基线路径为仓库相对路径(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """git show 只接受相对仓库根的路径；传绝对路径会报 not in 'HEAD'。"""
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    doc = root / "docs" / "06-回测引擎.md"
    doc.write_text("# 6. 回测\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="# 6. 回测\n".encode(), stderr=b"")

    monkeypatch.setattr("scripts.check_docs_sections.subprocess.run", fake_run)

    text = load_baseline(doc.resolve(), None, root)

    assert calls == [["git", "show", "HEAD:docs/06-回测引擎.md"]]
    assert text == "# 6. 回测\n"


def test_文档不在仓库根目录时抛SystemExit(tmp_path: Path) -> None:
    """路径无法转成仓库相对路径时报错退出，不静默换成别的基线。"""
    outsider = tmp_path / "other" / "06-回测引擎.md"
    outsider.parent.mkdir(parents=True)
    outsider.write_text("# 6. 回测\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        load_baseline(outsider, None, tmp_path / "repo")


def test_相邻的省略文档号引用沿用上一个文档号() -> None:
    """“06 §5.2、§5.3” 里的后一个 § 仍属 06。"""
    assert refs_in_line("（06 §5.2、§5.3）") == [("06", "5.2"), ("06", "5.3")]


def test_跨文字的省略文档号引用不猜文档号() -> None:
    """“AGENTS §3.2”“按 §2.1”不能被算成同行前一份文档的章节。"""
    assert refs_in_line("README、02 §4/§5.2/§6、AGENTS §3.2 已同步") == [
        ("02", "4"),
        ("02", "5.2"),
        ("02", "6"),
        (None, "3.2"),
    ]
    assert refs_in_line("（02 §5.8）。按 §2.1 的阶段分批") == [("02", "5.8"), (None, "2.1")]
