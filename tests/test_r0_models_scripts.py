"""R0 缺陷修复回归测试：Platt 校准、模型产物、TCN kernel、损失口径与调参辅助函数。

规格：docs/05 §3、§4、§6、§7；docs/06 §7 S5
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
import torch

from scripts.backtest import _monthly_fold_net
from scripts.tuning_exits import _no_halt_sizing as _exits_no_halt
from scripts.tuning_models import _no_halt_sizing as _models_no_halt
from trade.common.sizing import SizingConfig
from trade.models.artifact import ArtifactError, Predictor
from trade.models.calibration import fit_platt, platt_sigmoid
from trade.models.config import ModelConfig
from trade.models.tcn import TCNModel
from trade.models.train import multitask_loss, warmup_cosine_factor


def _sizing() -> SizingConfig:
    return SizingConfig(
        initial_equity=100.0,
        mode="fixed_fraction",
        position_frac=0.2,
        leverage=5,
        max_positions=3,
        base="equity",
        risk_per_trade=0.005,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.2,
        max_consecutive_losses=6,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def test_Platt校准能吸收pos_weight造成的logit平移() -> None:
    """R0-12：`p = σ(a·z + b)` 能把整体平移的 logit 校准回真实正样本率。"""

    rng = np.random.default_rng(7)
    n = 20000
    y = rng.random(n) < 0.2
    logit_true = rng.normal(-1.4, 1.0, n)
    shift = float(np.log(np.sqrt(0.8 / 0.2)))
    logit = np.where(y, logit_true + shift, logit_true + shift + 0.0)
    a, b = fit_platt(logit, y.astype("float64"))
    p = platt_sigmoid(logit, a, b)
    assert abs(float(p.mean()) - 0.2) < 0.02
    assert a > 0.0


def test_模型产物缺Platt参数时报错() -> None:
    """R0-11f：model_meta 缺 calibration 时不得静默按 1.0 回退。"""
    with pytest.raises(ArtifactError, match="calibration"):
        Predictor(
            meta={"model_id": "m", "arch": "lgbm", "temperature": 1.0, "threshold": 0.3},
            state_dicts=None,
            booster=None,
        )


def test_TCN的kernel配置生效() -> None:
    """R0-11a：TCNBlock 的卷积核长度由配置决定。"""
    model = TCNModel(3, hidden=8, blocks=1, kernel=5, dropout=0.0)
    conv = model.tcn_blocks[0].conv1.conv
    assert int(conv.kernel_size[0]) == 5


def test_BCE按权重求加权平均() -> None:
    """R0-11c：BCE 与 Huber/focal 同一口径（sum(w·l)/sum(w)）。"""
    cfg = ModelConfig(arch="lgbm", loss="bce", lambda_reg=0.0)
    logits = torch.tensor([0.5, -0.5, 1.0], dtype=torch.float32)
    y = torch.tensor([1.0, 0.0, 1.0], dtype=torch.float32)
    r_mult = torch.zeros(3)
    w = torch.tensor([1.0, 2.0, 4.0], dtype=torch.float32)
    loss = multitask_loss(logits, r_mult, y, r_mult, w, cfg, torch.tensor(1.0))
    raw = torch.nn.functional.binary_cross_entropy_with_logits(logits, y, reduction="none")
    expected = float((raw * w).sum() / w.sum())
    assert abs(float(loss) - expected) < 1e-6


def test_预热期学习率不再形同虚设() -> None:
    """R0-11g：warmup_epochs=1 时第 0 轮学习率因子为 0.5，不是 1.0。"""
    assert abs(warmup_cosine_factor(0, 1, 10) - 0.5) < 1e-12
    assert warmup_cosine_factor(10, 1, 10) >= 0.0


@pytest.mark.parametrize("helper", [_models_no_halt, _exits_no_halt])
def test_调参评估关闭停机(helper) -> None:
    """R0-2：调参评估用仓位关闭 R6/R8/R7 停机。"""
    cfg = helper(_sizing())
    assert cfg.max_drawdown_halt > 0.9
    assert cfg.daily_loss_halt > 0.9
    assert cfg.max_consecutive_losses >= 1000


class _FakeTrade:
    def __init__(self, ts_ms: int, net: float) -> None:
        self.signal_open_time = ts_ms
        self.net_pnl = net


class _FakeResult:
    def __init__(self, trades: list[_FakeTrade]) -> None:
        self.trades = trades


def test_S5按日历月映射到折() -> None:
    """R0-7：有交易月份缺月时，折号仍按测试段日历月映射，不按序号。"""

    def _ms(month: str) -> int:
        year, mon = (int(part) for part in month.split("-"))
        return int(datetime(year, mon, 1, tzinfo=UTC).timestamp() * 1000)

    test_months = ["2025-10", "2025-11", "2025-12", "2026-01"]
    result = _FakeResult([_FakeTrade(_ms("2025-12"), 2.0), _FakeTrade(_ms("2026-01"), 3.0)])
    _, fold_net = _monthly_fold_net(result, None, test_months=test_months)
    # 2025-12 属于 F2、2026-01 属于 F2（第 3、4 个月）→ 合并为一条
    assert fold_net == [5.0]
