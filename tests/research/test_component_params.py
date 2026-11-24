"""R5 成分网格读取（去掉仓位模式维度并去重）。

规格：docs/13-研究框架.md §5；1 号第八部分开头（网格乘以 sizing_mode）
"""

from __future__ import annotations

from pathlib import Path

from trade.research.pipeline import _component_param_sets


def test_成分网格去掉sizing_mode并去重(tmp_path: Path) -> None:
    grid = tmp_path / "component.yaml"
    grid.write_text(
        "round: RX\nfamily: xs_momentum_ls\nparams:\n"
        "  - {lookback_days: 7, top_k: 3, sizing_mode: fixed_risk}\n"
        "  - {lookback_days: 7, top_k: 3, sizing_mode: vol_target}\n"
        "  - {lookback_days: 14, top_k: 5}\n",
        encoding="utf-8",
    )
    result = _component_param_sets(tmp_path, {"xs_momentum_ls": "component.yaml"})
    assert result == {
        "xs_momentum_ls": [
            {"lookback_days": 7, "top_k": 3},
            {"lookback_days": 14, "top_k": 5},
        ]
    }
