"""规则化月度选币测试。

规格：docs/03-数据与选币.md §4
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.select_coins import main as select_coins_main
from trade.data.universe import (
    RolesConfig,
    SnapshotCoin,
    SymbolMeta,
    UniverseConfig,
    UniverseSnapshot,
    assign_aliases,
    build_universe,
    load_universe_config,
    read_universe_snapshot,
    role_for_symbol,
    rule_hash,
    write_universe_snapshot,
)

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
_DAY_MS = 86_400_000


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _daily(symbol: str, start_ms: int, n_days: int, quote: float, seed: int = 1) -> pd.DataFrame:
    """生成确定性日线（open_time、close、quote_volume）。"""
    rng = np.random.default_rng(seed)
    open_time = (start_ms + np.arange(n_days) * _DAY_MS).astype("int64")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, n_days)))
    return pd.DataFrame(
        {"open_time": open_time, "close": close, "quote_volume": np.full(n_days, float(quote))}
    )


def _cfg(**rule_overrides) -> UniverseConfig:
    rule = {
        "rebalance": "monthly",
        "top_n": 2,
        "min_listing_days": 3,
        "lookback_days": 10,
        "min_daily_quote_median": 100.0,
        "step_notional_max": 10.0,
        "min_notional_max": 5.0,
        "min_leverage": 5,
        "tick_rel_max": 0.01,
        "exclude_bases": ["BTC", "ETH"],
        "exclude_stable": True,
        "exclude_tradfi": True,
        "excluded_symbols": [],
    }
    rule.update(rule_overrides)
    return UniverseConfig(reviewed=False, btc_context="BTCUSDT", rule=rule)


def test_load_universe_config_defaults() -> None:
    cfg = load_universe_config(CONFIGS / "universe.yaml")
    assert cfg.reviewed is True  # 使用者已审阅首份快照（2026-10-31）
    assert cfg.rule.top_n == 60
    assert cfg.rule.new_entry_delay_days == 2
    assert cfg.rule.min_daily_quote_median == 20_000_000
    assert cfg.roles.holdout_mod == 5
    assert cfg.roles.holdout_rem == 0


def test_roles_config_rejects_rem_not_less_than_mod() -> None:
    with pytest.raises(ValueError):
        RolesConfig(holdout_mod=5, holdout_rem=5)


def test_role_for_symbol_is_stable() -> None:
    roles = RolesConfig(holdout_mod=5, holdout_rem=0)
    assert role_for_symbol("SOLUSDT", roles) == role_for_symbol("SOLUSDT", roles)
    assert role_for_symbol("SOLUSDT", roles) in {"train", "holdout"}
    other = RolesConfig(holdout_mod=7, holdout_rem=0)
    assert role_for_symbol("SOLUSDT", other) in {"train", "holdout"}


def test_rule_hash_excludes_reviewed_and_context() -> None:
    cfg = _cfg()
    base = rule_hash(cfg)
    reviewed = rule_hash(cfg.model_copy(update={"reviewed": True}))
    context = rule_hash(cfg.model_copy(update={"btc_context": "ETHUSDT"}))
    changed = rule_hash(_cfg(top_n=99))
    assert reviewed == base
    assert context == base
    assert changed != base


def test_build_universe_ignores_post_rebalance_data() -> None:
    """未来函数测试：修改 R 之后的日线，U(R) 不变（03 §4.2）。"""
    r = _ms(2026, 1, 1)
    start = _ms(2025, 10, 1)
    daily = {
        "SOLUSDT": _daily("SOLUSDT", start, 120, 5_000_000),
        "SUIUSDT": _daily("SUIUSDT", start, 120, 3_000_000),
    }
    rules = {
        "SOLUSDT": SymbolMeta(symbol="SOLUSDT", base_asset="SOL", tick_size=0.01, step_size=0.1),
        "SUIUSDT": SymbolMeta(symbol="SUIUSDT", base_asset="SUI", tick_size=0.01, step_size=0.1),
    }
    before = build_universe(r, daily, rules, _cfg())

    mutated = {k: v.copy() for k, v in daily.items()}
    for frame in mutated.values():
        frame.loc[frame["open_time"] >= r, "quote_volume"] = 1e15
        frame.loc[frame["open_time"] >= r, "close"] = 1e9
    after = build_universe(r, mutated, rules, _cfg())

    assert before.model_dump() == after.model_dump()
    assert [coin.symbol for coin in before.coins] == ["SOLUSDT", "SUIUSDT"]


def test_build_universe_exclusion_steps_and_ranking() -> None:
    r = _ms(2026, 1, 1)
    start = _ms(2025, 10, 1)
    daily = {
        "TRADFIUSDT": _daily("TRADFIUSDT", start, 120, 9_000_000),
        "BTCUSDT": _daily("BTCUSDT", start, 120, 9_000_000),
        "USDCUSDT": _daily("USDCUSDT", start, 120, 9_000_000),
        "NEWCOINUSDT": _daily("NEWCOINUSDT", r - 1 * _DAY_MS, 5, 9_000_000),
        "LOWLIQUSDT": _daily("LOWLIQUSDT", start, 120, 50.0),
        "BADSTEPUSDT": _daily("BADSTEPUSDT", start, 120, 9_000_000),
        "HIGHUSDT": _daily("HIGHUSDT", start, 120, 8_000_000),
        "MIDUSDT": _daily("MIDUSDT", start, 120, 6_000_000),
    }
    rules = {
        "TRADFIUSDT": SymbolMeta(symbol="TRADFIUSDT", underlying_type="STOCK"),
        "BTCUSDT": SymbolMeta(symbol="BTCUSDT", base_asset="BTC"),
        "USDCUSDT": SymbolMeta(symbol="USDCUSDT", is_stable=True),
        "NEWCOINUSDT": SymbolMeta(symbol="NEWCOINUSDT"),
        "LOWLIQUSDT": SymbolMeta(symbol="LOWLIQUSDT"),
        "BADSTEPUSDT": SymbolMeta(symbol="BADSTEPUSDT", step_size=100.0),
        "HIGHUSDT": SymbolMeta(symbol="HIGHUSDT", tick_size=0.01, step_size=0.1),
        "MIDUSDT": SymbolMeta(symbol="MIDUSDT", tick_size=0.01, step_size=0.1),
    }
    snapshot = build_universe(r, daily, rules, _cfg())

    assert [coin.symbol for coin in snapshot.coins] == ["HIGHUSDT", "MIDUSDT"]
    assert snapshot.excluded_counts["excluded"] == 3
    assert snapshot.excluded_counts["listing"] == 1
    assert snapshot.excluded_counts["liquidity"] == 1
    assert snapshot.excluded_counts["tradability"] == 1


def test_build_universe_no_substitute_on_quality() -> None:
    """第 7 步：质量不合格直接落选，不找替补（03 §4.2）。"""
    r = _ms(2026, 1, 1)
    start = _ms(2025, 10, 1)
    daily = {
        "HIGHUSDT": _daily("HIGHUSDT", start, 120, 9_000_000),
        "MIDUSDT": _daily("MIDUSDT", start, 120, 6_000_000),
        "LOWUSDT": _daily("LOWUSDT", start, 120, 3_000_000),
    }
    rules = {
        "HIGHUSDT": SymbolMeta(symbol="HIGHUSDT"),
        "MIDUSDT": SymbolMeta(symbol="MIDUSDT"),
        "LOWUSDT": SymbolMeta(symbol="LOWUSDT"),
    }
    quality = pd.DataFrame(
        {
            "symbol": ["HIGHUSDT", "MIDUSDT", "LOWUSDT"],
            "window_start_ms": [r - 30 * _DAY_MS] * 3,
            "window_end_ms": [r] * 3,
            "qualified": [False, True, True],
        }
    )
    snapshot = build_universe(r, daily, rules, _cfg(top_n=2), quality_cache=quality)

    assert [coin.symbol for coin in snapshot.coins] == ["MIDUSDT"]
    assert snapshot.excluded_counts["quality"] == 1


def test_assign_aliases_only_add_no_reuse() -> None:
    snapshot1 = UniverseSnapshot(
        month="2025-12",
        rebalance_ms=_ms(2025, 12, 1),
        rule_hash="h",
        coins=[
            SnapshotCoin(
                symbol="A",
                alias="",
                role="train",
                rank=1,
                daily_quote_median=1.0,
                first_listing_ms=0,
            ),
            SnapshotCoin(
                symbol="B",
                alias="",
                role="holdout",
                rank=2,
                daily_quote_median=2.0,
                first_listing_ms=0,
            ),
        ],
    )
    out1, aliases = assign_aliases(snapshot1, {})
    assert out1.coins[0].alias == "C001"
    assert out1.coins[1].alias == "C002"

    snapshot2 = UniverseSnapshot(
        month="2026-01",
        rebalance_ms=_ms(2026, 1, 1),
        rule_hash="h",
        coins=[
            SnapshotCoin(
                symbol="B",
                alias="",
                role="holdout",
                rank=1,
                daily_quote_median=2.0,
                first_listing_ms=0,
            ),
            SnapshotCoin(
                symbol="C",
                alias="",
                role="train",
                rank=2,
                daily_quote_median=3.0,
                first_listing_ms=0,
            ),
        ],
    )
    out2, aliases = assign_aliases(snapshot2, aliases)
    assert out2.coins[0].alias == "C002"
    assert out2.coins[1].alias == "C003"
    assert aliases == {"A": "C001", "B": "C002", "C": "C003"}


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    snapshot = UniverseSnapshot(
        month="2026-01",
        rebalance_ms=_ms(2026, 1, 1),
        rule_hash="abc123",
        coins=[
            SnapshotCoin(
                symbol="SOLUSDT",
                alias="C001",
                role="train",
                rank=1,
                daily_quote_median=5_000_000.0,
                first_listing_ms=_ms(2024, 1, 1),
            ),
        ],
        excluded_counts={"candidates": 1},
    )
    path = tmp_path / "universe" / "2026-01.json"
    write_universe_snapshot(path, snapshot)
    loaded = read_universe_snapshot(path)
    assert loaded == snapshot
    assert loaded.coins[0].alias == "C001"


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_select_coins_build_all_and_report(tmp_path: Path) -> None:
    """--build --all 生成月度快照、别名与报告（fixture 全市场日线）。"""
    data_root = tmp_path / "data"
    symbols = {
        "SOLUSDT": 10_000_000,
        "SUIUSDT": 8_000_000,
        "DOGEUSDT": 6_000_000,
        "AAPLUSDT": 9_000_000,  # TradFi，应被排除
        "BTCUSDT": 20_000_000,  # base BTC，应被排除
    }
    start = _ms(2025, 9, 1)
    for symbol, quote in symbols.items():
        frame = _daily(symbol, start, 150, quote, seed=hash(symbol) % 1000)
        daily_dir = data_root / "daily" / symbol
        daily_dir.mkdir(parents=True)
        frame.to_parquet(daily_dir / "2025.parquet", index=False)

    rules = {
        "SOLUSDT": {"base_asset": "SOL", "tick_size": 0.01, "step_size": 0.1},
        "SUIUSDT": {"base_asset": "SUI", "tick_size": 0.01, "step_size": 0.1},
        "DOGEUSDT": {"base_asset": "DOGE", "tick_size": 0.0001, "step_size": 0.1},
        "AAPLUSDT": {"base_asset": "AAPL", "underlying_type": "STOCK"},
        "BTCUSDT": {"base_asset": "BTC"},
    }
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")

    base_yaml = tmp_path / "base.yaml"
    _write_yaml(
        base_yaml,
        'market: usdm\nquote_asset: USDT\ndata_window:\n  end_month: "2026-01"\n'
        "  months: 2\n  warmup_days: 1\n  tuning_months: 12\n  holdout_months: 0\n"
        "fees: {maker: 0.0002, taker: 0.0005}\ndisplay_tz: UTC\n"
        f"paths: {{data_root: {str(data_root)}, artifact_root: {str(tmp_path / 'artifacts')}}}\n"
        "seed: 42\n",
    )
    universe_yaml = tmp_path / "universe.yaml"
    _write_yaml(
        universe_yaml,
        "reviewed: false\nbtc_context: BTCUSDT\nrule:\n  rebalance: monthly\n"
        "  new_entry_delay_days: 2\n  top_n: 3\n  min_listing_days: 3\n  lookback_days: 10\n"
        "  min_daily_quote_median: 100\n  step_notional_max: 50.0\n  min_notional_max: 5.0\n"
        "  min_leverage: 5\n  tick_rel_max: 0.01\n  exclude_bases: [BTC, ETH]\n"
        "  exclude_stable: true\n  exclude_tradfi: true\n  excluded_symbols: []\n"
        "roles: {holdout_mod: 5, holdout_rem: 0}\n"
        "quality: {missing_ratio_max: 0.01, aggtrades_missing_ratio_max: 0.01, "
        "consistency_error_max: 0.02}\n",
    )

    code = select_coins_main(
        [
            "--build",
            "--all",
            "--base",
            str(base_yaml),
            "--universe",
            str(universe_yaml),
            "--data-root",
            str(data_root),
            "--rules",
            str(rules_path),
        ]
    )
    assert code == 0

    for month in ("2025-12", "2026-01"):
        snap = read_universe_snapshot(data_root / "universe" / f"{month}.json")
        assert [coin.symbol for coin in snap.coins] == ["SOLUSDT", "SUIUSDT", "DOGEUSDT"]

    aliases = json.loads((data_root / "universe" / "aliases.json").read_text(encoding="utf-8"))
    assert aliases == {"SOLUSDT": "C001", "SUIUSDT": "C002", "DOGEUSDT": "C003"}

    report_path = tmp_path / "universe_report.md"
    code = select_coins_main(
        [
            "--report",
            "--base",
            str(base_yaml),
            "--universe",
            str(universe_yaml),
            "--data-root",
            str(data_root),
            "--report-out",
            str(report_path),
        ]
    )
    assert code == 0
    report = report_path.read_text(encoding="utf-8")
    assert "# 选币快照报告" in report
    assert "SOLUSDT" in report


def test_select_coins_rule_hash_protection(tmp_path: Path) -> None:
    """规则哈希不同时 --build 报错不覆盖（03 §4.6）。"""
    data_root = tmp_path / "data"
    start = _ms(2025, 9, 1)
    for symbol in ("SOLUSDT", "SUIUSDT"):
        frame = _daily(symbol, start, 150, 5_000_000, seed=1)
        daily_dir = data_root / "daily" / symbol
        daily_dir.mkdir(parents=True)
        frame.to_parquet(daily_dir / "2025.parquet", index=False)

    base_yaml = tmp_path / "base.yaml"
    _write_yaml(
        base_yaml,
        'market: usdm\nquote_asset: USDT\ndata_window:\n  end_month: "2026-01"\n'
        "  months: 1\n  warmup_days: 1\n  tuning_months: 12\n  holdout_months: 0\n"
        "fees: {maker: 0.0002, taker: 0.0005}\ndisplay_tz: UTC\n"
        f"paths: {{data_root: {str(data_root)}, artifact_root: {str(tmp_path / 'artifacts')}}}\n"
        "seed: 42\n",
    )

    def run_with(top_n: int) -> None:
        universe_yaml = tmp_path / f"universe_{top_n}.yaml"
        _write_yaml(
            universe_yaml,
            "reviewed: false\nbtc_context: BTCUSDT\nrule:\n  rebalance: monthly\n"
            f"  top_n: {top_n}\n  min_listing_days: 3\n  lookback_days: 10\n"
            "  min_daily_quote_median: 100\n  step_notional_max: 10.0\n  min_notional_max: 5.0\n"
            "  min_leverage: 5\n  tick_rel_max: 0.01\n  exclude_bases: [BTC, ETH]\n"
            "  exclude_stable: true\n  exclude_tradfi: true\n  excluded_symbols: []\n"
            "roles: {holdout_mod: 5, holdout_rem: 0}\n"
            "quality: {missing_ratio_max: 0.01, aggtrades_missing_ratio_max: 0.01, "
            "consistency_error_max: 0.02}\n",
        )
        select_coins_main(
            [
                "--build",
                "--month",
                "2025-12",
                "--base",
                str(base_yaml),
                "--universe",
                str(universe_yaml),
                "--data-root",
                str(data_root),
            ]
        )

    run_with(2)
    with pytest.raises(SystemExit):
        run_with(3)
