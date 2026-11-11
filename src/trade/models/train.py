"""M0 / M1 / M2 训练：早停、bf16、种子集成与 train_log.csv。

规格：docs/05-模型与训练.md §3、§4、§5、§6
"""

from __future__ import annotations

import contextlib
import copy
import csv
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch  # type: ignore[import-untyped]
from torch import nn
from torch.nn import functional as F  # noqa: N812 - PyTorch 惯例别名

from trade.models.config import ModelConfig
from trade.models.dataset import WindowDataset, peak_memory_mb
from trade.models.lightgbm import tabular_features_matrix, train_lgbm
from trade.models.lstm import LSTMModel
from trade.models.tcn import TCNModel

_MS_PER_MONTH = 30.44 * 24 * 3600 * 1000


@dataclass
class TrainingResult:
    """单次训练（全部种子）的结果与日志（05 §6 第 7 条）。"""

    arch: str
    arch_params: dict[str, Any]
    state_dicts: list[dict[str, torch.Tensor]] = field(default_factory=list)
    best_epochs: list[int] = field(default_factory=list)
    log_rows: list[dict[str, Any]] = field(default_factory=list)
    val_logits: list[np.ndarray] = field(default_factory=list)
    val_m: list[np.ndarray] = field(default_factory=list)
    val_y: np.ndarray | None = None
    val_pr_auc: float = 0.0


@dataclass
class LGBMResult:
    """M0 LightGBM 训练结果与验证集预测。"""

    booster: Any
    val_prob: np.ndarray
    val_y: np.ndarray
    val_pr_auc: float
    best_iteration: int


def set_deterministic(seed: int) -> None:
    """固定 torch / numpy / random 随机源（05 §6 第 4 条）。"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.use_deterministic_algorithms(True, warn_only=True)


def build_model(arch: str, in_features: int, cfg: ModelConfig) -> nn.Module:
    """按配置构造 M0/M1/M2 中的 PyTorch 模型（05 §3）。"""
    if arch == "tcn":
        return TCNModel(
            in_features,
            hidden=cfg.hidden,
            blocks=cfg.blocks,
            kernel=cfg.kernel,
            dropout=cfg.dropout,
        )
    if arch == "lstm":
        return LSTMModel(in_features, hidden=cfg.hidden, dropout=cfg.dropout)
    raise ValueError(f"arch={arch} 不是 PyTorch 模型（M0 用 train_lgbm）")


def average_precision(y: np.ndarray, p: np.ndarray) -> float:
    """PR-AUC（average precision，梯形求和，无 sklearn 依赖）。"""
    y = np.asarray(y, dtype="float64")
    p = np.asarray(p, dtype="float64")
    positives = float(y.sum())
    if positives <= 0 or len(y) == 0:
        return 0.0
    order = np.argsort(-p, kind="stable")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    fp = np.cumsum(1.0 - y_sorted)
    precision = tp / np.maximum(tp + fp, 1e-12)
    recall = tp / positives
    recall_prev = np.concatenate(([0.0], recall[:-1]))
    return float(np.sum((recall - recall_prev) * precision))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """ROC-AUC（秩和公式）。"""
    y = np.asarray(y, dtype="float64")
    p = np.asarray(p, dtype="float64")
    n_pos = float(y.sum())
    n_neg = float(len(y) - n_pos)
    if n_pos <= 0 or n_neg <= 0:
        return 0.5
    order = np.argsort(p, kind="stable")
    ranks = np.empty(len(p), dtype="float64")
    ranks[order] = np.arange(1, len(p) + 1, dtype="float64")
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def top_k_precision(y: np.ndarray, p: np.ndarray, fraction: float = 0.05) -> float:
    """Top-k 精确率：概率最高的 fraction 样本中 y=1 的比例（05 §8）。"""
    if len(y) == 0:
        return 0.0
    k = max(1, int(math.ceil(len(y) * fraction)))
    order = np.argsort(-np.asarray(p), kind="stable")[:k]
    return float(np.asarray(y)[order].mean())


def multitask_loss(
    logits: torch.Tensor,
    m: torch.Tensor,
    y: torch.Tensor,
    r_mult: torch.Tensor,
    w: torch.Tensor,
    cfg: ModelConfig,
    pos_weight: torch.Tensor,
) -> torch.Tensor:
    """L = 分类损失 + λ·Huber(m, r_mult)，样本权重 w（05 §4）。

    `loss: focal` 时用 focal loss（γ = cfg.focal_gamma）替代 BCE。
    """
    if cfg.loss == "focal":
        p = torch.sigmoid(logits)
        pt = y * p + (1.0 - y) * (1.0 - p)
        cls = -((1.0 - pt).clamp(min=0.0) ** cfg.focal_gamma) * torch.log(pt.clamp(min=1e-12))
        cls = (cls * w).sum() / w.sum().clamp(min=1e-12)
    else:
        cls = F.binary_cross_entropy_with_logits(
            logits, y, weight=w, pos_weight=pos_weight.to(logits.device)
        )
    huber = F.smooth_l1_loss(m, r_mult, beta=1.0, reduction="none")
    reg = (huber * w).sum() / w.sum().clamp(min=1e-12)
    return cls + cfg.lambda_reg * reg


def predict_dataset(
    model: nn.Module, dataset: WindowDataset, *, batch_size: int, device: str
) -> tuple[np.ndarray, np.ndarray]:
    """按时间顺序推理整个数据集，返回 (logits, m)。"""
    model.eval()
    logits_out: list[np.ndarray] = []
    m_out: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(dataset), batch_size):
            indices = list(range(start, min(len(dataset), start + batch_size)))
            x, _, _, _ = dataset.collate(indices)
            x = x.to(device).float()
            logit, m = model(x)
            logits_out.append(logit.detach().float().cpu().numpy())
            m_out.append(m.detach().float().cpu().numpy())
    if not logits_out:
        return np.zeros(0, dtype="float32"), np.zeros(0, dtype="float32")
    return np.concatenate(logits_out), np.concatenate(m_out)


def _labels(dataset: WindowDataset) -> np.ndarray:
    return np.asarray([sample.y for sample in dataset.samples], dtype="float64")


def _pos_weight(y: np.ndarray) -> torch.Tensor:
    """pos_weight π = sqrt(负样本数 / 正样本数)（05 §4，只用训练集计算）。"""
    positives = float(y.sum())
    negatives = float(len(y) - positives)
    if positives <= 0:
        return torch.tensor(1.0)
    return torch.tensor(math.sqrt(max(negatives, 1.0) / positives), dtype=torch.float32)


def _decay_weights(dataset: WindowDataset, cfg: ModelConfig) -> np.ndarray | None:
    """按月龄衰减样本权重：0.5 ** (月龄 / halflife)（05 §6.1、03 §6）。"""
    if cfg.time_decay_halflife_months is None or len(dataset) == 0:
        return None
    times = np.asarray([sample.open_time for sample in dataset.samples], dtype="float64")
    reference = float(times.max())
    age_months = (reference - times) / _MS_PER_MONTH
    return np.power(0.5, age_months / float(cfg.time_decay_halflife_months))


def write_train_log(rows: list[dict[str, Any]], path: Path) -> None:
    """写 train_log.csv（05 §6 第 7 条字段）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "seed",
        "epoch",
        "train_loss",
        "val_loss",
        "val_pr_auc",
        "val_roc_auc",
        "val_top5_precision",
        "elapsed_sec",
        "gpu_peak_mb",
        "is_best",
        "best_epoch",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in columns})


def _val_loss(val_ds: WindowDataset, logits: np.ndarray, m: np.ndarray, cfg: ModelConfig) -> float:
    """验证集损失（不参与早停选择，只写入日志）。"""
    y = np.asarray([s.y for s in val_ds.samples], dtype="float64")
    r = np.asarray([s.r_mult for s in val_ds.samples], dtype="float64")
    p = 1.0 / (1.0 + np.exp(-logits.astype("float64")))
    bce = float(
        -np.mean(y * np.log(np.clip(p, 1e-12, 1.0)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1.0)))
    )
    huber = np.where(np.abs(m - r) < 1.0, 0.5 * (m - r) ** 2, np.abs(m - r) - 0.5)
    return bce + cfg.lambda_reg * float(np.mean(huber))


def _train_one_seed(
    model: nn.Module,
    train_ds: WindowDataset,
    val_ds: WindowDataset,
    cfg: ModelConfig,
    *,
    seed: int,
    device: str,
    pos_weight: torch.Tensor,
    decay: np.ndarray | None,
) -> tuple[dict[str, torch.Tensor], np.ndarray, np.ndarray, list[dict[str, Any]]]:
    """训练单个种子：AdamW + 预热/余弦 + bf16 + 早停（05 §6 第 4 条）。"""
    set_deterministic(seed)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.learning_rate, weight_decay=cfg.weight_decay
    )
    warmup = max(1, cfg.warmup_epochs)
    total = max(1, cfg.max_epochs)

    def lr_lambda(epoch: int) -> float:
        if epoch < warmup:
            return float(epoch + 1) / float(warmup)
        progress = (epoch - warmup) / max(1, total - warmup)
        return 0.5 * (1.0 + math.cos(math.pi * min(progress, 1.0)))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    rng = np.random.default_rng(seed)
    y_val = np.asarray([s.y for s in val_ds.samples], dtype="float64")
    best_pr = -1.0
    best_state: dict[str, torch.Tensor] | None = None
    best_epoch = 0
    stale = 0
    rows: list[dict[str, Any]] = []
    use_bf16 = cfg.bf16 and device == "cuda"
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    for epoch in range(total):
        started = time.perf_counter()
        model.train()
        permutation = rng.permutation(len(train_ds))
        losses: list[float] = []
        for start in range(0, len(permutation), cfg.batch_size):
            indices = permutation[start : start + cfg.batch_size]
            x, y, r_mult, w = train_ds.collate([int(i) for i in indices])
            x = x.to(device).float()
            y = y.to(device)
            r_mult = r_mult.to(device)
            w = w.to(device)
            if decay is not None:
                w = w * torch.as_tensor(decay[indices], dtype=torch.float32, device=device)
            optimizer.zero_grad(set_to_none=True)
            with (
                torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                if use_bf16
                else contextlib.nullcontext()
            ):
                logit, m = model(x)
                loss = multitask_loss(logit, m, y, r_mult, w, cfg, pos_weight)
            if not torch.isfinite(loss):
                raise RuntimeError(
                    f"训练损失非有限值（seed={seed}，epoch={epoch + 1}，batch_start={start}）；"
                    "检查特征与标签是否含 NaN/Inf（05 §6）"
                )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_norm)
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        scheduler.step()

        val_logits, val_m = predict_dataset(model, val_ds, batch_size=cfg.batch_size, device=device)
        val_prob = 1.0 / (1.0 + np.exp(-val_logits.astype("float64")))
        val_pr = average_precision(y_val, val_prob)
        is_best = val_pr > best_pr + 1e-9
        if is_best:
            best_pr = val_pr
            best_epoch = epoch + 1
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
        rows.append(
            {
                "seed": seed,
                "epoch": epoch + 1,
                "train_loss": float(np.mean(losses)) if losses else None,
                "val_loss": _val_loss(val_ds, val_logits, val_m, cfg),
                "val_pr_auc": val_pr,
                "val_roc_auc": roc_auc(y_val, val_prob),
                "val_top5_precision": top_k_precision(y_val, val_prob),
                "elapsed_sec": time.perf_counter() - started,
                "gpu_peak_mb": peak_memory_mb(),
                "is_best": int(is_best),
                "best_epoch": best_epoch,
            }
        )
        if stale >= cfg.early_stopping_patience:
            break

    if best_state is None:
        best_state = copy.deepcopy(model.state_dict())
    model.load_state_dict(best_state)
    logits, m = predict_dataset(model, val_ds, batch_size=cfg.batch_size, device=device)
    for row in rows:
        row["best_epoch"] = best_epoch
    return best_state, logits, m, rows


def train_ensemble(
    train_ds: WindowDataset,
    val_ds: WindowDataset,
    cfg: ModelConfig,
    *,
    arch: str | None = None,
    out_dir: Path | None = None,
) -> TrainingResult:
    """训练全部种子的 M1/M2 集合并写出 train_log.csv / model.pt（05 §6）。

    Args:
        train_ds: 训练段数据集（只含训练组币与其入选月份）。
        val_ds: 验证段数据集（早停与校准用）。
        cfg: 训练超参（来自 configs/train.yaml）。
        arch: 覆盖 `cfg.arch`（第 2 步搜索时可传 tcn）。
        out_dir: 产物目录；None 时不落盘（冒烟用）。

    Returns:
        TrainingResult，含各种子 state_dict、日志与验证集 logits。

    Raises:
        ValueError: 训练集或验证集为空。
    """
    arch_name = arch or cfg.arch
    if len(train_ds) == 0 or len(val_ds) == 0:
        raise ValueError("训练集或验证集为空，无法训练（05 §6）")
    # 标签 r_mult 含 NaN/Inf 时（历史/缺失逐笔数据导致出场价无法确定）训练会发散成
    # NaN 权重，白白消耗数小时 GPU；在开始训练前就报错（04 §7.3、06 §3.3）。
    for name, dataset in (("训练集", train_ds), ("验证集", val_ds)):
        r_mult = np.asarray([sample.r_mult for sample in dataset.samples], dtype="float64")
        if not np.isfinite(r_mult).all():
            raise ValueError(
                f"{name} r_mult 含 {int((~np.isfinite(r_mult)).sum())} 个 NaN/Inf，"
                "标签构建异常（04 §7.3）；请重建数据集"
            )
    device = "cuda" if cfg.data_placement == "gpu" and torch.cuda.is_available() else "cpu"
    in_features = len(train_ds.feature_names)
    result = TrainingResult(
        arch=arch_name,
        arch_params={
            "hidden": cfg.hidden,
            "blocks": cfg.blocks,
            "kernel": cfg.kernel,
            "dropout": cfg.dropout,
            "window_L": cfg.window_L,
        },
    )
    result.val_y = _labels(val_ds)
    pos_weight = _pos_weight(_labels(train_ds))
    decay = _decay_weights(train_ds, cfg)
    for seed in cfg.seeds:
        model = build_model(arch_name, in_features, cfg).to(device)
        state_dict, logits, m, rows = _train_one_seed(
            model,
            train_ds,
            val_ds,
            cfg,
            seed=seed,
            device=device,
            pos_weight=pos_weight,
            decay=decay,
        )
        result.state_dicts.append(state_dict)
        result.best_epochs.append(int(rows[-1]["best_epoch"]))
        result.log_rows.extend(rows)
        result.val_logits.append(logits)
        result.val_m.append(m)

    ensemble_logit = np.mean(np.stack(result.val_logits), axis=0)
    result.val_pr_auc = average_precision(result.val_y, 1.0 / (1.0 + np.exp(-ensemble_logit)))
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "arch": arch_name,
                "arch_params": result.arch_params,
                "state_dicts": result.state_dicts,
            },
            out_dir / "model.pt",
        )
        write_train_log(result.log_rows, out_dir / "train_log.csv")
    return result


def _lgbm_matrices(dataset: WindowDataset) -> np.ndarray:
    """把 WindowDataset 转成 M0 的表格化特征矩阵 (N, 4F)（05 §2）。"""
    n_features = len(dataset.feature_names)
    matrix = np.zeros((len(dataset), 4 * n_features), dtype="float32")
    per_symbol: dict[int, list[int]] = {}
    for position, sample in enumerate(dataset.samples):
        per_symbol.setdefault(sample.sym_id, []).append(position)
    for sym_id, positions in per_symbol.items():
        x = dataset.xs[sym_id].detach().cpu().numpy()
        rows = np.asarray([dataset.samples[i].row_idx for i in positions], dtype="int64")
        matrix[np.asarray(positions, dtype="int64")] = tabular_features_matrix(x, rows)
    return matrix


def train_lgbm_baseline(
    train_ds: WindowDataset,
    val_ds: WindowDataset,
    cfg: ModelConfig,
    *,
    seed: int = 42,
) -> LGBMResult:
    """训练 M0 LightGBM 基线（05 §6 第 3 条，CPU）。"""
    if len(train_ds) == 0 or len(val_ds) == 0:
        raise ValueError("训练集或验证集为空，无法训练 M0")
    x_train = _lgbm_matrices(train_ds)
    x_val = _lgbm_matrices(val_ds)
    y_train = np.asarray([sample.y for sample in train_ds.samples], dtype="int64")
    y_val = np.asarray([sample.y for sample in val_ds.samples], dtype="int64")
    w_train = np.asarray([sample.w for sample in train_ds.samples], dtype="float64")
    w_val = np.asarray([sample.w for sample in val_ds.samples], dtype="float64")
    decay = _decay_weights(train_ds, cfg)
    if decay is not None:
        w_train = w_train * decay
    booster = train_lgbm(
        x_train,
        y_train,
        w_train,
        x_val,
        y_val,
        w_val,
        num_boost_round=cfg.lgbm_num_boost_round,
        early_stopping_rounds=cfg.lgbm_early_stopping_rounds,
        seed=seed,
    )
    prob = booster.predict(x_val, num_iteration=booster.best_iteration)
    return LGBMResult(
        booster=booster,
        val_prob=np.asarray(prob, dtype="float64"),
        val_y=y_val.astype("float64"),
        val_pr_auc=average_precision(y_val.astype("float64"), np.asarray(prob)),
        best_iteration=int(booster.best_iteration or 0),
    )
