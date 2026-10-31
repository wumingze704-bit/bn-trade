"""数据集构建：特征 + 预过滤 + 三重障碍标签 + manifest。

规格：docs/04-特征与标签.md §8；docs/03-数据与选币.md §6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

from trade.backtest.engine import BacktestConfig
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    segment_ids,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import SnapshotCoin, UniverseConfig, load_universe_config, rule_hash
from trade.features import FeaturesConfig, compute_features, enabled_features, feature_hash
from trade.labels import exit_sim, is_sample_mask, triple_barrier, uniqueness_weights


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset(
    run_id: str,
    base_cfg: BaseConfig,
    coins: Sequence[SnapshotCoin],
    features_cfg: FeaturesConfig,
    data_root: Path,
    *,
    btc_context: str = "BTCUSDT",
    include_holdout: bool = False,
    universe_cfg: UniverseConfig | None = None,
    exits_cfg: ExitConfig | None = None,
    tick_size_by_alias: Mapping[str, float] | None = None,
    half_spread_min: float = 0.0001,
    slip_min: float = 0.0001,
    Y: float = 1.0,
    notional_ref: float = 1000.0,
    max_chase: float = 0.003,
) -> dict:
    """构建数据集并写 manifest.json，返回 manifest 内容。

    Args:
        universe_cfg: 选币配置（None 则不按月范围过滤样本、不记录 rule_hash）。
        exits_cfg: 出场配置（label.mode=exit_sim 时必填，与回测同一份 exits.yaml）。
        tick_size_by_alias: alias -> 价格步长（exit_sim 点差与止损取整用，缺省 0.01）。
        half_spread_min/slip_min/Y: 成本模型参数（06 §4，base 情景）。
        notional_ref: 参考名义价值（滑点模型用，与回测同口径）。
        max_chase: 追价保护上限（06 §3.1 条件 5）。
    """
    features = enabled_features(features_cfg)
    fhash = feature_hash(features)
    wb = window_bounds(base_cfg)
    start_ms, end_ms, holdout_start_ms = wb.start_ms, wb.end_ms, wb.holdout_start_ms
    build_end_ms = end_ms if include_holdout else holdout_start_ms

    months = universe_months(base_cfg)
    membership = load_universe_membership(data_root, months) if universe_cfg is not None else None
    delay = universe_cfg.rule.new_entry_delay_days if universe_cfg is not None else 0

    features_dir = data_root / "features" / run_id
    labels_dir = data_root / "labels" / run_id
    features_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    # BTC 上下文（03 §1；04 §4.3）：只用于特征，不产出样本
    btc_bars = load_bars(
        data_root,
        btc_context,
        start_ms,
        build_end_ms,
        holdout_start_ms=holdout_start_ms,
        allow_holdout=include_holdout,
    )

    per_coin: list[dict] = []
    warnings: list[str] = []
    for coin in coins:
        role = coin.role
        alias = coin.alias
        bars = load_bars(
            data_root,
            alias,
            start_ms,
            build_end_ms,
            holdout_start_ms=holdout_start_ms,
            allow_holdout=include_holdout,
        )
        segments = segment_ids(bars["open_time"].to_numpy(dtype="int64"))
        features_df = compute_features(bars, btc_bars, features, features_cfg, segments)
        sample = is_sample_mask(
            bars,
            features_cfg.prefilter.vol_z_60,
            features_cfg.prefilter.r1_sigma,
            features_cfg.prefilter.brk_60,
            features_cfg.warmup_bars,
            segments,
        )
        if membership is not None:
            trade_mask = universe_trade_mask(
                bars["open_time"].to_numpy(dtype="int64"), coin.symbol, membership, delay
            )
            sample &= trade_mask
        features_df["is_sample"] = sample.astype("int64")
        # 特征以 fp16 落盘（design §4、05 §5；open_time/is_sample 保持 int64）
        feature_cols = [feature.name for feature in features]
        features_df[feature_cols] = features_df[feature_cols].astype("float16")
        features_path = features_dir / f"{alias}.parquet"
        features_df.to_parquet(features_path, index=False, compression="zstd")

        sample_positions = np.nonzero(sample)[0]
        if features_cfg.label.mode == "exit_sim":
            if exits_cfg is None:
                raise ValueError(
                    "label.mode=exit_sim 时必须提供 exits_cfg（与回测同一份 exits.yaml）"
                )
            tick_size = (tick_size_by_alias or {}).get(alias, 0.01)
            labels_df, kept_positions = exit_sim(
                bars,
                sample_positions,
                k_sl=features_cfg.label.k_sl,
                sl_min=features_cfg.label.sl_min,
                sl_max=features_cfg.label.sl_max,
                H_label=features_cfg.label.H_label,
                entry_latency_ms=features_cfg.label.entry_latency_ms,
                y_min_r=features_cfg.label.y_min_r,
                exit_cfg=exits_cfg,
                fees_taker=base_cfg.fees.taker,
                fees_maker=base_cfg.fees.maker,
                half_spread_min=half_spread_min,
                slip_min=slip_min,
                Y=Y,
                notional_ref=notional_ref,
                max_chase=max_chase,
                tick_size=tick_size,
                segments=segments,
            )
        else:
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
        labels_df["symbol"] = coin.symbol
        labels_df["weight"] = uniqueness_weights(
            kept_positions, labels_df["bars_held"].to_numpy(dtype="int64")
        )
        labels_path = labels_dir / f"{alias}.parquet"
        labels_df.to_parquet(labels_path, index=False, compression="zstd")

        n = len(bars)
        is_filled = bars["is_filled"].to_numpy(dtype="int64") == 1
        warmed = np.zeros(n, dtype=bool)
        for seg in np.unique(segments):
            idx = np.nonzero(segments == seg)[0]
            warmed[idx] = np.arange(idx.shape[0]) >= features_cfg.warmup_bars
        eligible_mask = (~is_filled) & warmed
        if membership is not None:
            eligible_mask &= trade_mask
        eligible = int(eligible_mask.sum())
        n_samples = len(labels_df)
        retention = n_samples / eligible if eligible else 0.0
        positive_rate = float(labels_df["y"].mean()) if n_samples else 0.0
        if (
            not features_cfg.prefilter.retention_min
            <= retention
            <= features_cfg.prefilter.retention_max
        ):
            warnings.append(f"{alias} 样本保留率 {retention:.4f} 超出预期区间")
        if not features_cfg.label.positive_min <= positive_rate <= features_cfg.label.positive_max:
            warnings.append(f"{alias} 正样本率 {positive_rate:.4f} 超出预期区间")
        per_coin.append(
            {
                "symbol": coin.symbol,
                "alias": alias,
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
        "universe_rule_hash": rule_hash(universe_cfg) if universe_cfg is not None else "",
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
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--exits", default="configs/exits.yaml")
    parser.add_argument("--sizing", default="configs/sizing.yaml")
    parser.add_argument("--backtest", default="configs/backtest.yaml")
    parser.add_argument("--final", action="store_true", help="包含封存集 M23~M24（仅 G3）")
    args = parser.parse_args(argv)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    base_cfg = load_config(BaseConfig, resolve(args.base))
    universe_cfg = load_universe_config(resolve(args.universe))
    features_cfg = load_config(FeaturesConfig, resolve(args.features))
    exits_cfg = load_config(ExitConfig, resolve(args.exits))
    sizing_cfg = load_config(SizingConfig, resolve(args.sizing))
    backtest_cfg = load_config(BacktestConfig, resolve(args.backtest))

    notional_ref = sizing_cfg.initial_equity * sizing_cfg.position_frac * float(sizing_cfg.leverage)
    coins = collect_universe_coins(base_cfg.paths.data_root, universe_months(base_cfg))
    manifest = build_dataset(
        args.run_id,
        base_cfg,
        coins,
        features_cfg,
        base_cfg.paths.data_root,
        btc_context=universe_cfg.btc_context,
        include_holdout=args.final,
        universe_cfg=universe_cfg,
        exits_cfg=exits_cfg,
        half_spread_min=backtest_cfg.costs.half_spread_min,
        slip_min=backtest_cfg.costs.slip_min,
        Y=backtest_cfg.costs.Y,
        notional_ref=notional_ref,
        max_chase=backtest_cfg.risk.max_chase,
    )
    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            print(f"警告：{warning}", file=sys.stderr)
    print(f"构建完成：{manifest['run_id']}，特征哈希 {manifest['feature_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
