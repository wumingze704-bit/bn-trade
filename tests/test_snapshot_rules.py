"""交易规则快照生成脚本的辅助函数测试。

规格：docs/03-数据与选币.md §4.3、§4.6
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.snapshot_rules import infer_missing_rules, merge_rules
from trade.data.universe import SymbolMeta


def _meta(symbol: str, tick: float) -> SymbolMeta:
    return SymbolMeta(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        contract_type="PERPETUAL",
        tick_size=tick,
        step_size=0.001,
        min_notional=5.0,
        max_leverage=None,
    )


def test_merge_rules不覆盖已有条目() -> None:
    existing = {"AAAUSDT": _meta("AAAUSDT", 0.01)}
    inferred = {"AAAUSDT": _meta("AAAUSDT", 0.5), "BBBUSDT": _meta("BBBUSDT", 0.001)}
    merged = merge_rules(existing, inferred)
    assert merged["AAAUSDT"].tick_size == 0.01
    assert merged["BBBUSDT"].tick_size == 0.001


def test_缺失已下架合约没有原始逐笔时报错(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="没有原始逐笔文件"):
        infer_missing_rules({}, {"OLDUSDT": "C999"}, tmp_path)


def test_已存在的合约不参与推断(tmp_path: Path) -> None:
    assert (
        infer_missing_rules({"AAAUSDT": _meta("AAAUSDT", 0.01)}, {"AAAUSDT": "C001"}, tmp_path)
        == {}
    )
