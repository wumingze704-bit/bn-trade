"""scripts/backtest.py 端到端 fixture 冒烟（R0 与模型信号共用同一入口）。

规格：docs/06-回测引擎.md §3、§5.5、§7、§8；docs/08 §2 P3~P4
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts.backtest import main

_ROOT = Path(__file__).resolve().parents[2]
_T0 = 1_735_689_600_000  # 2025-01-01T00:00Z
_MIN = 60_000


def _bars() -> pd.DataFrame:
    n = 600
    start = _T0 + 4 * 86_400_000  # 2025-01-05，避开新进入 2 天观察期
    close = np.linspace(100.0, 106.0, n)
    high = close + 0.2
    low = close - 0.2
    open_time = start + np.arange(n, dtype="int64") * _MIN
    frame = pd.DataFrame(
        {
            "open_time": open_time,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
            "close_time": open_time + _MIN - 1,
            "quote_volume": 120_000.0,
            "count": 100,
            "taker_buy_volume": 600.0,
            "taker_buy_quote_volume": 60_000.0,
            "agg_n": 100,
            "buy_quote": 60_000.0,
            "sell_quote": 60_000.0,
            "max_trade_quote": 1000.0,
            "max_buy_1s_quote": 1000.0,
            "whale_buy_quote": 0.0,
            "whale_sell_quote": 0.0,
            "whale_thr": 1e9,
            "p_1s": close,
            "p_1s_filled": 0,
            "p_3s": close,
            "p_3s_filled": 0,
            "hi_post_1s": high,
            "lo_post_1s": low,
            "hi_post_3s": high,
            "lo_post_3s": low,
            "is_filled": 0,
            "kline_missing": 0,
            "aggtrades_missing": 0,
        }
    )
    return frame


def _prepare(tmp_path: Path) -> dict[str, Path]:
    data = tmp_path / "data"
    artifacts = tmp_path / "artifacts"
    configs = tmp_path / "configs"
    (data / "universe").mkdir(parents=True)
    (data / "bars" / "C001").mkdir(parents=True)
    (artifacts / "exchange_info").mkdir(parents=True)
    configs.mkdir(parents=True)

    bars = _bars()
    bars.to_parquet(data / "bars" / "C001" / "2025-01.parquet", index=False)
    snapshot = {
        "month": "2025-01",
        "rebalance_ms": _T0,
        "rule_hash": "rule-fixture",
        "coins": [
            {
                "symbol": "ALPHAUSDT",
                "alias": "C001",
                "role": "train",
                "rank": 1,
                "daily_quote_median": 50_000_000.0,
                "first_listing_ms": _T0 - 90 * 86_400_000,
            }
        ],
        "excluded_counts": {},
    }
    (data / "universe" / "2025-01.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (data / "universe" / "aliases.json").write_text(
        json.dumps({"ALPHAUSDT": "C001"}), encoding="utf-8"
    )
    rules = {"C001": {"tick_size": 0.01, "step_size": 0.01, "min_notional": 5.0}}
    rules_path = artifacts / "exchange_info" / "rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")

    base = yaml.safe_load((_ROOT / "configs" / "base.yaml").read_text("utf-8"))
    base["data_window"] = {
        "end_month": "2025-01",
        "months": 1,
        "warmup_days": 1,
        "tuning_months": 1,
        "holdout_months": 0,
    }
    base["paths"] = {"data_root": str(data), "artifact_root": str(artifacts)}
    (configs / "base.yaml").write_text(yaml.safe_dump(base, allow_unicode=True), "utf-8")
    backtest = yaml.safe_load((_ROOT / "configs" / "backtest.yaml").read_text("utf-8"))
    backtest["sanity"]["random_iters"] = 5
    (configs / "backtest.yaml").write_text(yaml.safe_dump(backtest, allow_unicode=True), "utf-8")
    for name in ("exits.yaml", "sizing.yaml", "features.yaml", "train.yaml", "universe.yaml"):
        (configs / name).write_text((_ROOT / "configs" / name).read_text("utf-8"), "utf-8")

    signals = pd.DataFrame(
        {
            "symbol": ["C001"],
            "open_time": [int(bars["open_time"].iloc[100])],
            "p": [0.9],
            "m": [0.5],
            "is_sample": [1],
        }
    )
    signals_path = artifacts / "signals.parquet"
    signals.to_parquet(signals_path, index=False)
    model_dir = tmp_path / "models" / "m1"
    model_dir.mkdir(parents=True)
    (model_dir / "model_meta.json").write_text(
        json.dumps({"model_id": "m1", "threshold": 0.5}), encoding="utf-8"
    )
    return {
        "configs": configs,
        "artifacts": artifacts,
        "rules": rules_path,
        "signals": signals_path,
        "bars": bars,
        "model": model_dir,
    }


@pytest.mark.parametrize("signals_kind", ["r0", "model"])
def test_backtest_script_end_to_end_fixture(tmp_path: Path, signals_kind: str) -> None:
    paths = _prepare(tmp_path)
    configs = paths["configs"]
    run_id = f"smoke_{signals_kind}"
    code = main(
        [
            "--signals",
            str(paths["signals"]),
            "--rules",
            str(paths["rules"]),
            "--run-id",
            run_id,
            *(["--model", str(paths["model"])] if signals_kind == "model" else []),
            "--base",
            str(configs / "base.yaml"),
            "--backtest",
            str(configs / "backtest.yaml"),
            "--exits",
            str(configs / "exits.yaml"),
            "--sizing",
            str(configs / "sizing.yaml"),
            "--features",
            str(configs / "features.yaml"),
            "--train",
            str(configs / "train.yaml"),
            "--universe",
            str(configs / "universe.yaml"),
            "--signals-kind",
            signals_kind,
        ]
    )
    assert code == 0
    report = paths["artifacts"] / "reports" / run_id
    gate = json.loads((report / "gate.json").read_text("utf-8"))
    assert gate["signals_kind"] == signals_kind
    assert gate["model_id"] == ("m1" if signals_kind == "model" else None)
    for name in (
        "summary.json",
        "trades.parquet",
        "skipped.parquet",
        "equity.parquet",
        "equity.png",
        "monthly.png",
        "by_symbol.png",
        "p_calibration.png",
        "sizing_sweep.parquet",
        "sizing_sweep.md",
        "sizing_sweep.png",
        "report.md",
    ):
        assert (report / name).is_file(), name
    summary = json.loads((report / "summary.json").read_text("utf-8"))
    assert "base" in summary and "reference" in summary["base"]
