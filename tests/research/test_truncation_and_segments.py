"""max_positions 截断与 R5 逐折成分参数的仓位模式（缺陷 D19、D23 的复现测试）。

规格：docs/13 §3 第 6 条（多空交替截断、不得先排满多头）、§5（R5 成分按模式取）。
"""

from __future__ import annotations

from types import SimpleNamespace

from trade.research.pipeline import build_segments
from trade.research.portfolio import PlannedPosition, _truncate_alternating
from trade.research.rolling import Fold, FoldChoice

_START_MS = 1_704_067_200_000
_DAY_MS = 86_400_000


def _position(symbol: str, side: int) -> PlannedPosition:
    return PlannedPosition(
        symbol=symbol,
        side=side,
        sl=0.15,
        notional=1_000.0,
        price_estimate=100.0,
        step_size=0.001,
        min_notional=5.0,
    )


def test_一个多头腿加N个空头腿不会被截断成一侧() -> None:
    """R12 的 N = 9 + BTC = 10 个仓位必须全部保留（1 号第八部分 R12）。"""
    planned = [_position("BTCUSDT", 1)] + [_position(f"A{index}USDT", -1) for index in range(9)]
    kept = _truncate_alternating(planned, 10)
    assert len(kept) == 10
    assert sum(1 for item in kept if item.side < 0) == 9


def test_截断按多空交替顺序而不先排满多头() -> None:
    planned = [_position(f"L{index}", 1) for index in range(5)] + [
        _position(f"S{index}", -1) for index in range(5)
    ]
    kept = _truncate_alternating(planned, 4)
    assert [item.side for item in kept] == [1, -1, 1, -1]


def test_分段按选中参数的仓位模式取成分(monkeypatch) -> None:
    captured: list[tuple[str, object]] = []

    def _fake_build(family, params, *, stop_max, components=None):  # noqa: ANN001
        captured.append((str(params.get("sizing_mode")), components))
        return SimpleNamespace(name=family)

    monkeypatch.setattr("trade.research.pipeline.build_strategy", _fake_build)

    folds = [
        Fold(index=0, start_ms=_START_MS, end_ms=_START_MS + 30 * _DAY_MS),
        Fold(index=1, start_ms=_START_MS + 30 * _DAY_MS, end_ms=_START_MS + 60 * _DAY_MS),
    ]
    params_a = {"top_k": 3, "sizing_mode": "fixed_risk"}
    params_b = {"top_k": 5, "sizing_mode": "vol_target"}
    choices = [
        FoldChoice(fold=folds[0], params_key="a", train_sharpe=1.0, train_trades=40),
        FoldChoice(fold=folds[1], params_key="b", train_sharpe=1.0, train_trades=40),
    ]
    components_by_mode = {
        "fixed_risk": [{"xs_momentum_ls": {"lookback_days": 7}}, {"xs_momentum_ls": {}}],
        "vol_target": [{"xs_momentum_ls": {}}, {"xs_momentum_ls": {"lookback_days": 14}}],
    }
    build_segments(
        family="ensemble",
        folds=folds,
        choices=choices,
        params_by_key={"a": params_a, "b": params_b},
        config=SimpleNamespace(execution=SimpleNamespace(stop_max=0.15)),  # type: ignore[arg-type]
        components_by_mode=components_by_mode,
    )
    assert captured[0] == ("fixed_risk", {"xs_momentum_ls": {"lookback_days": 7}})
    assert captured[1] == ("vol_target", {"xs_momentum_ls": {"lookback_days": 14}})
