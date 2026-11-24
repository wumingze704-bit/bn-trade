"""试验登记、逐组落盘与断点续跑测试（1 号第三部分第 5 条、B 第 13 条；docs/13 §9）。

覆盖 D12：试验记录含 code_hash / data_hash / config_hash / equity_basis；逐组落盘；
哈希一致时复用、不重复登记；可执行口径写 kind=exec 记录；脚本不写 ledger。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tests.research.helpers import START_MS, make_research_config
from tests.research.test_credibility import (
    _FAMILY,
    _PARAMS,
    _folds,
    _market_with,
    _signal_returns,
)
from trade.common.risk_rules import RiskLimits
from trade.research import pipeline
from trade.research.pipeline import (
    RecordHashes,
    compute_hashes,
    group_cache_path,
    load_group,
    params_key,
    save_group,
)


def _tree(root: Path) -> Path:
    """搭出 compute_hashes 需要的最小仓库结构，返回配置。"""
    (root / "src" / "trade" / "research").mkdir(parents=True)
    (root / "src" / "trade" / "strategies").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "configs").mkdir()
    (root / "data" / "history").mkdir(parents=True)
    (root / "src" / "trade" / "research" / "dummy.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "trade" / "strategies" / "dummy.py").write_text("y = 2\n", encoding="utf-8")
    (root / "scripts" / "portfolio_backtest.py").write_text("z = 3\n", encoding="utf-8")
    (root / "data" / "history" / "manifest.json").write_text('{"files": []}\n', encoding="utf-8")
    for name in ("research.yaml", "sizing_research.yaml", "base.yaml"):
        (root / "configs" / name).write_text("placeholder: 1\n", encoding="utf-8")
    grid = root / "configs" / "round_test.yaml"
    grid.write_text("family: xs_reversal_ls\nparams: []\n", encoding="utf-8")
    return grid


def _config(root: Path):
    config = make_research_config()
    updates = {
        "artifacts": root / "artifacts",
        "experiments_log": root / "artifacts" / "reports" / "EXPERIMENTS.log",
        "data_root": root / "data",
        "klines_1h": root / "data" / "klines_1h",
        "klines_1d": root / "data" / "klines_1d",
        "klines_1h_flow": root / "data" / "klines_1h_flow",
        "premium_1h": root / "data" / "premium_1h",
        "mark_1h": root / "data" / "mark_1h",
        "funding": root / "data" / "funding",
        "universe_hist": root / "data" / "universe_hist",
    }
    return config.model_copy(update={"paths": config.paths.model_copy(update=updates)})


def test_哈希随代码与配置变化(tmp_path, monkeypatch) -> None:
    """code_hash / config_hash 随文件内容变化，data_hash 取 manifest 的 SHA256。"""
    grid = _tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path)
    first = compute_hashes(config=config, root=tmp_path, grid_path=grid)
    assert len(first.code_hash) == 64 and len(first.data_hash) == 64
    (tmp_path / "src" / "trade" / "research" / "dummy.py").write_text("x = 2\n", encoding="utf-8")
    second = compute_hashes(config=config, root=tmp_path, grid_path=grid)
    assert second.code_hash != first.code_hash
    assert second.data_hash == first.data_hash
    grid.write_text("family: xs_reversal_ls\nparams: [{top_k: 1}]\n", encoding="utf-8")
    third = compute_hashes(config=config, root=tmp_path, grid_path=grid)
    assert third.config_hash != first.config_hash
    (tmp_path / "data" / "history" / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError):
        compute_hashes(config=config, root=tmp_path, grid_path=grid)


def test_代码哈希覆盖共享风控与仓位模块(tmp_path, monkeypatch) -> None:
    """code_hash 必须覆盖 `src/trade/common`：风控与仓位是研究、影子盘、实盘共用的执行口径。

    修复前 `compute_hashes` 只看 `src/trade/research`、`src/trade/strategies` 与
    `scripts/portfolio_backtest.py`，改共享风控后断点续跑仍复用旧结果，旧结果被静默当成新口径。
    """
    grid = _tree(tmp_path)
    common = tmp_path / "src" / "trade" / "common"
    common.mkdir(parents=True)
    (common / "risk_rules.py").write_text("HALT = 'reset'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path)
    first = compute_hashes(config=config, root=tmp_path, grid_path=grid)
    (common / "risk_rules.py").write_text("HALT = 'strict'\n", encoding="utf-8")
    second = compute_hashes(config=config, root=tmp_path, grid_path=grid)
    assert second.code_hash != first.code_hash
    assert second.data_hash == first.data_hash


def test_网格评估写出记录与逐组文件并可断点续跑(tmp_path, monkeypatch) -> None:
    """evaluate_grid 写 grid.json、groups/*.json 与两类试验记录；再次运行不重复登记 tuning。"""
    grid_path = _tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path)
    market = _market_with(_signal_returns())
    folds = _folds(START_MS)
    monkeypatch.setattr(pipeline, "build_market", lambda *args, **kwargs: market)
    monkeypatch.setattr(pipeline, "fold_windows", lambda *args, **kwargs: folds)
    end_ms = folds[-1].end_ms
    risk = RiskLimits(
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        halt_mode="reset",
        halt_reset_days=7,
    )
    from trade.research.config import load_research_sizing

    sizing = load_research_sizing(
        Path(__file__).resolve().parents[2] / "configs" / "sizing_research.yaml"
    ).with_tier()
    grid = {"family": _FAMILY, "exposure": "neutral", "params": _PARAMS}
    out_dir = tmp_path / "artifacts" / "reports"
    log_path = tmp_path / "artifacts" / "reports" / "EXPERIMENTS.log"
    kwargs = {
        "config": config,
        "sizing": sizing,
        "fee_taker": 0.0005,
        "risk_limits": risk,
        "grid": grid,
        "round_id": "RTEST",
        "start_ms": START_MS,
        "end_ms": end_ms,
        "start_month": "2024-01",
        "end_month": "2024-03",
        "workers": 1,
        "out_dir": out_dir,
        "log_path": log_path,
        "grid_path": grid_path,
        "scenarios": ("cost2x",),
    }
    report = pipeline.evaluate_grid(**kwargs)
    assert report["hashes"]["code_hash"]
    assert report["equity_basis"] == {"signal": 10_000.0, "exec": 500.0}
    assert (out_dir / "RTEST" / "grid.json").is_file()
    groups = sorted((out_dir / "RTEST" / "groups").glob("*.json"))
    assert len(groups) == len(_PARAMS)
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    tuning = [entry for entry in entries if entry["kind"] == "tuning"]
    execs = [entry for entry in entries if entry["kind"] == "exec"]
    assert len(tuning) == len(_PARAMS)
    assert len(execs) == 1
    for entry in tuning + execs:
        assert entry["code_hash"] and entry["data_hash"] and entry["config_hash"]
    assert tuning[0]["equity_basis"] == 10_000.0
    assert execs[0]["equity_basis"] == 500.0
    # 断点续跑：哈希一致时复用逐组结果，不再追加 tuning 记录
    pipeline.evaluate_grid(**kwargs)
    again = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len([entry for entry in again if entry["kind"] == "tuning"]) == len(_PARAMS)


def test_逐组落盘与哈希不符时重跑(tmp_path) -> None:
    """save_group / load_group 往返一致；哈希不同时视为没有缓存。"""
    hashes = RecordHashes(code_hash="a" * 64, data_hash="b" * 64, config_hash="c" * 64)
    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    group = pipeline.GroupResult(
        family=_FAMILY,
        params=dict(_PARAMS[0]),
        train_daily=pd.Series([0.01, -0.02, 0.0, 0.03, 0.01], index=index),
        train_entries=[1, 2, 3],
        train_meta={"train_daily_sharpe": 0.5},
        own_daily=pd.Series([0.02, 0.0, 0.01, -0.01, 0.0], index=index),
        own_meta={"own_trades": 3.0},
    )
    path = group_cache_path(tmp_path, _FAMILY, params_key(_PARAMS[0]))
    save_group(path, group, hashes)
    loaded = load_group(path, hashes)
    assert loaded is not None
    assert loaded.params == group.params
    pd.testing.assert_series_equal(loaded.train_daily, group.train_daily, check_freq=False)
    assert loaded.train_entries == [1, 2, 3]
    other = RecordHashes(code_hash="d" * 64, data_hash="b" * 64, config_hash="c" * 64)
    assert load_group(path, other) is None
