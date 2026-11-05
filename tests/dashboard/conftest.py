"""看板测试共用 fixture。

构造最小回测报告产物（06 §8）与状态库 sample（10 §12），不依赖真实 P4/P5 产物。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest

from trade.backtest.engine import ExecutedTrade, PortfolioResult
from trade.backtest.report import write_report
from trade.data.quality import write_quality_cache
from trade.live.store import Store

_NOW_MS = int(time.time() * 1000)


def _write_universe_month(
    data_dir: Path, month: str, rebalance_ms: int, coins: list[tuple[str, str, str, int, float]]
) -> None:
    """写入一份最小交易范围快照（03 §4.2）。"""
    universe_dir = data_dir / "universe"
    universe_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "month": month,
        "rebalance_ms": rebalance_ms,
        "rule_hash": "rule-fixture",
        "coins": [
            {
                "symbol": symbol,
                "alias": alias,
                "role": role,
                "rank": rank,
                "daily_quote_median": median,
                "first_listing_ms": rebalance_ms - 90 * 86_400_000,
            }
            for symbol, alias, role, rank, median in coins
        ],
        "excluded_counts": {"tradfi": 2},
    }
    (universe_dir / f"{month}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def state_db(tmp_path: Path) -> Path:
    """构造含 scan_cycles / scan_state / positions / orders / fills / events 的状态库。"""
    db_path = tmp_path / "live" / "state.db"
    store = Store(db_path)
    try:
        store.write_scan(
            cycle={
                "open_time": _NOW_MS - 10 * 60_000,
                "started_ms": _NOW_MS - 10 * 60_000 + 100,
                "finished_ms": _NOW_MS - 10 * 60_000 + 282,
                "n_symbols": 3,
                "n_prefilter": 1,
                "n_signal": 1,
                "n_opened": 1,
            },
            states=[
                {
                    "symbol": "AAAUSDT",
                    "alias": "C001",
                    "open_time": _NOW_MS - 10 * 60_000,
                    "seq": 2,
                    "status": "tradable",
                    "prefilter": 1,
                    "p": 0.62,
                    "m": 0.4,
                    "decision": "open",
                    "skip_reason": None,
                    "spread_bp": 1.2,
                    "processed_ms": 90.0,
                },
                {
                    "symbol": "BBBUSDT",
                    "alias": "C002",
                    "open_time": _NOW_MS - 10 * 60_000,
                    "seq": 1,
                    "status": "tradable",
                    "prefilter": 0,
                    "p": 0.1,
                    "m": 0.0,
                    "decision": "none",
                    "skip_reason": None,
                    "spread_bp": 2.0,
                    "processed_ms": 80.0,
                },
                {
                    "symbol": "CCCUSDT",
                    "alias": "C003",
                    "open_time": _NOW_MS - 10 * 60_000,
                    "seq": 3,
                    "status": "watching",
                    "prefilter": 0,
                    "p": None,
                    "m": None,
                    "decision": "none",
                    "skip_reason": None,
                    "spread_bp": 3.0,
                    "processed_ms": 70.0,
                },
            ],
        )
        store.upsert_position(
            {
                "position_id": "pos-1",
                "symbol": "AAAUSDT",
                "alias": "C001",
                "signal_t": _NOW_MS - 47 * 60_000,
                "p": 0.62,
                "m": 0.4,
                "entry_price": 1.0,
                "qty": 20.0,
                "margin": 4.0,
                "leverage": 5,
                "sl": 0.02,
                "atr_abs": 0.01,
                "stop_price": 1.02,
                "cap_price": 1.08,
                "hh": 1.04,
                "stop_updates": 1,
                "exit_state_json": None,
                "t_exit_deadline": None,
                "status": "OPEN",
                "exit_type": None,
                "exit_price": None,
                "fee": 0.01,
                "funding": 0.0,
                "pnl": None,
                "position_frac": 0.2,
            }
        )
        store.upsert_order(
            client_id="trd-stop-1",
            symbol="AAAUSDT",
            side="SELL",
            order_type="STOP_MARKET",
            price=1.02,
            qty=20.0,
            status="NEW",
        )
        store.upsert_order(
            client_id="trd-tp-1",
            symbol="AAAUSDT",
            side="SELL",
            order_type="TAKE_PROFIT_MARKET",
            price=1.08,
            qty=20.0,
            status="CANCELED",
        )
        store.insert_fill(
            exchange_trade_id=1,
            client_id="trd-close-1",
            symbol="AAAUSDT",
            price=1.03,
            qty=20.0,
            fee=0.01,
            fee_asset="USDT",
            realized_pnl=0.35,
            ts_ms=_NOW_MS - 60_000,
        )
        store.insert_fill(
            exchange_trade_id=2,
            client_id="trd-close-2",
            symbol="BBBUSDT",
            price=0.8,
            qty=25.0,
            fee=0.01,
            fee_asset="USDT",
            realized_pnl=-0.12,
            ts_ms=_NOW_MS - 120_000,
        )
        store.insert_equity_snapshot(
            ts_ms=_NOW_MS - 30 * 60_000,
            wallet_balance=10.0,
            unrealized_pnl=0.05,
            available_margin=6.0,
        )
        store.insert_equity_snapshot(
            ts_ms=_NOW_MS - 60_000,
            wallet_balance=10.23,
            unrealized_pnl=0.12,
            available_margin=6.1,
        )
        store.set_halt_flag(0, date="2025-10-09")
        store.update_risk_state(
            "2025-10-09", realized_pnl=0.23, peak_equity=10.35, consecutive_losses=0
        )
        store.insert_event(
            level="info",
            event="AAA 开仓 · IOC 全部成交",
            symbol="AAAUSDT",
            detail={"p": 0.62, "notional": 20.0},
        )
        store.insert_event(
            level="warn",
            event="CCC 信号未开仓 · 冷却期",
            symbol="CCCUSDT",
            detail={"p": 0.58},
        )
        store.insert_event(
            level="error",
            event="BBB 初始止损 −1.00R",
            symbol="BBBUSDT",
            detail={"reason": "止损冷却 15 分钟"},
        )
    finally:
        store.commit()
        store.close()
    return db_path


@pytest.fixture
def research_data(tmp_path: Path) -> Path:
    """构造交易范围快照、质量缓存与数据集 manifest（10 §5.5）。"""
    data_dir = tmp_path / "data"
    _write_universe_month(
        data_dir,
        "2025-09",
        _NOW_MS - 40 * 86_400_000,
        [
            ("AAAUSDT", "C001", "train", 1, 50_000_000.0),
            ("BBBUSDT", "C002", "holdout", 2, 40_000_000.0),
        ],
    )
    _write_universe_month(
        data_dir,
        "2025-10",
        _NOW_MS - 10 * 86_400_000,
        [
            ("AAAUSDT", "C001", "train", 1, 55_000_000.0),
            ("CCCUSDT", "C003", "train", 2, 30_000_000.0),
        ],
    )
    features_dir = data_dir / "features" / "ds_fixture"
    features_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "ds_fixture",
                "include_holdout": False,
                "feature_hash": "f" * 64,
                "exit_policy_hash": "e" * 64,
                "universe_rule_hash": "u" * 64,
                "label": {"mode": "exit_sim"},
                "coins": [
                    {"alias": "C001", "samples": 100, "retention": 0.2, "positive_rate": 0.25},
                    {"alias": "C003", "samples": 50, "retention": 0.1, "positive_rate": 0.15},
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    quality = pd.DataFrame(
        {
            "symbol": ["C001", "C003"],
            "window_start_ms": [_NOW_MS - 40 * 86_400_000, _NOW_MS - 40 * 86_400_000],
            "window_end_ms": [_NOW_MS - 10 * 86_400_000, _NOW_MS - 10 * 86_400_000],
            "kline_missing_ratio": [0.001, 0.2],
            "aggtrades_missing_ratio": [0.0, 0.15],
            "consistency_error_median": [0.001, 0.05],
            "qualified": [True, False],
        }
    )
    write_quality_cache(quality, tmp_path / "quality" / "quality_cache.parquet")
    (tmp_path / "universe_report.md").write_text("# 交易范围报告\n\nfixture\n", encoding="utf-8")
    return data_dir


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """构造一份最小回测报告产物，返回 `artifacts/reports` 目录路径。"""
    trade = ExecutedTrade(
        symbol="ALT01USDT",
        group="train",
        signal_open_time=1_700_000_000_000,
        entry_open_time=1_700_000_060_000,
        exit_open_time=1_700_000_120_000,
        p=0.9,
        m=0.5,
        p_fill=100.0,
        sl=0.008,
        exit_price=101.0,
        exit_type="tp",
        exit_type_code=3,
        notional=10.0,
        qty=0.1,
        margin=2.0,
        bars_held=2,
        stop_updates=0,
        mfe_r=1.0,
        mae_r=0.0,
        entry_fee=0.005,
        exit_fee=0.002,
        gross_pnl=1.0,
        net_pnl=0.993,
        capped_by=None,
    )
    result = PortfolioResult(
        trades=[trade],
        equity_curve=pd.DataFrame(
            {
                "ts_ms": [1_700_000_000_000, 1_700_000_060_000, 1_700_000_120_000],
                "equity": [10.0, 10.5, 10.993],
                "drawdown": [0.0, 0.0, 0.0],
            }
        ),
    )
    reports = tmp_path / "reports"
    run_dir = reports / "run1"
    write_report(
        run_dir,
        "run1",
        source="fixture",
        run_results={"reference": {"base": result}},
        reference_metrics={"net_return_usdt": 0.993},
        sanity_results={"S1": "passed", "S8": "not_run"},
        initial_equity=10.0,
    )
    # 封存集运行标记（06 §8）
    (reports / "FINAL_RUNS.log").write_text(
        json.dumps({"ts": "2026-10-31T00:00:00Z", "run_id": "run1"}) + "\n",
        encoding="utf-8",
    )
    return reports
