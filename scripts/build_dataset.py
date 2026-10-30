"""数据集构建：特征 + 预过滤 + 三重障碍标签 + manifest。

规格：docs/04-特征与标签.md §8；docs/03-数据与选币.md §6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from trade.common.config import BaseConfig, load_config
from trade.data.loader import load_bars, window_bounds
from trade.data.universe import CoinsConfig, load_coins
from trade.features import FeaturesConfig, compute_features, enabled_features, feature_hash
from trade.labels import is_sample_mask, triple_barrier, uniqueness_weights


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset(
    run_id: str,
    base_cfg: BaseConfig,
    coins: CoinsConfig,
    features_cfg: FeaturesConfig,
    data_root: Path,
    *,
    include_holdout: bool = False,
) -> dict:
    """构建数据集并写 manifest.json，返回 manifest 内容。"""
    features = enabled_features(features_cfg)
    fhash = feature_hash(features)
    wb = window_bounds(base_cfg)
    start_ms, end_ms, holdout_start_ms = wb.start_ms, wb.end_ms, wb.holdout_start_ms
    build_end_ms = end_ms if include_holdout else holdout_start_ms

    features_dir = data_root / "features" / run_id
    labels_dir = data_root / "labels" / run_id
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    # BTC 上下文（03 §1；04 §4.3）：只用于特征，不产出样本
    btc_bars = load_bars(
        data_root,
        coins.btc_context,
        start_ms,
        build_end_ms,
        holdout_start_ms=holdout_start_ms,
        allow_holdout=include_holdout,
    )

    per_coin: list[dict] = []
    warnings: list[str] = []
    for role in ("train", "holdout", "blind"):
        for entry in getattr(coins, role):
            bars = load_bars(
                data_root,
                entry.alias,
                start_ms,
                build_end_ms,
                holdout_start_ms=holdout_start_ms,
                allow_holdout=include_holdout,
            )
            features_df = compute_features(bars, btc_bars, features, features_cfg)
            sample = is_sample_mask(
                bars,
                features_cfg.prefilter.vol_z_60,
                features_cfg.prefilter.r1_sigma,
                features_cfg.prefilter.brk_60,
                features_cfg.warmup_bars,
            )
            features_df["is_sample"] = sample.astype("int64")
            features_path = features_dir / f"{entry.alias}.parquet"
            features_df.to_parquet(features_path, index=False, compression="zstd")

            sample_positions = np.nonzero(sample)[0]
            labels_df, kept_positions = triple_barrier(
                bars,
                sample_positions,
                k_sl=features_cfg.label.k_sl,
                rr=features_cfg.label.rr,
                sl_min=features_cfg.label.sl_min,
                sl_max=features_cfg.label.sl_max,
                H_label=features_cfg.label.H_label,
                entry_latency_ms=features_cfg.label.entry_latency_ms,
                c_rt=features_cfg.label.c_rt,
                r_mult_clip=features_cfg.label.r_mult_clip,
                fill_ratio_max=features_cfg.label.fill_ratio_max,
            )
            labels_df["symbol"] = entry.symbol
            labels_df["weight"] = uniqueness_weights(
                kept_positions, labels_df["bars_held"].to_numpy(dtype="int64")
            )
            labels_path = labels_dir / f"{entry.alias}.parquet"
            labels_df.to_parquet(labels_path, index=False, compression="zstd")

            n = len(bars)
            is_filled = bars["is_filled"].to_numpy(dtype="int64") == 1
            warmed = np.arange(n) >= features_cfg.warmup_bars
            eligible = int(((~is_filled) & warmed).sum())
            n_samples = len(labels_df)
            retention = n_samples / eligible if eligible else 0.0
            positive_rate = float(labels_df["y"].mean()) if n_samples else 0.0
            if (
                not features_cfg.prefilter.retention_min
                <= retention
                <= features_cfg.prefilter.retention_max
            ):
                warnings.append(f"{entry.alias} 样本保留率 {retention:.4f} 超出预期区间")
            if (
                not features_cfg.label.positive_min
                <= positive_rate
                <= features_cfg.label.positive_max
            ):
                warnings.append(f"{entry.alias} 正样本率 {positive_rate:.4f} 超出预期区间")
            per_coin.append(
                {
                    "symbol": entry.symbol,
                    "alias": entry.alias,
                    "role": role,
                    "rows": n,
                    "eligible": eligible,
                    "samples": n_samples,
                    "retention": retention,
                    "positive_rate": positive_rate,
                    "features_sha256": _sha256_file(features_path),
                    "labels_sha256": _sha256_file(labels_path),
                }
            )

    manifest = {
        "run_id": run_id,
        "include_holdout": include_holdout,
        "window": {
            "end_month": base_cfg.data_window.end_month,
            "months": base_cfg.data_window.months,
            "warmup_days": base_cfg.data_window.warmup_days,
            "tuning_months": base_cfg.data_window.tuning_months,
            "holdout_months": base_cfg.data_window.holdout_months,
            "start_ms": start_ms,
            "end_ms": build_end_ms,
        },
        "feature_hash": fhash,
        "features": [feature.name for feature in features],
        "ewm_halflife_bars": features_cfg.ewm.halflife_bars,
        "prefilter": features_cfg.prefilter.model_dump(),
        "label": features_cfg.label.model_dump(),
        "coins": per_coin,
        "warnings": warnings,
    }
    manifest_path = features_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False), encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    """build_dataset.py 入口。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="构建特征与标签数据集（04 §8）")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--coins", default="configs/coins.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--final", action="store_true", help="包含封存集 M23~M24（仅 G3）")
    args = parser.parse_args(argv)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    base_cfg = load_config(BaseConfig, resolve(args.base))
    coins = load_coins(resolve(args.coins))
    features_cfg = load_config(FeaturesConfig, resolve(args.features))

    manifest = build_dataset(
        args.run_id,
        base_cfg,
        coins,
        features_cfg,
        base_cfg.paths.data_root,
        include_holdout=args.final,
    )
    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            print(f"警告：{warning}", file=sys.stderr)
    print(f"构建完成：{manifest['run_id']}，特征哈希 {manifest['feature_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
