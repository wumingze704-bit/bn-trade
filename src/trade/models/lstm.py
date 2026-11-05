"""消融对照模型 M2：2 层 LSTM。

规格：docs/05-模型与训练.md §3（M2 段落）
"""

from __future__ import annotations

import torch  # type: ignore[import-untyped]
from torch import nn


class LSTMModel(nn.Module):
    """Linear → LSTM(2 层) → 最后时刻 → 同 M1 的 MLP 与双头（05 §3）。"""

    def __init__(
        self,
        in_features: int,
        *,
        hidden: int = 64,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.project = nn.Sequential(nn.Linear(in_features, hidden), nn.LayerNorm(hidden))
        self.lstm = nn.LSTM(
            hidden,
            hidden,
            num_layers=2,
            dropout=dropout,
            batch_first=True,
        )
        self.mlp = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.Dropout(dropout))
        self.cls_head = nn.Linear(hidden, 1)
        self.reg_head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (logit (B,), m (B,))。"""
        h = self.project(x)
        out, _ = self.lstm(h)
        z = self.mlp(out[:, -1, :])
        return self.cls_head(z).squeeze(-1), self.reg_head(z).squeeze(-1)
