"""候选冻结文件与上线前置条件测试（docs/07 §3、§5）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from trade.live.candidate import (
    CandidateError,
    live_precondition_errors,
    load_candidate,
    parse_candidate_document,
    run_mode_errors,
    verify_frozen,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "repo"
    candidates = root / "docs" / "research" / "candidates"
    candidates.mkdir(parents=True)
    frozen = root / "configs" / "strategies" / "round_R1.yaml"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("round: R1\n", encoding="utf-8")
    ledger = root / "docs" / "research" / "ledger.yaml"
    ledger.write_text(
        yaml.safe_dump(
            {
                "candidates": [
                    {
                        "id": "C1",
                        "family": "xs_momentum_ls",
                        "final": "pass",
                        "ops": "live_ready",
                        "final_window": "W1",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    document = candidates / "C1.md"
    document.write_text(
        "结论：FROZEN\n\n```yaml\n"
        "family: xs_momentum_ls\n"
        "exposure: neutral\n"
        "rebalance_hours: 24\n"
        "risk_per_trade: 0.0075\n"
        "framework_dir: src/trade/research\n"
        "params: {lookback_days: 14, top_k: 5}\n"
        "frozen:\n"
        "  - path: configs/strategies/round_R1.yaml\n"
        f"    sha256: {_sha(frozen)}\n"
        "```\n",
        encoding="utf-8",
    )
    return root, candidates, ledger


def test_解析候选并核对冻结文件(tmp_path: Path) -> None:
    root, candidates, ledger = _setup(tmp_path)
    spec, entry = load_candidate(
        root=root, candidates_dir=candidates, ledger_path=ledger, candidate_id="C1"
    )
    assert spec.family == "xs_momentum_ls"
    assert spec.rebalance_hours == 24
    verify_frozen(spec, root)
    assert entry["ops"] == "live_ready"


def test_冻结文件被修改时拒绝(tmp_path: Path) -> None:
    root, candidates, ledger = _setup(tmp_path)
    spec, _ = load_candidate(
        root=root, candidates_dir=candidates, ledger_path=ledger, candidate_id="C1"
    )
    (root / "configs" / "strategies" / "round_R1.yaml").write_text("round: R2\n", encoding="utf-8")
    with pytest.raises(CandidateError, match="SHA256"):
        verify_frozen(spec, root)


def test_缺少字段或非法调仓周期时报错(tmp_path: Path) -> None:
    path = tmp_path / "C1.md"
    path.write_text("结论：FROZEN\n```yaml\nfamily: xs_momentum_ls\n```\n", encoding="utf-8")
    with pytest.raises(CandidateError, match="缺少字段"):
        parse_candidate_document(path, "C1")
    _root, candidates, _ledger = _setup(tmp_path / "x")
    document = candidates / "C2.md"
    document.write_text(
        "结论：FROZEN\n```yaml\n"
        "family: xs_momentum_ls\nexposure: neutral\nrebalance_hours: 5\n"
        "risk_per_trade: 0.0075\nframework_dir: src/trade/research\nparams: {}\n"
        "frozen:\n  - path: a\n    sha256: " + "0" * 64 + "\n```\n",
        encoding="utf-8",
    )
    with pytest.raises(CandidateError, match="24 的因数"):
        parse_candidate_document(document, "C2")


def test_live_前置条件逐一核对(tmp_path: Path) -> None:
    root, candidates, ledger = _setup(tmp_path)
    spec, entry = load_candidate(
        root=root, candidates_dir=candidates, ledger_path=ledger, candidate_id="C1"
    )
    final_dir = root / "docs" / "research" / "final"
    shadow_dir = root / "docs" / "research" / "shadow"
    final_dir.mkdir(parents=True)
    shadow_dir.mkdir(parents=True)
    errors = live_precondition_errors(
        spec=spec, entry=entry, final_dir=final_dir, shadow_dir=shadow_dir, root=root
    )
    assert any("结论为 缺失" in item for item in errors)
    (final_dir / "C1_W1.md").write_text("结论：PASS\n", encoding="utf-8")
    (shadow_dir / "C1.md").write_text("结论：LIVE_READY\n", encoding="utf-8")
    assert (
        live_precondition_errors(
            spec=spec, entry=entry, final_dir=final_dir, shadow_dir=shadow_dir, root=root
        )
        == []
    )


def test_启动模式校验(tmp_path: Path) -> None:
    root, candidates, ledger = _setup(tmp_path)
    spec, entry = load_candidate(
        root=root, candidates_dir=candidates, ledger_path=ledger, candidate_id="C1"
    )
    final_dir = root / "docs" / "research" / "final"
    shadow_dir = root / "docs" / "research" / "shadow"
    errors = run_mode_errors(
        mode="live",
        i_understand_the_risk=False,
        spec=spec,
        entry=entry,
        final_dir=final_dir,
        shadow_dir=shadow_dir,
        root=root,
        env={},
    )
    assert any("--i-understand-the-risk" in item for item in errors)
    assert any("BINANCE_API_KEY" in item for item in errors)
    paper_errors = run_mode_errors(
        mode="paper",
        i_understand_the_risk=False,
        spec=spec,
        entry=entry,
        final_dir=final_dir,
        shadow_dir=shadow_dir,
        root=root,
        env={},
    )
    assert any("BINANCE_DEMO_API_KEY" in item for item in paper_errors)
    assert (
        run_mode_errors(
            mode="shadow",
            i_understand_the_risk=False,
            spec=spec,
            entry=entry,
            final_dir=final_dir,
            shadow_dir=shadow_dir,
            root=root,
            env={},
        )
        == []
    )
