"""前端布局与视觉规范测试（与 docs/dashboard-overview.svg 对照）。

规格：docs/10-可视化看板.md §4、§5.0、§5.1、§6、§12
"""

from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "src" / "trade" / "dashboard" / "static"


def _read(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def test_无浅色主题与设计令牌() -> None:
    """只使用 10 §4 的深色令牌，不提供浅色主题或主题切换。"""
    css = _read("styles.css")
    for token in (
        "--bg: #08090d",
        "--card: rgba(28, 28, 32, 0.72)",
        "--border: rgba(255, 255, 255, 0.08)",
        "--text: #f5f5f7",
        "--text-2: #8e8e93",
        "--text-3: #636366",
        "--green: #30d158",
        "--red: #ff453a",
        "--blue: #0a84ff",
        "--orange: #ff9f0a",
        "--purple: #5e5ce6",
        "--radius: 18px",
    ):
        assert token in css, f"缺少设计令牌 {token}"
    assert "prefers-color-scheme: light" not in css
    assert "[data-theme" not in css


def test_布局与草图一致() -> None:
    """侧边栏 220 px、4 张指标卡、扫描网格 10 列、下排持仓/挂单/事件。"""
    css = _read("styles.css")
    assert "--sidebar: 220px" in css
    assert re.search(r"\.kpis\s*\{[^}]*repeat\(4, 1fr\)", css, re.S)
    assert re.search(r"\.scan-grid\s*\{[^}]*repeat\(10,", css, re.S)
    assert ".scan-card { grid-column: span 7" in css
    assert ".equity-card { grid-column: span 5" in css
    assert ".positions-card { grid-column: span 5" in css
    assert ".orders-card { grid-column: span 4" in css
    assert ".events-card { grid-column: span 3" in css

    html = _read("index.html")
    assert html.count('class="card kpi') == 4
    assert 'class="scan-grid"' in html
    assert 'class="card positions-card"' in html
    assert 'class="card orders-card"' in html
    assert 'class="card events-card"' in html
    assert "SSH 隧道 · 127.0.0.1:8501" in html


def test_扫描网格使用seq顺序与transform动画() -> None:
    """点亮顺序按 seq；进度条与格子动画只用 transform / opacity（10 §6、§10）。"""
    js = _read("app.js")
    assert ".sort((a, b) => a.seq - b.seq)" in js
    assert "transform: scaleX(" in js
    assert "--delay:" in js

    css = _read("styles.css")
    scan_cell_rule = re.search(r"\.scan-cell\s*\{(.*?)\}", css, re.S)
    assert scan_cell_rule is not None
    rule = scan_cell_rule.group(1)
    assert "opacity" in rule and "transform" in rule
    transition = re.search(r"transition:(.*?);", rule, re.S)
    assert transition is not None
    assert "width" not in transition.group(1) and "height" not in transition.group(1)


def test_动态内容只用x_text转义() -> None:
    """所有动态文本用 x-text，不用 x-html（10 §3）。"""
    html = _read("index.html")
    assert "x-html" not in html
    assert "x-text" in html


def test_研究页数据与模型标签() -> None:
    """“数据与模型”标签页包含交易范围快照与质量页（10 §5.5）。"""
    html = _read("index.html")
    assert "数据与模型" in html
    assert "交易范围快照" in html
    assert "数据质量（30 天窗口）" in html
    assert "qualityRows" in html
    assert "universeMonths" in html
    assert "datasets" in html
