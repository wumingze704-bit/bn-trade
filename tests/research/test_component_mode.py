"""R5 成分参数按 R5 本组的仓位模式选择（1 号第五部分 B 第 15 条 k 款；缺陷 D19 的复现测试）。

修复前：`_component_param_sets` 去掉 `sizing_mode` 维度并去重，模式 A 与模式 B 的 R5 组
共用同一批成分参数，与 k 款不一致。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trade.research.pipeline import _component_param_sets, ensure_groups
from trade.research.rolling import Fold

_START_MS = 1_704_067_200_000


@pytest.fixture()
def component_grid(tmp_path: Path) -> dict[str, str]:
    grid = tmp_path / "component.yaml"
    grid.write_text(
        "round: RX\nfamily: xs_momentum_ls\nparams:\n"
        "  - {lookback_days: 7, top_k: 3, sizing_mode: fixed_risk}\n"
        "  - {lookback_days: 7, top_k: 3, sizing_mode: vol_target}\n"
        "  - {lookback_days: 14, top_k: 5, sizing_mode: vol_target}\n",
        encoding="utf-8",
    )
    return {"xs_momentum_ls": "component.yaml"}


def test_成分参数按仓位模式过滤(tmp_path: Path, component_grid: dict[str, str]) -> None:
    vol_target = _component_param_sets(tmp_path, component_grid, sizing_mode="vol_target")
    assert vol_target == {
        "xs_momentum_ls": [
            {"lookback_days": 7, "top_k": 3, "sizing_mode": "vol_target"},
            {"lookback_days": 14, "top_k": 5, "sizing_mode": "vol_target"},
        ]
    }
    fixed_risk = _component_param_sets(tmp_path, component_grid, sizing_mode="fixed_risk")
    assert fixed_risk == {
        "xs_momentum_ls": [{"lookback_days": 7, "top_k": 3, "sizing_mode": "fixed_risk"}]
    }


def test_缺少该模式的成分参数时抛出异常(tmp_path: Path) -> None:
    folds = [
        Fold(index=0, start_ms=_START_MS + 30 * 86_400_000, end_ms=_START_MS + 60 * 86_400_000)
    ]
    with pytest.raises(ValueError, match="没有对应成分参数"):
        ensure_groups(
            market=None,
            config=SimpleNamespace(equity=SimpleNamespace(signal=10_000.0)),  # type: ignore[arg-type]
            sizing=SimpleNamespace(),  # type: ignore[arg-type]
            fee_taker=0.0004,
            risk_limits=SimpleNamespace(),  # type: ignore[arg-type]
            pairs=[("ensemble", {"top_k": 3, "sizing_mode": "vol_target"})],
            payload={},
            workers=1,
            group_dir=tmp_path,
            log_path=tmp_path / "EXPERIMENTS.log",
            hashes=None,  # type: ignore[arg-type]
            round_id="R5",
            folds=folds,
            start_ms=_START_MS,
            end_ms=_START_MS + 60 * 86_400_000,
            components_by_mode={"fixed_risk": [[]]},
        )
