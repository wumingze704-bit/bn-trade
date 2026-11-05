"""数据集加载器 WindowDataset 测试。

规格：docs/05-模型与训练.md §5
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch  # type: ignore[import-untyped]

from trade.models.dataset import WindowDataset, month_end_ms, month_start_ms, peak_memory_mb

_FEATURES = ("f0", "f1")


def _make_coin(
    data_root,
    alias: str,
    n: int = 120,
    *,
    gap_after: int | None = None,
    fill_rows: list[int] | None = None,
) -> np.ndarray:
    """构造单币的特征 + 标签 parquet，返回 open_time 数组。"""
    features_dir = data_root / "features" / "run"
    labels_dir = data_root / "labels" / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    open_time = (np.arange(n) * 60_000 + 1_700_000_000_000).astype("int64")
    if gap_after is not None:
        # 在 gap_after 之后制造 10 分钟 gap，形成数据段边界（03 §6）。
        open_time[gap_after:] += 600_000

    feat = pd.DataFrame({name: np.arange(n, dtype="float16") for name in _FEATURES})
    feat["open_time"] = open_time
    feat["is_filled"] = np.zeros(n, dtype="int64")
    if fill_rows is not None:
        feat.loc[fill_rows, "is_filled"] = 1
    feat.to_parquet(features_dir / f"{alias}.parquet", index=False)

    # 样本取最后 5 个 open_time
    lab = pd.DataFrame(
        {
            "open_time": open_time[-5:],
            "y": np.array([1, 0, 1, 0, 1]),
            "r_mult": np.array([1.0, -0.5, 0.5, -1.0, 0.2]),
            "weight": np.ones(5),
        }
    )
    lab.to_parquet(labels_dir / f"{alias}.parquet", index=False)
    return open_time


def test_window_slice(tmp_path) -> None:
    """窗口切片：形状 (L, F)，特征值等于对应行号。"""
    _make_coin(tmp_path, "C01", n=120)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
    )
    assert len(ds) == 5
    x, y, r_mult, w = ds[0]
    assert x.shape == (60, 2)
    assert x.dtype == torch.float16
    # 第一个样本对应 open_time[-5]，row_idx = 115，窗口 = [56, 115]，特征值 = arange(56, 116)
    assert int(x[0, 0].item()) == 56
    assert int(x[-1, 0].item()) == 115
    assert int(y.item()) == 1


def test_window_too_short_dropped(tmp_path) -> None:
    """窗口不足（样本太靠前）时丢弃：n < L 时无样本。"""
    _make_coin(tmp_path, "C01", n=50)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
    )
    assert len(ds) == 0


def test_segment_boundary_dropped(tmp_path) -> None:
    """窗口跨数据段边界的样本丢弃。"""
    # gap 在第 100 行之后，样本 row_idx 在 100~119，窗口 [row_idx-59, row_idx] 若覆盖 gap 则丢弃
    _make_coin(tmp_path, "C01", n=120, gap_after=100)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
    )
    # 样本 row_idx=115..119，窗口起点=56..60，都不跨 100；但 gap 在 100 行后，
    # 窗口 [56,115] 包含行 100~115（gap 边界在 100 与 101 之间），故应丢弃。
    assert len(ds) == 0


def test_fill_ratio_dropped(tmp_path) -> None:
    """窗口内补齐分钟占比 > 20% 的样本丢弃。"""
    # 填充最后 30 行（窗口 60 行中 30 行补齐，占比 50%）
    _make_coin(tmp_path, "C01", n=120, fill_rows=list(range(90, 120)))
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        max_fill_ratio=0.20,
    )
    assert len(ds) == 0


def test_balance_weights(tmp_path) -> None:
    """币种均衡：样本数不同的两个币，均衡后每币权重之和相等。"""
    _make_coin(tmp_path, "C01", n=120)
    _make_coin(tmp_path, "C02", n=120)
    # C02 标签只保留 1 个样本（手工重写，模拟样本数差异）
    lab = pd.read_parquet(tmp_path / "labels" / "run" / "C02.parquet")
    lab.iloc[:1].to_parquet(tmp_path / "labels" / "run" / "C02.parquet", index=False)

    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01", "C02"],
        _FEATURES,
        window_L=60,
    )
    w0 = sum(ds[i][3].item() for i in range(len(ds)) if ds.samples[i].sym_id == 0)
    w1 = sum(ds[i][3].item() for i in range(len(ds)) if ds.samples[i].sym_id == 1)
    assert w0 == pytest.approx(w1, rel=1e-6)


def _make_two_month_coin(data_root, alias: str = "C01") -> None:
    """构造跨 2025-01 与 2025-02 的特征与标签（每月各 1 个样本）。"""
    features_dir = data_root / "features" / "run"
    labels_dir = data_root / "labels" / "run"
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    jan = month_start_ms("2025-01")
    feb = month_start_ms("2025-02")
    # 每月 100 分钟，每月最后 1 分钟是标签样本。
    times = np.concatenate(
        [
            jan + np.arange(100, dtype="int64") * 60_000,
            feb + np.arange(100, dtype="int64") * 60_000,
        ]
    )
    feat = pd.DataFrame({name: np.arange(len(times), dtype="float16") for name in _FEATURES})
    feat["open_time"] = times
    feat["is_filled"] = 0
    feat.to_parquet(features_dir / f"{alias}.parquet", index=False)
    lab = pd.DataFrame(
        {
            "open_time": [times[99], times[-1]],
            "y": [0, 1],
            "r_mult": [0.0, 1.0],
            "weight": [1.0, 1.0],
        }
    )
    lab.to_parquet(labels_dir / f"{alias}.parquet", index=False)


def test_月份边界() -> None:
    assert month_start_ms("2025-01") == 1_735_689_600_000
    assert month_end_ms("2025-01") == month_start_ms("2025-02")
    assert month_end_ms("2024-12") == month_start_ms("2025-01")


def test_months_filter_only_keeps_selected_months(tmp_path) -> None:
    """入选月份过滤：只保留指定月份的标签样本（05 §5.1）。"""
    _make_two_month_coin(tmp_path)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=10,
        months=["2025-02"],
    )
    assert len(ds) == 1
    _, y, _, _ = ds[0]
    assert int(y.item()) == 1


def test_cache_dir_writes_npy_and_reloads(tmp_path) -> None:
    """缓存目录写出 fp16 .npy，二次加载直接读 memmap 且结果一致（05 §5.1）。"""
    _make_coin(tmp_path, "C01", n=120)
    cache_dir = tmp_path / "cache"
    first = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        cache_dir=cache_dir,
    )
    assert (cache_dir / "C01.features.npy").is_file()
    assert (cache_dir / "C01.open_time.npy").is_file()
    array = np.load(cache_dir / "C01.features.npy")
    assert array.dtype == np.float16

    second = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        cache_dir=cache_dir,
    )
    assert len(second) == len(first)
    assert torch.equal(second[0][0], first[0][0])


def test_pinned_cpu_placement(tmp_path) -> None:
    """显存不足退路：pinned_cpu 放置时数据在锁页 CPU 内存（05 §5.1）。"""
    _make_coin(tmp_path, "C01", n=120)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        data_placement="pinned_cpu",
    )
    x, _, _, _ = ds[0]
    assert x.device.type == "cpu"
    assert x.is_pinned()


def test_invalid_placement_rejected(tmp_path) -> None:
    _make_coin(tmp_path, "C01", n=120)
    with pytest.raises(ValueError, match="data_placement"):
        WindowDataset(
            tmp_path / "features" / "run",
            tmp_path / "labels" / "run",
            ["C01"],
            _FEATURES,
            data_placement="disk",
        )


def test_collate_batch_shape(tmp_path) -> None:
    """批量 gather：collate 返回 (B, L, F) 与 (B,) 标签（05 §6.2）。"""
    _make_coin(tmp_path, "C01", n=120)
    ds = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
    )
    x, y, r_mult, w = ds.collate([0, 1, 2])
    assert x.shape == (3, 60, 2)
    assert y.shape == (3,)
    assert r_mult.shape == (3,)
    assert w.shape == (3,)


def test_peak_memory_mb_without_cuda() -> None:
    """无 CUDA 时显存实测返回 0（05 §6.2）。"""
    if torch.cuda.is_available():
        pytest.skip("有 CUDA，跳过无显卡分支")
    assert peak_memory_mb() == 0.0


@pytest.mark.skipif(not torch.cuda.is_available(), reason="无 CUDA")
def test_gpu_cpu_consistent(tmp_path) -> None:
    """GPU 与 CPU 放置产出的窗口一致（08 P4 单测）。"""
    _make_coin(tmp_path, "C01", n=120)
    cpu = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        device="cpu",
    )
    gpu = WindowDataset(
        tmp_path / "features" / "run",
        tmp_path / "labels" / "run",
        ["C01"],
        _FEATURES,
        window_L=60,
        device="cuda",
    )
    x_cpu, y_cpu, r_cpu, w_cpu = cpu[0]
    x_gpu, y_gpu, r_gpu, w_gpu = gpu[0]
    assert torch.equal(x_cpu, x_gpu.cpu())
    assert y_cpu.item() == y_gpu.item()
    assert r_cpu.item() == r_gpu.item()
    assert w_cpu.item() == w_gpu.item()
