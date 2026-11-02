"""主模型 M1：因果 TCN + 注意力池化（PyTorch）。

规格：docs/05-模型与训练.md §3
"""

from __future__ import annotations

import torch  # type: ignore[import-untyped]
from torch import nn
from torch.nn import functional as F  # noqa: N812 - PyTorch 惯例别名


class CausalConv1d(nn.Module):
    """左侧补零的因果一维卷积（05 §3：t 位置只看到 ≤ t 的输入）。"""

    def __init__(
        self, in_channels: int, out_channels: int, kernel_size: int, dilation: int
    ) -> None:
        super().__init__()
        self.conv = nn.utils.weight_norm(
            nn.Conv1d(in_channels, out_channels, kernel_size, dilation=dilation)
        )
        self.pad = (kernel_size - 1) * dilation

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # 只在左侧补零 (k-1)*d，输出长度与输入一致。
        x = F.pad(x, (self.pad, 0))
        return self.conv(x)


class TCNBlock(nn.Module):
    """TCN 残差块：两层因果卷积 + GELU + Dropout + 残差连接（05 §3）。"""

    def __init__(self, channels: int, dilation: int, dropout: float) -> None:
        super().__init__()
        self.conv1 = CausalConv1d(channels, channels, kernel_size=3, dilation=dilation)
        self.conv2 = CausalConv1d(channels, channels, kernel_size=3, dilation=dilation)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        out = self.dropout(F.gelu(self.conv1(x)))
        out = self.dropout(F.gelu(self.conv2(out)))
        return out + residual


class AttnPool(nn.Module):
    """注意力池化：a = softmax(H·w)，输出 Σ a_i·H_i（05 §3）。"""

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.w = nn.Parameter(torch.randn(dim))

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        # h: (B, L, dim)
        a = torch.softmax(h @ self.w, dim=1)  # (B, L)
        return (h * a.unsqueeze(-1)).sum(dim=1)  # (B, dim)


class TCNModel(nn.Module):
    """因果 TCN + 注意力池化 + 双头（分类 logit / 回归 m）。

    规格：docs/05-模型与训练.md §3；感受野 = 1 + 2·(k-1)·Σd ≥ L。
    """

    def __init__(
        self,
        in_features: int,
        *,
        hidden: int = 64,
        blocks: int = 4,
        kernel: int = 3,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.project = nn.Sequential(nn.Linear(in_features, hidden), nn.LayerNorm(hidden))
        self.tcn_blocks = nn.ModuleList(
            TCNBlock(hidden, dilation=2**i, dropout=dropout) for i in range(blocks)
        )
        self.attn_pool = AttnPool(hidden)
        self.mlp = nn.Sequential(nn.Linear(2 * hidden, hidden), nn.GELU(), nn.Dropout(dropout))
        self.cls_head = nn.Linear(hidden, 1)
        self.reg_head = nn.Linear(hidden, 1)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """逐时间步表示 H：(B, L, hidden)，供因果性单测。"""
        h = self.project(x).transpose(1, 2)  # (B, hidden, L)
        for block in self.tcn_blocks:
            h = block(h)
        return h.transpose(1, 2)  # (B, L, hidden)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """返回 (logit (B,), m (B,))。"""
        h = self.encode(x)
        pooled = torch.cat([h[:, -1, :], self.attn_pool(h)], dim=1)  # (B, 2*hidden)
        z = self.mlp(pooled)
        logit = self.cls_head(z).squeeze(-1)
        m = self.reg_head(z).squeeze(-1)
        return logit, m
