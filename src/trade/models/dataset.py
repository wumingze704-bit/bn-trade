"""训练数据集加载与窗口采样（fp16 特征 + 样本索引）。

规格：docs/05-模型与训练.md §5
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch  # type: ignore[import-untyped]


@dataclass(frozen=True)
class Sample:
    """单个训练样本：币 ID、特征数组行索引、标签与权重（05 §5.1）。"""

    sym_id: int
    row_idx: int
    y: float
    r_mult: float
    w: float


class WindowDataset:
    """跨币 fp16 特征常驻内存，样本只存索引，按需切 L 窗口（05 §5.1）。

    每个币加载特征矩阵 `X_sym`（shape (N, F)，fp16），样本存 `(sym_id, row_idx, y, r_mult, w)`，
    `__getitem__` 返回 `X_sym[row_idx-L+1 : row_idx+1]`，不预先展开序列。
    """

    def __init__(
        self,
        features_dir: Path,
        labels_dir: Path,
        symbols: Sequence[str],
        feature_names: Sequence[str],
        *,
        window_L: int = 60,  # noqa: N803 - 与 05 §5.1 文档名 window_L 一致
        max_fill_ratio: float = 0.20,
        device: str = "cpu",
    ) -> None:
        """加载各币特征与标签，建立样本索引并做币种均衡。

        Args:
            features_dir: data/features/{run_id}。
            labels_dir: data/labels/{run_id}。
            symbols: 该集合内的币种 alias 列表（训练组或全组，05 §5.2）。
            feature_names: 特征列名顺序（manifest.features，05 §1 F 列）。
            window_L: 输入窗口长度（05 §1，默认 60）。
            max_fill_ratio: 窗口内补齐分钟占比上限，超过丢弃（05 §5.1）。
            device: 特征张量放置设备（cuda | cpu）；cuda 不可用时回退 cpu。
        """
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        self.window_L = window_L
        self.max_fill_ratio = max_fill_ratio
        self.device = device
        self.symbols = list(symbols)
        self.feature_names = list(feature_names)

        xs: list[torch.Tensor] = []
        raw: list[Sample] = []
        per_sym: list[int] = []

        for sym_id, symbol in enumerate(self.symbols):
            feat = pd.read_parquet(features_dir / f"{symbol}.parquet")
            lab = pd.read_parquet(labels_dir / f"{symbol}.parquet")
            open_time = feat["open_time"].to_numpy(dtype="int64")
            filled = (
                feat["is_filled"].to_numpy(dtype="int64")
                if "is_filled" in feat.columns
                else np.zeros(len(feat), dtype="int64")
            )
            x = np.ascontiguousarray(feat[self.feature_names].to_numpy(dtype="float16"))
            xs.append(torch.from_numpy(x).to(device))

            row_of = {int(t): i for i, t in enumerate(open_time)}
            # 数据段边界：相邻 open_time 间隔 > 1 分钟（03 §6），窗口不得跨段。
            boundary = np.zeros(len(open_time), dtype=bool)
            boundary[1:] = np.diff(open_time) > 60_000

            count = 0
            for open_t, y, r_mult, w in zip(
                lab["open_time"], lab["y"], lab["r_mult"], lab["weight"], strict=True
            ):
                row_idx = row_of.get(int(open_t))
                if row_idx is None:
                    continue
                start = row_idx - window_L + 1
                if start < 0:
                    continue  # 窗口不足（warmup 内）
                if boundary[start + 1 : row_idx + 1].any():
                    continue  # 窗口跨数据段
                if filled[start : row_idx + 1].mean() > max_fill_ratio:
                    continue  # 补齐分钟占比过高
                raw.append(Sample(sym_id, row_idx, float(y), float(r_mult), float(w)))
                count += 1
            per_sym.append(count)

        self.xs = xs
        self.samples = self._balance(raw, per_sym)

    @staticmethod
    def _balance(raw: list[Sample], per_sym: list[int]) -> list[Sample]:
        """币种均衡：w = 独特性权重 × (1/该币样本数)，再整体归一化（05 §5.2）。"""
        if not raw:
            return []
        balanced = [
            Sample(s.sym_id, s.row_idx, s.y, s.r_mult, s.w / max(per_sym[s.sym_id], 1)) for s in raw
        ]
        total = sum(s.w for s in balanced)
        scale = len(balanced) / total if total > 0 else 1.0
        return [Sample(s.sym_id, s.row_idx, s.y, s.r_mult, s.w * scale) for s in balanced]

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(
        self,
        index: int,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """返回 (窗口 x (L,F), y, r_mult, w)。"""
        s = self.samples[index]
        x = self.xs[s.sym_id][s.row_idx - self.window_L + 1 : s.row_idx + 1]
        return (
            x,
            torch.tensor(s.y, dtype=torch.float32, device=self.device),
            torch.tensor(s.r_mult, dtype=torch.float32, device=self.device),
            torch.tensor(s.w, dtype=torch.float32, device=self.device),
        )
