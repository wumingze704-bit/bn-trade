"""训练数据集加载与窗口采样（fp16 特征 + 样本索引）。

规格：docs/05-模型与训练.md §5
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
    open_time: int = 0


def month_start_ms(month: str) -> int:
    """`YYYY-MM` → 该 UTC 自然月 1 日 00:00 的毫秒时间戳（03 §6）。"""
    year, mon = month.split("-")
    return int(datetime(int(year), int(mon), 1, tzinfo=UTC).timestamp() * 1000)


def month_end_ms(month: str) -> int:
    """`YYYY-MM` → 下个 UTC 自然月 1 日 00:00 的毫秒时间戳（不含）。"""
    year, mon = (int(part) for part in month.split("-"))
    if mon == 12:
        return int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000)
    return int(datetime(year, mon + 1, 1, tzinfo=UTC).timestamp() * 1000)


def _month_key(ts_ms: int) -> str:
    """毫秒时间戳 → `YYYY-MM`（UTC）。"""
    moment = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


class WindowDataset:
    """跨币 fp16 特征常驻内存，样本只存索引，按需切 L 窗口（05 §5.1）。

    每个币加载特征矩阵 `X_sym`（shape (N, F)，fp16），样本存 `(sym_id, row_idx, y, r_mult, w)`，
    `__getitem__` 返回 `X_sym[row_idx-L+1 : row_idx+1]`，不预先展开序列。

    支持：
    - `months`：只保留这些入选月份的样本，并按月份裁剪读取特征（05 §5.1 内存控制）；
    - `cache_dir`：把每个币的特征与 open_time 缓存为 fp16 `.npy`，用 `np.load(mmap_mode="r")`
      读取，避免 pandas → float32 中间副本；
    - `data_placement: pinned_cpu`：显存不足时特征留在锁页内存，训练时逐批拷到 GPU。
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
        device: str | None = None,
        months: Sequence[str] | None = None,
        cache_dir: Path | None = None,
        data_placement: str = "gpu",
        warmup_days: int = 30,
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
            months: 入选月份列表（`YYYY-MM`）；None 表示不过滤。只读取这些月份
                及其前 `window_L` 分钟的预热行（05 §5.1）。
            cache_dir: `.npy` fp16 缓存目录；None 时直接读 parquet。
            data_placement: `gpu`（默认）或 `pinned_cpu`（05 §5.1 显存不足退路）。
            warmup_days: 入选月份之前保留的特征预热天数（05 §5.1，默认 30）。
        """
        if data_placement not in {"gpu", "pinned_cpu"}:
            raise ValueError(f"data_placement 只能是 gpu / pinned_cpu，收到 {data_placement}")
        if data_placement == "pinned_cpu":
            device = "cpu"
        elif device is None:
            device = "cuda" if data_placement == "gpu" and torch.cuda.is_available() else "cpu"
        elif device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        self.window_L = window_L
        self.max_fill_ratio = max_fill_ratio
        self.device = device
        self.data_placement = data_placement
        self.symbols = list(symbols)
        self.feature_names = list(feature_names)
        self.months = list(months) if months is not None else None
        self.cache_dir = cache_dir

        x_arrays: list[torch.Tensor] = []
        raw: list[Sample] = []
        per_sym: list[int] = []

        for sym_id, symbol in enumerate(self.symbols):
            open_time, x = _load_symbol_features(
                features_dir,
                symbol,
                self.feature_names,
                months=self.months,
                window_len=window_L,
                warmup_days=warmup_days,
                cache_dir=cache_dir,
            )
            filled = _load_filled_flags(features_dir, symbol, open_time)
            x_arrays.append(_to_tensor(x, device=device, pinned=data_placement == "pinned_cpu"))

            lab = pd.read_parquet(
                labels_dir / f"{symbol}.parquet",
                columns=["open_time", "y", "r_mult", "weight"],
            )
            row_of = {int(t): i for i, t in enumerate(open_time)}
            # 数据段边界：相邻 open_time 间隔 > 1 分钟（03 §6），窗口不得跨段。
            boundary = np.zeros(len(open_time), dtype=bool)
            boundary[1:] = np.diff(open_time) > 60_000

            count = 0
            for open_t, y, r_mult, w in zip(
                lab["open_time"], lab["y"], lab["r_mult"], lab["weight"], strict=True
            ):
                if self.months is not None and _month_key(int(open_t)) not in self.months:
                    continue
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
                raw.append(Sample(sym_id, row_idx, float(y), float(r_mult), float(w), int(open_t)))
                count += 1
            per_sym.append(count)

        self.xs = x_arrays
        self.samples = self._balance(raw, per_sym)

    @staticmethod
    def _balance(raw: list[Sample], per_sym: list[int]) -> list[Sample]:
        """币种均衡：w = 独特性权重 × (1/该币样本数)，再整体归一化（05 §5.2）。"""
        if not raw:
            return []
        balanced = [
            Sample(s.sym_id, s.row_idx, s.y, s.r_mult, s.w / max(per_sym[s.sym_id], 1), s.open_time)
            for s in raw
        ]
        total = sum(s.w for s in balanced)
        scale = len(balanced) / total if total > 0 else 1.0
        return [
            Sample(s.sym_id, s.row_idx, s.y, s.r_mult, s.w * scale, s.open_time) for s in balanced
        ]

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

    def select(self, mask: np.ndarray) -> WindowDataset:
        """返回只保留 `mask` 为真的样本的新数据集（Purge / Embargo 用，03 §6）。

        特征数组与币种列表共享，不复制内存；样本权重已在原数据集完成币种均衡。
        """
        if len(mask) != len(self.samples):
            raise ValueError(f"mask 长度 {len(mask)} 与样本数 {len(self.samples)} 不一致")
        clone = object.__new__(WindowDataset)
        clone.window_L = self.window_L
        clone.max_fill_ratio = self.max_fill_ratio
        clone.device = self.device
        clone.data_placement = self.data_placement
        clone.symbols = self.symbols
        clone.feature_names = self.feature_names
        clone.months = self.months
        clone.cache_dir = self.cache_dir
        clone.xs = self.xs
        clone.samples = [sample for sample, keep in zip(self.samples, mask, strict=True) if keep]
        return clone

    def collate(
        self, indices: Sequence[int]
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """按索引批量切窗口，返回 (B, L, F) 与 (B,) 标签/权重（05 §6.2 GPU gather）。

        `pinned_cpu` 放置时返回锁页 CPU 张量，由训练循环一次拷贝到 GPU。
        """
        windows: list[torch.Tensor] = []
        ys: list[float] = []
        r_mults: list[float] = []
        weights: list[float] = []
        for index in indices:
            sample = self.samples[index]
            windows.append(
                self.xs[sample.sym_id][sample.row_idx - self.window_L + 1 : sample.row_idx + 1]
            )
            ys.append(sample.y)
            r_mults.append(sample.r_mult)
            weights.append(sample.w)
        x = torch.stack(windows, dim=0)
        return (
            x,
            torch.tensor(ys, dtype=torch.float32, device=x.device),
            torch.tensor(r_mults, dtype=torch.float32, device=x.device),
            torch.tensor(weights, dtype=torch.float32, device=x.device),
        )


def peak_memory_mb() -> float:
    """返回当前 CUDA 峰值显存占用（MB）；无 CUDA 时返回 0（05 §6.2 实测显存）。"""
    if not torch.cuda.is_available():
        return 0.0
    return float(torch.cuda.max_memory_allocated() / (1024 * 1024))


def _to_tensor(x: np.ndarray, *, device: str, pinned: bool) -> torch.Tensor:
    """把 fp16 特征数组转为张量；pinned_cpu 时固定在锁页内存（05 §5.1）。"""
    if not x.flags.writeable:
        # memmap 为只读视图，torch.from_numpy 需要可写数组。
        x = np.array(x, copy=True)
    tensor = torch.from_numpy(x)
    if pinned:
        return tensor.pin_memory()
    return tensor.to(device)


def _load_symbol_features(
    features_dir: Path,
    symbol: str,
    feature_names: Sequence[str],
    *,
    months: Sequence[str] | None,
    window_len: int,
    warmup_days: int,
    cache_dir: Path | None,
) -> tuple[np.ndarray, np.ndarray]:
    """读取单币特征与 open_time，优先使用 `.npy` fp16 memmap 缓存。

    Args:
        features_dir: 特征 parquet 目录。
        symbol: 币种 alias。
        feature_names: 特征列顺序。
        months: 入选月份；有值时按月份裁剪读取范围（含预热）。
        window_L: 窗口长度，用于保留窗口预热分钟。
        warmup_days: 入选月份之前的预热天数（05 §5.1 的 30 天）。
        cache_dir: 缓存目录；None 时直接读 parquet。

    Returns:
        (open_time int64 数组, 特征 fp16 数组 (N, F))。
    """
    cache_features = cache_dir / f"{symbol}.features.npy" if cache_dir is not None else None
    cache_open_time = cache_dir / f"{symbol}.open_time.npy" if cache_dir is not None else None
    if cache_features is not None and cache_features.is_file() and cache_open_time.is_file():
        open_time = np.load(cache_open_time)
        x = np.load(cache_features, mmap_mode="r")
        return open_time, np.asarray(x)

    frame = pd.read_parquet(features_dir / f"{symbol}.parquet")
    if months is not None:
        start_ms = month_start_ms(min(months)) - max(window_len * 60_000, warmup_days * 86_400_000)
        end_ms = month_end_ms(max(months))
        frame = frame[(frame["open_time"] >= start_ms) & (frame["open_time"] < end_ms)]
    frame = frame.sort_values("open_time")
    open_time = frame["open_time"].to_numpy(dtype="int64")
    x = np.ascontiguousarray(frame[list(feature_names)].to_numpy(dtype="float16"))

    if cache_dir is not None and cache_features is not None and cache_open_time is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if months is None:
            # 只在加载完整数据窗口时写缓存，避免把裁剪后的数组误当作全量缓存。
            np.save(cache_features, x)
            np.save(cache_open_time, open_time)
    return open_time, x


def _load_filled_flags(features_dir: Path, symbol: str, open_time: np.ndarray) -> np.ndarray:
    """读取 is_filled（按 open_time 对齐）；缺列时视为全部非补齐。"""
    frame = pd.read_parquet(features_dir / f"{symbol}.parquet")
    if "is_filled" not in frame.columns:
        return np.zeros(len(open_time), dtype="int64")
    lookup = dict(
        zip(
            frame["open_time"].to_numpy(dtype="int64"),
            frame["is_filled"].to_numpy(dtype="int64"),
            strict=True,
        )
    )
    return np.asarray([int(lookup.get(int(t), 0)) for t in open_time], dtype="int64")
