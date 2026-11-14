"""数据集构建：特征 + 预过滤 + 三重障碍标签 + manifest。

规格：docs/04-特征与标签.md §8；docs/03-数据与选币.md §6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.engine import BacktestConfig
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig, exit_policy_hash
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    month_index_array,
    segment_ids,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import SnapshotCoin, UniverseConfig, load_universe_config, rule_hash
from trade.features import FeaturesConfig, compute_features, enabled_features, feature_hash
from trade.labels import exit_sim, is_sample_mask, triple_barrier, uniqueness_weights

# tick_size / 当月收盘价中位数 的告警阈值（03 §4.6、04 §8）
_TICK_REL_WARN = 0.002


def _tick_relative_warnings(
    alias: str, tick_size: float, month_idx: np.ndarray, close: np.ndarray
) -> list[str]:
    """tick_size 相对当月收盘价中位数过大时生成告警文本（03 §4.6）。

    Args:
        alias: 币别名。
        tick_size: 价格步长。
        month_idx: 每分钟的月序号（`month_index_array`，0 表示窗口外）。
        close: 对应分钟的收盘价。

    Returns:
        每个超阈值月份一条告警；没有超阈值月份时为空列表。
    """
    out: list[str] = []
    for month in np.unique(month_idx):
        if month <= 0:
            continue
        prices = close[month_idx == month]
        if prices.size == 0:
            continue
        median = float(np.median(prices))
        if median <= 0:
            continue
        ratio = tick_size / median
        if ratio > _TICK_REL_WARN:
            out.append(f"{alias} M{int(month)} tick/收盘价中位数 = {ratio:.4f} 超过 0.002")
    return out


def _nan_entry_price_minutes(bars: pd.DataFrame) -> int:
    """统计 `p_1s` 为空但 `is_filled = 0` 的分钟数（数据质量告警，04 §8）。

    这类分钟没有逐笔价却未被标为补齐分钟：入场会被 `no_entry_price` 放弃，但数据管道
    没有记录缺口，因此写入 manifest 告警，避免静默（AGENTS.md §6 红线 13）。

    Args:
        bars: 分钟数据（需含 `p_1s` 与 `is_filled`）。

    Returns:
        满足条件的分钟数。
    """
    if "p_1s" not in bars or "is_filled" not in bars:
        return 0
    missing_price = bars["p_1s"].isna().to_numpy()
    not_filled = bars["is_filled"].to_numpy() == 0
    return int((missing_price & not_filled).sum())


def _segment_tail_dropped(sample_positions: np.ndarray, segments: np.ndarray, h_max: int) -> int:
    """统计因“段尾不足 H_max / 跨数据段”被 `exit_sim` 丢弃的样本数（04 §8）。

    Args:
        sample_positions: 预过滤样本的 bars 行位置。
        segments: 每根 K 线的数据段编号（03 §6）。
        h_max: 该标签模式的最长持仓根数。

    Returns:
        被丢弃的样本数。
    """
    n = int(segments.shape[0])
    positions = np.asarray(sample_positions, dtype="int64")
    if positions.size == 0:
        return 0
    inside = positions + h_max < n
    same_segment = np.zeros(positions.shape[0], dtype=bool)
    same_segment[inside] = segments[positions[inside] + h_max] == segments[positions[inside]]
    return int((~same_segment).sum())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_tick_size_by_alias(rules_path: Path, aliases_path: Path) -> dict[str, float]:
    """从交易规则与别名快照构造 alias -> 价格步长映射。

    规格：docs/04-特征与标签.md §7.3（exit_sim 标签的止损/点差取整用真实 tick_size，
    与回测同口径）。只取 aliases.json 中历史上入选过的币，不覆盖全市场。

    Args:
        rules_path: artifacts/exchange_info/rules.json（symbol -> {tick_size, ...}）。
        aliases_path: data/universe/aliases.json（symbol -> alias）。

    Returns:
        alias -> 价格步长；缺失 symbol 或 tick_size 的币不写入，由 `build_dataset`
        直接报错（红线 13，不回退默认值）。
    """
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    tick_size_by_alias: dict[str, float] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = rules.get(symbol)
        if rule is not None and "tick_size" in rule:
            tick_size_by_alias[alias] = float(rule["tick_size"])
    return tick_size_by_alias


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
    rules_path: Path | None = None,
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
        tick_size_by_alias: alias -> 价格步长（exit_sim 点差与止损取整用）。缺失币直接
            报错，不回退默认值（03 §4.6、红线 13）。
        rules_path: 交易规则快照路径，仅用于报错信息。
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
        nan_entry_minutes = _nan_entry_price_minutes(bars)
        if nan_entry_minutes:
            warnings.append(
                f"{alias} 有 {nan_entry_minutes} 个分钟 p_1s 为空但 is_filled=0"
                "（入场按 no_entry_price 放弃）"
            )
        features_df["is_sample"] = sample.astype("int64")
        # 特征以 fp16 落盘（design §4、05 §5；open_time/is_sample 保持 int64）
        feature_cols = [feature.name for feature in features]
        features_df[feature_cols] = features_df[feature_cols].astype("float16")
        features_path = features_dir / f"{alias}.parquet"
        features_df.to_parquet(features_path, index=False, compression="zstd")

        sample_positions = np.nonzero(sample)[0]
        h_max_label = (
            exits_cfg.H_max
            if (exits_cfg is not None and features_cfg.label.mode == "exit_sim")
            else features_cfg.label.H_label
        )
        segment_tail_dropped = _segment_tail_dropped(sample_positions, segments, int(h_max_label))
        if features_cfg.label.mode == "exit_sim":
            if exits_cfg is None:
                raise ValueError(
                    "label.mode=exit_sim 时必须提供 exits_cfg（与回测同一份 exits.yaml）"
                )
            if tick_size_by_alias is None:
                raise ValueError(
                    f"未提供交易规则（tick_size_by_alias），无法构建 exit_sim 标签：{alias}"
                )
            if alias not in tick_size_by_alias:
                raise ValueError(
                    f"交易规则缺少 {alias}（{coin.symbol}）的 tick_size："
                    f"{rules_path if rules_path is not None else 'rules.json'}"
                    "（03 §4.6；缺失时不得回退默认值）"
                )
            tick_size = float(tick_size_by_alias[alias])
            warnings.extend(
                _tick_relative_warnings(
                    alias,
                    tick_size,
                    month_index_array(
                        bars["open_time"].to_numpy(dtype="int64"),
                        base_cfg.data_window.end_month,
                        base_cfg.data_window.months,
                    ),
                    bars["close"].to_numpy(dtype="float64"),
                )
            )
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
                "segment_tail_dropped": segment_tail_dropped,
                "nan_entry_price_minutes": nan_entry_minutes,
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
        "exit_policy_hash": exit_policy_hash(exits_cfg) if exits_cfg is not None else "",
        "notional_ref": notional_ref,
        "rules_sha256": _sha256_file(rules_path) if rules_path is not None else "",
        "created_at": datetime.now(UTC).isoformat(),
        "label_dropped": {
            "segment_tail": {
                "total": int(sum(item["segment_tail_dropped"] for item in per_coin)),
                "by_alias": {item["alias"]: item["segment_tail_dropped"] for item in per_coin},
            }
        },
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
    months = universe_months(base_cfg)
    # 只用 M1~M{months-holdout_months} 收集币：封存集（M23~M24）才首次入选的新币
    # 本地无 bars，且红线 2 禁止加载封存集数据（03 §6、08 G1）。
    # 用 len(months)-holdout_months 而非 -holdout_months，避免 holdout_months=0 时
    # Python 切片 [:-0] 退化为 [:0]（空列表）。
    coins = collect_universe_coins(
        base_cfg.paths.data_root, months[: len(months) - base_cfg.data_window.holdout_months]
    )
    tick_size_by_alias = _load_tick_size_by_alias(
        base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
        base_cfg.paths.data_root / "universe" / "aliases.json",
    )
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
        tick_size_by_alias=tick_size_by_alias,
        rules_path=base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
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
