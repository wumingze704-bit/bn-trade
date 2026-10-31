"""数据集构建与 manifest 测试。

规格：docs/04-特征与标签.md §8；08 §2 P2 验收
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from scripts.build_dataset import build_dataset
from tests.features.helpers import make_bars
from trade.common.config import BaseConfig
from trade.common.exits import ExitConfig
from trade.data.loader import collect_universe_coins
from trade.data.universe import (
    RuleConfig,
    SnapshotCoin,
    UniverseConfig,
    UniverseSnapshot,
    write_universe_snapshot,
)
from trade.features import FeaturesConfig


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _inject_breakout(bars, i: int) -> None:
    new_close = bars.loc[i, "close"] * 1.05
    bars.loc[i, "close"] = new_close
    bars.loc[i, "high"] = new_close
    bars.loc[i, "low"] = new_close * 0.99
    bars.loc[i, "p_1s"] = new_close
    bars.loc[i, "hi_post_1s"] = new_close
    bars.loc[i, "lo_post_1s"] = new_close


def test_build_dataset_end_to_end(tmp_path) -> None:
    base_ms = _ms(2026, 1, 1)
    data_root = tmp_path / "data"
    for alias, seed in (("C01", 1), ("C02", 2)):
        bars = make_bars(300, seed=seed, base_ms=base_ms)
        for i in (80, 120, 160, 200, 240):
            _inject_breakout(bars, i)
        bars_dir = data_root / "bars" / alias
        bars_dir.mkdir(parents=True)
        bars.to_parquet(bars_dir / "2026-01.parquet", index=False)
    btc = make_bars(300, seed=99, base_ms=base_ms)
    btc_dir = data_root / "bars" / "BTCUSDT"
    btc_dir.mkdir(parents=True)
    btc.to_parquet(btc_dir / "2026-01.parquet", index=False)

    base_cfg = BaseConfig(
        market="usdm",
        quote_asset="USDT",
        data_window={
            "end_month": "2026-01",
            "months": 1,
            "warmup_days": 1,
            "tuning_months": 12,
            "holdout_months": 0,
        },
        fees={"maker": 0.0002, "taker": 0.0005},
        display_tz="UTC",
        paths={"data_root": data_root, "artifact_root": tmp_path / "artifacts"},
        seed=42,
    )
    universe_cfg = UniverseConfig(
        btc_context="BTCUSDT",
        rule=RuleConfig(new_entry_delay_days=0),
    )
    snapshot = UniverseSnapshot(
        month="2026-01",
        rebalance_ms=base_ms,
        rule_hash="test-rule-hash",
        coins=[
            SnapshotCoin(
                symbol="SOLUSDT",
                alias="C01",
                role="train",
                rank=1,
                daily_quote_median=100.0,
                first_listing_ms=base_ms,
            ),
            SnapshotCoin(
                symbol="SUIUSDT",
                alias="C02",
                role="train",
                rank=2,
                daily_quote_median=90.0,
                first_listing_ms=base_ms,
            ),
        ],
    )
    universe_dir = data_root / "universe"
    universe_dir.mkdir(parents=True)
    write_universe_snapshot(universe_dir / "2026-01.json", snapshot)

    coins = collect_universe_coins(data_root, ["2026-01"])
    features_cfg = FeaturesConfig(warmup_bars=0)

    manifest = build_dataset(
        "test_run",
        base_cfg,
        coins,
        features_cfg,
        data_root,
        btc_context="BTCUSDT",
        universe_cfg=universe_cfg,
        exits_cfg=ExitConfig(type="trail"),
    )

    assert len(manifest["coins"]) == 2
    assert manifest["feature_hash"]
    assert len(manifest["features"]) == 37
    assert manifest["universe_rule_hash"]
    assert manifest["exit_policy_hash"]
    for entry in manifest["coins"]:
        assert entry["rows"] == 300
        assert 0.0 <= entry["retention"] <= 1.0
        assert 0.0 <= entry["positive_rate"] <= 1.0
        assert entry["samples"] > 0
    # 产物文件与 manifest 落盘
    manifest_path = data_root / "features" / "test_run" / "manifest.json"
    assert manifest_path.exists()
    assert (data_root / "features" / "test_run" / "C01.parquet").exists()
    assert (data_root / "labels" / "test_run" / "C01.parquet").exists()
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["feature_hash"] == manifest["feature_hash"]
