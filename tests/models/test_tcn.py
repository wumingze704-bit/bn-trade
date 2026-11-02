"""主模型 M1 因果 TCN 测试。

规格：docs/05-模型与训练.md §3；08 §4 关键测试（TCN 因果性）
"""

from __future__ import annotations

import torch  # type: ignore[import-untyped]

from trade.models.tcn import TCNModel


def test_forward_shape() -> None:
    """输出 logit 与 m 形状正确（05 §3 双头）。"""
    model = TCNModel(in_features=37)
    x = torch.randn(4, 60, 37)
    logit, m = model(x)
    assert logit.shape == (4,)
    assert m.shape == (4,)


def test_causal_encoding() -> None:
    """因果性：修改 t+1 之后的数据，t 及之前的逐时间步表示不变。"""
    torch.manual_seed(0)
    model = TCNModel(in_features=37, hidden=16).eval()
    x = torch.randn(2, 60, 37)
    t = 30
    x2 = x.clone()
    x2[:, t + 1 :, :] = torch.randn_like(x2[:, t + 1 :, :])

    h = model.encode(x)
    h2 = model.encode(x2)
    assert torch.allclose(h[:, : t + 1, :], h2[:, : t + 1, :], atol=1e-5)


def test_receptive_field_covers_L() -> None:
    """感受野 ≥ L：首位置脉冲能传播到末位置（05 §3 感受野 61 ≥ L=60）。"""
    torch.manual_seed(0)
    model = TCNModel(in_features=1, hidden=8).eval()
    x = torch.zeros(1, 60, 1)
    x[0, 0, 0] = 1.0
    h = model.encode(x)
    assert h[0, -1, :].abs().max().item() > 1e-4
