"""选币快照重建辅助函数测试（离线 fixture）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_universe_hist import (
    _month_list,
    _previous_month,
    _quality_table,
)


def test_月份序列与上一个月() -> None:
    assert _month_list("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]
    assert _previous_month("2025-01") == "2024-12"
    assert _previous_month("2025-03") == "2025-02"


def test_质量表按上月1h覆盖率判定(tmp_path) -> None:
    klines_1h = tmp_path / "klines_1h"
    good = klines_1h / "AAAUSDT"
    good.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "open_time": [1_700_000_000_000 + i * 3_600_000 for i in range(744)],
            "open": [1.0] * 744,
            "high": [1.0] * 744,
            "low": [1.0] * 744,
            "close": [1.0] * 744,
            "volume": [1.0] * 744,
            "quote_volume": [1.0] * 744,
            "count": [1.0] * 744,
        }
    )
    frame.to_parquet(good / "2024-11.parquet", index=False)
    table = _quality_table(klines_1h, ["AAAUSDT", "BBBUSDT"], ["2024-12"], 0.01)
    qualified = table.set_index("symbol")["qualified"].to_dict()
    assert qualified["AAAUSDT"] is True or bool(qualified["AAAUSDT"]) is True
    assert bool(qualified["BBBUSDT"]) is False
    assert Path(good / "2024-11.parquet").is_file()
