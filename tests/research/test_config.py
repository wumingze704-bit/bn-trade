"""研究配置与研究期风险包络的加载校验。

规格：docs/13-研究框架.md §2（停机方式、两种权益口径、三个风险档位）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trade.common.config import ConfigError
from trade.research.config import load_research_config, load_research_sizing

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_YAML = ROOT / "configs" / "research.yaml"
SIZING_YAML = ROOT / "configs" / "sizing_research.yaml"


def _copy_yaml(source: Path, target: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    values = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutate(values)
    target.write_text(yaml.safe_dump(values, allow_unicode=True), encoding="utf-8")
    return target


def test_研究配置含停机方式两种权益口径与组合上限() -> None:
    """research.yaml 的 risk / equity / portfolio 三节取值与规格一致。"""
    config = load_research_config(RESEARCH_YAML)
    assert config.risk.halt_mode == "reset"
    assert config.risk.halt_reset_days == 7
    assert config.equity.signal == 10_000.0
    assert config.equity.exec == 500.0
    assert config.portfolio.total_notional_cap == 3.0
    assert config.portfolio.net_exposure_cap == 1.5


def test_可执行口径不得大于等于信号口径(tmp_path: Path) -> None:
    """equity.exec ≥ equity.signal 时拒绝加载（两种口径的用途不同）。"""

    def mutate(values: dict[str, object]) -> None:
        values["equity"] = {"signal": 500.0, "exec": 500.0}  # type: ignore[assignment]

    path = _copy_yaml(RESEARCH_YAML, tmp_path / "research.yaml", mutate)
    with pytest.raises(ConfigError):
        load_research_config(path)


def test_停机方式只接受strict与reset(tmp_path: Path) -> None:
    """halt_mode 拼错时拒绝加载，不静默回退（红线 12）。"""

    def mutate(values: dict[str, object]) -> None:
        values["risk"] = {"halt_mode": "stop", "halt_reset_days": 7}  # type: ignore[assignment]

    path = _copy_yaml(RESEARCH_YAML, tmp_path / "research.yaml", mutate)
    with pytest.raises(ConfigError):
        load_research_config(path)


def test_风险包络含三个档位且默认标准档() -> None:
    """sizing_research.yaml：初始权益 500、默认标准档、三档齐全。"""
    cfg = load_research_sizing(SIZING_YAML)
    assert cfg.initial_equity == 500.0
    assert cfg.tier == "standard"
    assert set(cfg.risk_tiers) == {"standard", "progressive", "aggressive"}
    applied = cfg.with_tier()
    assert applied.risk_per_trade == pytest.approx(0.0075)
    assert applied.max_risk_per_trade == pytest.approx(0.025)
    assert applied.daily_loss_stop == pytest.approx(0.05)
    assert applied.daily_loss_halt == pytest.approx(0.08)
    assert applied.max_drawdown_halt == pytest.approx(0.20)


def test_切到激进档放大仓位与停机阈值(tmp_path: Path) -> None:
    """档位只放大仓位与停机阈值，不改变其他风险包络项。"""

    def mutate(values: dict[str, object]) -> None:
        values["tier"] = "aggressive"

    path = _copy_yaml(SIZING_YAML, tmp_path / "sizing_research.yaml", mutate)
    applied = load_research_sizing(path).with_tier()
    assert applied.risk_per_trade == pytest.approx(0.02)
    assert applied.max_risk_per_trade == pytest.approx(0.06)
    assert applied.daily_loss_stop == pytest.approx(0.10)
    assert applied.daily_loss_halt == pytest.approx(0.15)
    assert applied.max_drawdown_halt == pytest.approx(0.40)
    assert applied.position_frac == pytest.approx(0.08)
    assert applied.leverage == 5


def test_缺少档位或档位缺失时报错(tmp_path: Path) -> None:
    """三档必须齐全，缺一个就拒绝加载（红线 12）。"""

    def mutate(values: dict[str, object]) -> None:
        tiers = values["risk_tiers"]
        assert isinstance(tiers, dict)
        del tiers["progressive"]

    path = _copy_yaml(SIZING_YAML, tmp_path / "sizing_research.yaml", mutate)
    with pytest.raises(ConfigError):
        load_research_sizing(path)


def test_风险包络含模式B与β中性参数() -> None:
    """sizing_research.yaml 的 sizing_mode / target_vol / 回看窗口 / β 上限与多空比区间。"""
    cfg = load_research_sizing(SIZING_YAML)
    assert cfg.sizing_mode == "fixed_risk"
    assert cfg.vol_lookback_days == 30
    assert cfg.cov_lookback_days == 60
    assert cfg.beta_lookback_days == 60
    assert cfg.beta_abs_max == pytest.approx(0.10)
    assert cfg.ls_ratio_range == (0.5, 2.0)
    standard = cfg.with_tier()
    assert standard.target_vol == pytest.approx(0.20)
    assert standard.sizing_mode == "fixed_risk"
    assert standard.ls_ratio_range == (0.5, 2.0)


def test_多空名义比区间必须包含1(tmp_path: Path) -> None:
    """ls_ratio_range 下限必须 ≤ 1 ≤ 上限（13 §3 的多空名义比约束）。"""

    def mutate(values: dict[str, object]) -> None:
        values["ls_ratio_range"] = [2.0, 0.5]

    path = _copy_yaml(SIZING_YAML, tmp_path / "sizing_research.yaml", mutate)
    with pytest.raises(ConfigError):
        load_research_sizing(path)


def test_仓位模式只接受两种取值(tmp_path: Path) -> None:
    """sizing_mode 拼错时拒绝加载（红线 12）。"""

    def mutate(values: dict[str, object]) -> None:
        values["sizing_mode"] = "voltarget"

    path = _copy_yaml(SIZING_YAML, tmp_path / "sizing_research.yaml", mutate)
    with pytest.raises(ConfigError):
        load_research_sizing(path)


def test_切档同时改模式B的目标波动(tmp_path: Path) -> None:
    """with_tier() 用选中档位的 target_vol 覆盖配置默认值（13 §2）。"""

    def mutate(values: dict[str, object]) -> None:
        values["tier"] = "aggressive"

    path = _copy_yaml(SIZING_YAML, tmp_path / "sizing_research.yaml", mutate)
    applied = load_research_sizing(path).with_tier()
    assert applied.target_vol == pytest.approx(0.40)
