"""T4-3 第 1 步：出场与止损参数搜索（用 M0 LightGBM 评估）。

规格：docs/05-模型与训练.md §6.1（先定出场与止损，再调模型）。只在调参期
（训练 M1~M9、验证 M10~M12）上搜索，6 组出场/止损参数各重建 `exit_sim` 标签、
训练 M0、在验证段选阈值、算 `base` 情景净收益与 PF，取 PF 最高且净收益 > 0 的一组。
每组（含失败的组）结果写入 `artifacts/reports/EXPERIMENTS.log`（05 §6.1、06 §8）。

两种只报告、不参与选择的模式：
- `--checkup`：用冻结的组 N 参数与阈值复现其 M0，在验证段 M10~M12 上跑
  `base`/`stress`/`hell` 三情景与盈亏平衡滑点（06 §4.3），供体检用；
- `--backfill-log`：把第 1 步历史轮次（3 轮 × 6 组，数字取自各轮真实运行日志）
  补登进 EXPERIMENTS.log，计入 05 §6.1 的 30 组预算。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import lightgbm as lgb  # type: ignore[import-untyped]
import numpy as np
import pandas as pd

from trade.backtest.costs import FeeSchedule, Scenario, scenario
from trade.backtest.engine import (
    BacktestConfig,
    PortfolioResult,
    PrecomputedTrade,
    PreparedSymbol,
    SkippedSignal,
    TradeRule,
    precompute_trades_all,
    prepare_symbols,
    simulate_portfolio,
)
from trade.backtest.metrics import compute_metrics
from trade.backtest.report import (
    append_experiments_log,
    compute_experiment_hash,
    experiments_log_path,
    read_experiments_log,
)
from trade.common.config import BaseConfig, load_config
from trade.common.costs import breakeven_slip
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    month_index_array,
    rebalance_ms_of,
    segment_ids,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import SnapshotCoin, load_universe_config
from trade.features import FeaturesConfig, compute_features, enabled_features
from trade.features.registry import feature_hash
from trade.labels.exit_sim import exit_sim
from trade.labels.prefilter import is_sample_mask
from trade.labels.triple_barrier import uniqueness_weights
from trade.models.lightgbm import tabular_features_matrix, train_lgbm

# 6 组出场/止损参数（05 §6.1 第 1 步）。H_max/k_trail 固定 120/1.5，sl_max 固定 2.5%，
# 在 k_sl 2.5/3.0 与 sl_min 1.0%~1.8% 上各取 3 组。
_EXIT_GRID: list[dict[str, float]] = [
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.010, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.012, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 2.5, "sl_min": 0.014, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.012, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.015, "sl_max": 0.025},
    {"H_max": 120.0, "k_trail": 1.5, "k_sl": 3.0, "sl_min": 0.018, "sl_max": 0.025},
]

# 阈值网格（05 §7.2：0.30~0.80，步长 0.02）
_THETA_GRID = [round(0.30 + 0.02 * i, 2) for i in range(26)]

# 验证段阈值约束：日均交易 ≥ 3 笔 × 90 天 ≈ 270 笔（05 §6.1 第 1 步）
_MIN_VAL_TRADES = 270

# 训练样本上限：超过则按固定种子下采样，控制内存（可复现；LightGBM 直方图对样本数不敏感）。
_MAX_SAMPLES = 2_000_000

# 一天毫秒数（验证段天数折算日均笔数）
_MS_PER_DAY = 86_400_000

# 盈亏平衡滑点二分搜索：上界 1%（100 bp）、10 次二分 → 分辨率约 0.1 bp（06 §4.3）
_BREAKEVEN_HI = 0.01
_BREAKEVEN_ITERS = 10


@dataclass(frozen=True)
class BackfillTrial:
    """第 1 步历史轮次的一组真实运行结果（数字来自当轮终端日志）。"""

    round: str
    group: int
    h_max: int
    k_trail: float
    k_sl: float
    sl_min: float
    sl_max: float
    theta: float
    pf: float
    net: float
    n_trades: float | None = None


# 第 1 步历史轮次的真实运行输出（仓库根目录 tuning_exits.log / tuning_exits2.log /
# tuning_exits3.log 的「结果汇总」）。当时脚本未打印每组的笔数，故 n_trades 记 None，
# 不估算、不编造。第 1、2 轮用旧的切分与 features.yaml 止损上下限（训练 M1~M10、
# 验证 M11~M12，sl_min=0.5%、sl_max=1.5%），第 3 轮为现行 M1~M9 / M10~M12、sl_max=2.5%。
_BACKFILL_TRIALS: list[BackfillTrial] = [
    # 第 1 轮：H_max/k_trail 扫描（k_sl 1.0/1.3，sl_min 0.5%，sl_max 1.5%）
    BackfillTrial("r1", 1, 120, 1.5, 1.0, 0.005, 0.015, 0.34, 0.754, -452.94),
    BackfillTrial("r1", 2, 60, 1.5, 1.0, 0.005, 0.015, 0.32, 0.615, -350.24),
    BackfillTrial("r1", 3, 240, 1.5, 1.0, 0.005, 0.015, 0.30, 0.683, -656.75),
    BackfillTrial("r1", 4, 120, 1.0, 1.0, 0.005, 0.015, 0.36, 0.710, -740.80),
    BackfillTrial("r1", 5, 120, 2.0, 1.0, 0.005, 0.015, 0.34, 0.773, -243.70),
    BackfillTrial("r1", 6, 120, 1.5, 1.3, 0.005, 0.015, 0.38, 0.976, -30.62),
    # 第 2 轮：k_sl / sl_min 网格（H_max 120，k_trail 1.5，sl_max 1.5%）
    BackfillTrial("r2", 1, 120, 1.5, 1.0, 0.005, 0.015, 0.34, 0.754, -452.94),
    BackfillTrial("r2", 2, 120, 1.5, 1.5, 0.005, 0.015, 0.38, 0.720, -365.69),
    BackfillTrial("r2", 3, 120, 1.5, 2.0, 0.005, 0.015, 0.32, 0.669, -1067.56),
    BackfillTrial("r2", 4, 120, 1.5, 1.5, 0.008, 0.015, 0.30, 0.649, -2090.62),
    BackfillTrial("r2", 5, 120, 1.5, 2.0, 0.010, 0.015, 0.30, 0.742, -1438.37),
    BackfillTrial("r2", 6, 120, 1.5, 2.5, 0.012, 0.015, 0.32, 1.688, 1410.57),
    # 第 3 轮：现行网格（训练 M1~M9、验证 M10~M12，sl_max 2.5%）
    BackfillTrial("r3", 1, 120, 1.5, 2.5, 0.010, 0.025, 0.34, 1.473, 1322.06),
    BackfillTrial("r3", 2, 120, 1.5, 2.5, 0.012, 0.025, 0.34, 1.033, 103.65),
    BackfillTrial("r3", 3, 120, 1.5, 2.5, 0.014, 0.025, 0.30, 1.026, 261.92),
    BackfillTrial("r3", 4, 120, 1.5, 3.0, 0.012, 0.025, 0.30, 1.001, 15.79),
    BackfillTrial("r3", 5, 120, 1.5, 3.0, 0.015, 0.025, 0.34, 1.039, 117.32),
    BackfillTrial("r3", 6, 120, 1.5, 3.0, 0.018, 0.025, 0.30, 1.087, 946.46),
]


def _resolve(root: Path, value: str) -> Path:
    """把配置里的相对路径按仓库根目录解析为绝对路径。"""
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_rules_by_alias(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读 rules.json + aliases.json，构造 alias -> TradeRule（与回测 key 一致）。"""
    rules_raw = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    result: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = rules_raw.get(symbol)
        if rule is not None:
            result[alias] = TradeRule(
                tick_size=float(rule["tick_size"]),
                step_size=float(rule["step_size"]),
                min_notional=float(rule.get("min_notional", 5.0)),
            )
    return result


@dataclass
class CoinData:
    """单币的行情、特征与样本位置（与出场参数无关，一次运行只算一次）。"""

    symbol: str
    bars: pd.DataFrame
    segments: np.ndarray
    x: np.ndarray
    open_time: np.ndarray
    sample_positions: np.ndarray


@dataclass
class TuningData:
    """一次运行的配置、行情与逐币数据（05 §6.1）。"""

    base_cfg: BaseConfig
    features_cfg: FeaturesConfig
    exits_cfg: ExitConfig
    sizing_cfg: SizingConfig
    backtest_cfg: BacktestConfig
    months: list[str]
    coins: list[SnapshotCoin]
    per_coin: dict[str, CoinData]
    bars_mtm: dict[str, pd.DataFrame]
    prepared: dict[str, PreparedSymbol]
    rules: dict[str, TradeRule]
    groups: dict[str, str]
    fees: FeeSchedule
    feature_names: list[str]
    val_start_ms: int
    val_end_ms: int
    log_path: Path
    config_inputs: dict[str, Path]


@dataclass
class SampleSet:
    """调参期拼接后的样本数组（已按币分组、按固定种子下采样）。"""

    x: np.ndarray
    y: np.ndarray
    w: np.ndarray
    m: np.ndarray
    open_time: np.ndarray
    symbol: np.ndarray
    t_exit: np.ndarray


def _load_disk_features(
    features_dir: Path,
    features_cfg: FeaturesConfig,
    feature_names: list[str],
    aliases: list[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """读取已构建数据集的特征 parquet，并校验 manifest（05 §6.1）。

    Args:
        features_dir: 数据集特征目录 `data/features/{run_id}`。
        features_cfg: 当前特征配置。
        feature_names: 当前启用特征名（有序）。
        aliases: 本次需要的币别名（只读取这些文件）。
        start_ms: 需要的起点（毫秒，含）。
        end_ms: 需要的终点（毫秒，不含）。

    Returns:
        alias -> (`open_time` int64 数组, 特征 float16 矩阵)，`open_time` 严格递增。

    Raises:
        ValueError: manifest 缺失、`feature_hash`/特征列表与当前注册表不一致，
            或数据集覆盖区间不包含所需区间（红线 13，不回退到现场计算）。
    """
    manifest_path = features_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"数据集缺少 manifest.json：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_hash = feature_hash(enabled_features(features_cfg))
    if manifest.get("feature_hash") != expected_hash:
        raise ValueError(
            f"数据集 feature_hash 与当前注册表不一致：{manifest_path}；"
            f"数据集 {manifest.get('feature_hash')}，当前 {expected_hash}（04 §3、05 §6.1）"
        )
    if list(manifest.get("features") or []) != feature_names:
        raise ValueError(f"数据集特征列表与当前注册表不一致：{manifest_path}")
    window = manifest.get("window") or {}
    window_start = int(window.get("start_ms", 0))
    window_end = int(window.get("end_ms", 0))
    if window_start > start_ms or window_end < end_ms:
        raise ValueError(
            f"数据集覆盖区间 [{window_start}, {window_end}) 不包含所需区间 "
            f"[{start_ms}, {end_ms})：{manifest_path}（05 §6.1）"
        )
    frames: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for alias in aliases:
        path = features_dir / f"{alias}.parquet"
        if not path.is_file():
            raise ValueError(f"数据集缺少币 {alias} 的特征文件：{path}（05 §6.1）")
        frame = pd.read_parquet(path, columns=["open_time", *feature_names])
        times = frame["open_time"].to_numpy(dtype="int64")
        if times.size > 1 and not bool(np.all(np.diff(times) > 0)):
            raise ValueError(f"数据集 open_time 非严格递增：{path}")
        frames[alias] = (times, frame[feature_names].to_numpy(dtype="float16"))
    if not frames:
        raise ValueError(f"数据集目录内没有特征 parquet：{features_dir}")
    return frames


def _align_disk_features(
    frames: dict[str, tuple[np.ndarray, np.ndarray]],
    alias: str,
    open_time: np.ndarray,
) -> np.ndarray:
    """把落盘特征按 bars 的 `open_time` 对齐，返回 fp16 特征矩阵（05 §6.1）。"""
    entry = frames.get(alias)
    if entry is None:
        raise ValueError(f"数据集缺少币 {alias} 的特征 parquet，无法复用落盘特征（05 §6.1）")
    dataset_time, values = entry
    pos = np.searchsorted(dataset_time, open_time)
    matched = pos < dataset_time.size
    if matched.any():
        matched[matched] = dataset_time[pos[matched]] == open_time[matched]
    if not bool(matched.all()):
        raise ValueError(
            f"数据集特征缺少 {alias} 的 {int((~matched).sum())} 个分钟（open_time 对不上），"
            f"无法复用落盘特征（05 §6.1）"
        )
    aligned = values[pos]
    if not np.isfinite(aligned.astype("float32")).all():
        raise ValueError(f"落盘特征在调参期存在非有限值：{alias}（05 §6.1）")
    return aligned


def _prepare_data(root: Path, args: argparse.Namespace) -> TuningData:
    """加载配置与调参期 M1~M12 行情，逐币算特征与预过滤样本（05 §6.1）。"""
    base_cfg = load_config(BaseConfig, _resolve(root, args.base))
    universe_cfg = load_universe_config(_resolve(root, args.universe))
    features_cfg = load_config(FeaturesConfig, _resolve(root, args.features))
    exits_cfg = load_config(ExitConfig, _resolve(root, args.exits))
    sizing_cfg = load_config(SizingConfig, _resolve(root, args.sizing))
    backtest_cfg = load_config(BacktestConfig, _resolve(root, args.backtest))

    data_root = _resolve(root, str(base_cfg.paths.data_root))
    months = universe_months(base_cfg)
    tuning_months = base_cfg.data_window.tuning_months
    tuning_months_list = months[:tuning_months]  # M1~M12
    tuning_end_ms = rebalance_ms_of(months[tuning_months])  # M13 起点，调参期右边界
    val_start_ms = rebalance_ms_of(months[tuning_months - 3])  # M10 起点，验证段左边界

    coins = collect_universe_coins(data_root, tuning_months_list)
    if args.limit is not None:
        coins = coins[: args.limit]

    wb = window_bounds(base_cfg)
    membership = load_universe_membership(data_root, months)
    delay = universe_cfg.rule.new_entry_delay_days
    features = enabled_features(features_cfg)
    feature_names = [f.name for f in features]
    disk_features: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
    if args.features_dir is not None:
        disk_features = _load_disk_features(
            _resolve(root, str(args.features_dir)),
            features_cfg,
            feature_names,
            [coin.alias for coin in coins],
            wb.start_ms,
            tuning_end_ms,
        )
    # 复用落盘特征时不需要 BTC 上下文（只用于 compute_features）。
    btc_bars = (
        None
        if disk_features is not None
        else load_bars(
            data_root,
            universe_cfg.btc_context,
            wb.start_ms,
            tuning_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
    )

    per_coin: dict[str, CoinData] = {}
    bars_mtm: dict[str, pd.DataFrame] = {}
    for coin in coins:
        alias = coin.alias
        bars = load_bars(
            data_root,
            alias,
            wb.start_ms,
            tuning_end_ms,
            holdout_start_ms=wb.holdout_start_ms,
            allow_holdout=False,
        )
        seg = segment_ids(bars["open_time"].to_numpy(dtype="int64"))
        times = bars["open_time"].to_numpy(dtype="int64")
        if disk_features is not None:
            x = _align_disk_features(disk_features, alias, times)
        else:
            if btc_bars is None:  # pragma: no cover - 防御性检查（红线 13）
                raise ValueError("缺少 BTC 上下文但需要现场计算特征（05 §6.1）")
            feat_df = compute_features(bars, btc_bars, features, features_cfg, seg)
            x = feat_df[feature_names].to_numpy(dtype="float16")
        sample = is_sample_mask(
            bars,
            features_cfg.prefilter.vol_z_60,
            features_cfg.prefilter.r1_sigma,
            features_cfg.prefilter.brk_60,
            features_cfg.warmup_bars,
            seg,
        )
        trade_mask = universe_trade_mask(times, coin.symbol, membership, delay)
        sample = sample & trade_mask
        per_coin[alias] = CoinData(
            symbol=coin.symbol,
            bars=bars,
            segments=seg,
            x=x,
            open_time=times,
            sample_positions=np.nonzero(sample)[0],
        )
        # 盯市权益曲线只需要验证段窗口的开盘时间与收盘价（06 §5.1）：预先切片，
        # 避免每次组合模拟都为全量 M1~M12 bars 重建 close 查找表。
        in_val = (times >= val_start_ms) & (times < tuning_end_ms)
        bars_mtm[alias] = pd.DataFrame(
            {"open_time": times[in_val], "close": bars["close"].to_numpy(dtype="float64")[in_val]}
        )

    artifact_root = _resolve(root, str(base_cfg.paths.artifact_root))
    rules_path = artifact_root / "exchange_info" / "rules.json"
    rules = _load_rules_by_alias(rules_path, data_root / "universe" / "aliases.json")
    missing_rules = sorted(alias for alias in per_coin if alias not in rules)
    if missing_rules:
        raise ValueError(
            f"交易规则缺少 {len(missing_rules)} 个币的条目（前 5 个：{missing_rules[:5]}）："
            f"{rules_path}（03 §4.6；不得静默排除缺规则的币）"
        )
    groups = {coin.alias: coin.role for coin in coins}
    fees = FeeSchedule(base_cfg.fees.maker, base_cfg.fees.taker)
    # 每币 numpy 数组只准备一次（06 §3.4），各轮出场参数与 θ 复用同一份数组。
    prepared = prepare_symbols({alias: info.bars for alias, info in per_coin.items()})
    return TuningData(
        base_cfg=base_cfg,
        features_cfg=features_cfg,
        exits_cfg=exits_cfg,
        sizing_cfg=sizing_cfg,
        backtest_cfg=backtest_cfg,
        months=months,
        coins=coins,
        per_coin=per_coin,
        bars_mtm=bars_mtm,
        prepared=prepared,
        rules=rules,
        groups=groups,
        fees=fees,
        feature_names=feature_names,
        val_start_ms=val_start_ms,
        val_end_ms=tuning_end_ms,
        log_path=experiments_log_path(artifact_root),
        config_inputs={
            "exchange_rules_snapshot": rules_path,
            "coin_aliases": data_root / "universe" / "aliases.json",
        },
    )


def _build_samples(
    data: TuningData,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
) -> SampleSet | None:
    """按一组出场参数重建 `exit_sim` 标签，并拼装 M0 的样本数组（04 §7.3）。"""
    notional_ref = (
        data.sizing_cfg.initial_equity * data.sizing_cfg.position_frac * data.sizing_cfg.leverage
    )
    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    w_parts: list[np.ndarray] = []
    m_parts: list[np.ndarray] = []
    ot_parts: list[np.ndarray] = []
    tx_parts: list[np.ndarray] = []
    sym_parts: list[np.ndarray] = []
    label_cfg = data.features_cfg.label
    for alias, info in data.per_coin.items():
        labels_df, kept = exit_sim(
            info.bars,
            info.sample_positions,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=label_cfg.H_label,
            entry_latency_ms=label_cfg.entry_latency_ms,
            y_min_r=label_cfg.y_min_r,
            exit_cfg=exit_cfg,
            fees_taker=data.base_cfg.fees.taker,
            fees_maker=data.base_cfg.fees.maker,
            half_spread_min=data.backtest_cfg.costs.half_spread_min,
            slip_min=data.backtest_cfg.costs.slip_min,
            Y=data.backtest_cfg.costs.Y,
            notional_ref=notional_ref,
            max_chase=data.backtest_cfg.risk.max_chase,
            tick_size=data.rules[alias].tick_size,
            segments=info.segments,
        )
        n = kept.size
        if n == 0:
            continue
        weights = uniqueness_weights(kept, labels_df["bars_held"].to_numpy(dtype="int64"))
        row_idx = kept.astype("int64")
        x_parts.append(tabular_features_matrix(info.x, row_idx))
        y_parts.append(labels_df["y"].to_numpy(dtype="int64"))
        w_parts.append(weights)
        ot = labels_df["open_time"].to_numpy(dtype="int64")
        ot_parts.append(ot)
        tx_parts.append(labels_df["t_exit"].to_numpy(dtype="int64"))
        m_parts.append(month_index_array(ot, data.base_cfg.data_window.end_month, len(data.months)))
        sym_parts.append(np.full(n, alias, dtype=object))
    if not x_parts:
        return None

    # 内存保护：样本过多时先按固定种子下采样，再拼接（避免峰值内存超限）。
    n_total = sum(len(p) for p in y_parts)
    rng = np.random.default_rng(data.base_cfg.seed)
    keep = (
        np.sort(rng.choice(n_total, _MAX_SAMPLES, replace=False))
        if n_total > _MAX_SAMPLES
        else None
    )
    xl, yl, wl, ml, ol, sl2, txl = [], [], [], [], [], [], []
    offset = 0
    for xp, yp, wp, mp, otp, sp, txp in zip(
        x_parts, y_parts, w_parts, m_parts, ot_parts, sym_parts, tx_parts, strict=True
    ):
        n = len(yp)
        local = (
            np.arange(n) if keep is None else keep[(keep >= offset) & (keep < offset + n)] - offset
        )
        xl.append(xp[local])
        yl.append(yp[local])
        wl.append(wp[local])
        ml.append(mp[local])
        ol.append(otp[local])
        sl2.append(sp[local])
        txl.append(txp[local])
        offset += n
    return SampleSet(
        x=np.concatenate(xl),
        y=np.concatenate(yl),
        w=np.concatenate(wl),
        m=np.concatenate(ml),
        open_time=np.concatenate(ol),
        symbol=np.concatenate(sl2),
        t_exit=np.concatenate(txl),
    )


def _train_and_predict(
    data: TuningData, samples: SampleSet
) -> tuple[lgb.Booster, pd.DataFrame, int, int]:
    """训练 M0（训练 M1~M9）并在验证段 M10~M12 输出信号（05 §6、§7.2）。

    Purge 按样本自身的 `t_exit`：`t_exit` 落入验证段起点（M10 起点）的 M1~M9
    样本从训练集删除（03 §6）。
    """
    train_mask = (samples.m >= 1) & (samples.m <= 9) & (samples.t_exit < data.val_start_ms)
    val_mask = (samples.m >= 10) & (samples.m <= 12)
    if train_mask.sum() == 0 or val_mask.sum() == 0:
        raise ValueError("训练/验证样本为空，无法训练 M0（05 §6.1）")
    booster = train_lgbm(
        samples.x[train_mask],
        samples.y[train_mask],
        samples.w[train_mask],
        samples.x[val_mask],
        samples.y[val_mask],
        samples.w[val_mask],
        seed=data.base_cfg.seed,
    )
    p_val = booster.predict(samples.x[val_mask], num_iteration=booster.best_iteration)
    n_val = int(val_mask.sum())
    val_signals = pd.DataFrame(
        {
            "symbol": samples.symbol[val_mask],
            "open_time": samples.open_time[val_mask],
            "p": p_val,
            "m": np.zeros(n_val),
            "is_sample": np.ones(n_val, dtype="int64"),
        }
    )
    return booster, val_signals, int(train_mask.sum()), n_val


_TUNING_PRECOMPUTE_CACHE: dict[
    tuple[object, ...], tuple[list[PrecomputedTrade], list[SkippedSignal]]
] = {}


def _precompute_all(
    data: TuningData,
    signals: pd.DataFrame,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    scen: Scenario,
    threshold: float,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """按情景与出场参数预计算一次（p ≥ 最低阈值），再按 θ 过滤（06 §3.4）。

    同一组出场参数下的 26 个 θ 只在第一次调用时做逐笔出场模拟，其余复用缓存。
    """
    floor = min(_THETA_GRID)
    key = (
        exit_cfg.model_dump_json(),
        k_sl,
        sl_min,
        sl_max,
        scen.name,
        scen.latency_ms,
        scen.slip_mult,
        scen.half_spread_mult,
        scen.fixed_slip,
        scen.tp_taker,
        scen.all_taker,
        id(signals),
        signals.shape[0],
    )
    cached = _TUNING_PRECOMPUTE_CACHE.get(key)
    if cached is None:
        cfg = data.backtest_cfg.model_copy(update={"threshold": floor})
        cached = precompute_trades_all(
            data.prepared,
            signals,
            data.rules,
            data.groups,
            scen=scen,
            fees=data.fees,
            backtest_cfg=cfg,
            exit_cfg=exit_cfg,
            sizing_cfg=data.sizing_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            H_label=data.features_cfg.label.H_label,
            p_min=floor,
        )
        _TUNING_PRECOMPUTE_CACHE[key] = cached
    trades = [trade for trade in cached[0] if trade.p >= threshold]
    skipped = [item for item in cached[1] if item.p >= threshold]
    return trades, skipped


def _simulate(
    data: TuningData,
    trades: list[PrecomputedTrade],
    skipped: list[SkippedSignal],
    initial_equity: float,
) -> PortfolioResult:
    """组合模拟（06 §3.4 第 2 步），盯市曲线只用持仓可能触及的验证段 bars。"""
    symbols = {t.symbol for t in trades}
    bars = {alias: data.bars_mtm[alias] for alias in symbols if alias in data.bars_mtm}
    return simulate_portfolio(
        trades,
        skipped,
        sizing_cfg=data.sizing_cfg,
        backtest_cfg=data.backtest_cfg,
        fees=data.fees,
        initial_equity=initial_equity,
        bars_by_symbol=bars,
    )


def _scan_theta(
    data: TuningData,
    trades: list[PrecomputedTrade],
    skipped: list[SkippedSignal],
    initial_equity: float,
) -> dict[str, float]:
    """在验证段按 05 §7.2 选阈值：日均 ≥ 3 笔约束下取 PF 最高的 θ。"""
    best = {"theta": 0.5, "pf": 0.0, "net": float("-inf"), "n_trades": 0.0}
    for theta in _THETA_GRID:
        filtered = [t for t in trades if t.p >= theta]
        result = _simulate(data, filtered, skipped, initial_equity)
        metrics = compute_metrics(result, initial_equity)
        pf = float(metrics.get("profit_factor") or 0.0)
        net = float(metrics.get("net_return_usdt") or 0.0)
        n_trades = float(metrics.get("n_trades") or 0.0)
        if n_trades < _MIN_VAL_TRADES:
            continue
        if pf > best["pf"]:
            best = {"theta": theta, "pf": pf, "net": net, "n_trades": n_trades}
    return best


def _trial_run_id(trial: BackfillTrial) -> str:
    """历史补登组的 run_id（含全部参数与验证段，便于逐组核对）。"""
    suffix = "" if trial.n_trades is not None else "_n_missing"
    split = "valM11-12" if trial.round in ("r1", "r2") else "valM10-12"
    return (
        f"t4-3-s1-{trial.round}-g{trial.group}"
        f"_H{trial.h_max}_kt{trial.k_trail:g}_ksl{trial.k_sl:g}"
        f"_slmin{trial.sl_min * 100:g}_slmax{trial.sl_max * 100:g}"
        f"_th{trial.theta:.2f}_{split}{suffix}"
    )


def _log_trial(
    data: TuningData, run_id: str, payload: dict[str, object], metrics: dict[str, float]
) -> None:
    """把一组试验写入 EXPERIMENTS.log（05 §6.1、06 §8）；同一 run_id 只写一次。"""
    existing = {str(row.get("run_id")) for row in read_experiments_log(data.log_path)}
    if run_id in existing:
        print(f"EXPERIMENTS.log 已有 {run_id}，跳过", flush=True)
        return
    config_hash = compute_experiment_hash(payload, data.config_inputs)
    append_experiments_log(data.log_path, run_id, config_hash, metrics)
    print(f"已写入 EXPERIMENTS.log：{run_id}", flush=True)


def _trial_payload(
    data: TuningData,
    *,
    mode: str,
    group: int,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    h_max: int,
    k_trail: float,
    theta: float,
    split: str,
) -> dict[str, object]:
    """构造第 1 步一组的配置快照（写入 EXPERIMENTS.log 的 config_hash）。"""
    features = data.features_cfg.model_dump(mode="json")
    features["label"] = {
        **features["label"],
        "k_sl": k_sl,
        "sl_min": sl_min,
        "sl_max": sl_max,
    }
    return {
        "mode": mode,
        "group": group,
        "grid": {
            "H_max": h_max,
            "k_trail": k_trail,
            "k_sl": k_sl,
            "sl_min": sl_min,
            "sl_max": sl_max,
            "theta": theta,
        },
        "split": split,
        "base": data.base_cfg.model_dump(mode="json"),
        "backtest": data.backtest_cfg.model_dump(mode="json"),
        "exits": data.exits_cfg.model_dump(mode="json"),
        "features": features,
        "sizing": data.sizing_cfg.model_dump(mode="json"),
    }


def _search_run_id(
    group: int, k_sl: float, sl_min: float, sl_max: float, h_max: int, k_trail: float, theta: float
) -> str:
    """第 1 步现行网格一组的 run_id（r3 = 训练 M1~M9、验证 M10~M12）。"""
    return (
        f"t4-3-s1-r3-g{group}_H{h_max}_kt{k_trail:g}"
        f"_ksl{k_sl:g}_slmin{sl_min * 100:g}_slmax{sl_max * 100:g}"
        f"_th{theta:.2f}_valM10-12"
    )


def _run_search(data: TuningData, args: argparse.Namespace) -> int:
    """第 1 步主流程：逐组重建标签、训练 M0、选阈值、写 EXPERIMENTS.log。"""
    initial_equity = data.backtest_cfg.reference.initial_equity
    results: list[dict[str, object]] = []
    for gi in range(args.groups):
        grid = _EXIT_GRID[gi]
        exit_cfg = data.exits_cfg.model_copy(
            update={"H_max": int(grid["H_max"]), "k_trail": grid["k_trail"]}
        )
        k_sl, sl_min, sl_max = grid["k_sl"], grid["sl_min"], grid["sl_max"]
        samples = _build_samples(data, exit_cfg, k_sl, sl_min, sl_max)
        if samples is None:
            print(f"组 {gi + 1}：无样本", flush=True)
            continue
        _, val_signals, n_train, n_val = _train_and_predict(data, samples)
        trades, skipped = _precompute_all(
            data,
            val_signals,
            exit_cfg,
            k_sl,
            sl_min,
            sl_max,
            scenario("base"),
            _THETA_GRID[0],
        )
        best = _scan_theta(data, trades, skipped, initial_equity)
        theta = best["theta"]
        row = {
            "group": gi + 1,
            "H_max": int(grid["H_max"]),
            "k_trail": grid["k_trail"],
            "k_sl": k_sl,
            "theta": theta,
            "pf": best["pf"],
            "net_return_usdt": best["net"],
            "n_trades": best["n_trades"],
            "n_train": n_train,
            "n_val": n_val,
        }
        results.append(row)
        if not args.no_log:
            _log_trial(
                data,
                _search_run_id(
                    gi + 1, k_sl, sl_min, sl_max, int(grid["H_max"]), grid["k_trail"], theta
                ),
                _trial_payload(
                    data,
                    mode="search",
                    group=gi + 1,
                    k_sl=k_sl,
                    sl_min=sl_min,
                    sl_max=sl_max,
                    h_max=int(grid["H_max"]),
                    k_trail=grid["k_trail"],
                    theta=theta,
                    split="M1-M9/M10-M12",
                ),
                {
                    "net_return_usdt": best["net"],
                    "profit_factor": best["pf"],
                    "n_trades": best["n_trades"],
                },
            )
        print(
            f"组 {gi + 1}: H_max={int(grid['H_max'])} k_trail={grid['k_trail']} k_sl={k_sl} "
            f"→ θ={theta} PF={best['pf']:.3f} 净收益={best['net']:.2f} "
            f"笔数={int(best['n_trades'])}",
            flush=True,
        )

    print("\n==== 结果汇总 ====")
    for r in results:
        print(
            f"组 {r['group']}: H_max={r['H_max']} k_trail={r['k_trail']} k_sl={r['k_sl']} "
            f"θ={r['theta']} PF={r['pf']:.3f} 净收益={r['net_return_usdt']:.2f} "
            f"笔数={int(r['n_trades'])}"
        )
    return 0


def _scenario_row(result: PortfolioResult, initial_equity: float, days: float) -> dict[str, float]:
    """单个情景的核心指标（06 §6）：净收益、PF、笔数、单笔期望 bp、盯市回撤、日均笔数。"""
    metrics = compute_metrics(result, initial_equity)
    trades = result.trades
    valid = [t for t in trades if t.notional > 0.0]
    expectancy_bp = float(np.mean([t.net_pnl / t.notional for t in valid])) * 1e4 if valid else 0.0
    return {
        "net_return_usdt": float(metrics["net_return_usdt"]),
        "profit_factor": float(metrics["profit_factor"]),
        "n_trades": float(len(trades)),
        "expectancy_bp": expectancy_bp,
        "max_drawdown": float(metrics["max_drawdown"]),
        "trades_per_day": len(trades) / days if days > 0 else 0.0,
    }


def _net_at_fixed_slip(
    data: TuningData,
    signals: pd.DataFrame,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    theta: float,
    initial_equity: float,
    slip: float,
) -> float:
    """给定单边固定滑点时验证段的净收益（06 §4.3 `breakeven` 的求根函数）。"""
    scen = replace(scenario("base"), name="breakeven", fixed_slip=slip)
    trades, skipped = _precompute_all(data, signals, exit_cfg, k_sl, sl_min, sl_max, scen, theta)
    return float(sum(t.net_pnl for t in _simulate(data, trades, skipped, initial_equity).trades))


def _checkup_rows(
    data: TuningData,
    signals: pd.DataFrame,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    theta: float,
) -> list[dict[str, float | str]]:
    """组 1 体检：base/stress/hell 三情景 + 盈亏平衡滑点（只报告，不参与选择）。"""
    initial_equity = data.backtest_cfg.reference.initial_equity
    days = (data.val_end_ms - data.val_start_ms) / _MS_PER_DAY
    rows: list[dict[str, float | str]] = []
    for name in ("base", "stress", "hell"):
        trades, skipped = _precompute_all(
            data, signals, exit_cfg, k_sl, sl_min, sl_max, scenario(name), theta
        )
        result = _simulate(data, trades, skipped, initial_equity)
        rows.append({"scenario": name, **_scenario_row(result, initial_equity, days)})

    breakeven = breakeven_slip(
        net_return=lambda slip: _net_at_fixed_slip(
            data, signals, exit_cfg, k_sl, sl_min, sl_max, theta, initial_equity, slip
        ),
        hi=_BREAKEVEN_HI,
        max_iter=_BREAKEVEN_ITERS,
    )
    scen = replace(scenario("base"), name="breakeven", fixed_slip=breakeven)
    trades, skipped = _precompute_all(data, signals, exit_cfg, k_sl, sl_min, sl_max, scen, theta)
    result = _simulate(data, trades, skipped, initial_equity)
    rows.append(
        {
            "scenario": "breakeven",
            **_scenario_row(result, initial_equity, days),
            "breakeven_bp": breakeven * 1e4,
        }
    )
    return rows


def _fmt_pf(value: float) -> str:
    """PF 可能为 inf（无亏损笔），打印为 inf/数值。"""
    return "inf" if math.isinf(value) else f"{value:.3f}"


def _print_checkup(rows: list[dict[str, float | str]]) -> None:
    """打印体检表（数字来自本次实际运行）。"""
    print(
        f"{'情景':<10}{'净收益 USDT':>14}{'PF':>9}{'笔数':>8}"
        f"{'单笔期望 bp':>13}{'最大回撤':>11}{'日均笔数':>10}"
    )
    for row in rows:
        print(
            f"{str(row['scenario']):<10}"
            f"{float(row['net_return_usdt']):>14.2f}"
            f"{_fmt_pf(float(row['profit_factor'])):>9}"
            f"{int(float(row['n_trades'])):>8}"
            f"{float(row['expectancy_bp']):>13.2f}"
            f"{float(row['max_drawdown']) * 100:>10.2f}%"
            f"{float(row['trades_per_day']):>10.2f}"
        )


def _run_checkup(data: TuningData, args: argparse.Namespace) -> int:
    """组 N 体检：复现其 M0 与冻结阈值，跑三情景与盈亏平衡滑点，并登记「复现」行。"""
    gi = args.checkup_group - 1
    grid = _EXIT_GRID[gi]
    h_max, k_trail = int(grid["H_max"]), grid["k_trail"]
    k_sl, sl_min, sl_max = grid["k_sl"], grid["sl_min"], grid["sl_max"]
    exit_cfg = data.exits_cfg.model_copy(update={"H_max": h_max, "k_trail": k_trail})
    tuning_months = data.base_cfg.data_window.tuning_months
    val_months = ", ".join(data.months[tuning_months - 3 : tuning_months])
    print(
        f"体检组 {gi + 1}：H_max={h_max} k_trail={k_trail} k_sl={k_sl} "
        f"sl_min={sl_min * 100:g}% sl_max={sl_max * 100:g}% θ={args.theta:.2f} "
        f"验证段={val_months}",
        flush=True,
    )
    samples = _build_samples(data, exit_cfg, k_sl, sl_min, sl_max)
    if samples is None:
        raise ValueError("组 1 体检无样本（05 §6.1）")
    booster, val_signals, n_train, n_val = _train_and_predict(data, samples)
    print(
        f"训练样本 {n_train}，验证样本 {n_val}，best_iteration={booster.best_iteration}",
        flush=True,
    )
    rows = _checkup_rows(data, val_signals, exit_cfg, k_sl, sl_min, sl_max, args.theta)
    _print_checkup(rows)
    breakeven_bp = float(rows[-1]["breakeven_bp"])
    print(
        f"盈亏平衡滑点：{breakeven_bp:.1f} bp（单边固定滑点；0 表示滑点为 0 时仍亏损，"
        "当前未找到盈亏平衡点）"
    )
    base_row = rows[0]
    if not args.no_log:
        _log_trial(
            data,
            _search_run_id(gi + 1, k_sl, sl_min, sl_max, h_max, k_trail, args.theta) + "_repro",
            _trial_payload(
                data,
                mode="checkup_repro",
                group=gi + 1,
                k_sl=k_sl,
                sl_min=sl_min,
                sl_max=sl_max,
                h_max=h_max,
                k_trail=k_trail,
                theta=args.theta,
                split="M1-M9/M10-M12",
            ),
            {
                "net_return_usdt": float(base_row["net_return_usdt"]),
                "profit_factor": float(base_row["profit_factor"]),
                "n_trades": float(base_row["n_trades"]),
            },
        )
    return 0


def _backfill_log(root: Path, args: argparse.Namespace) -> None:
    """把第 1 步历史轮次补登到 EXPERIMENTS.log（数字取自各轮真实运行日志）。

    历史运行的完整配置快照已不可复现，`config_hash` 由本表记录的参数子集计算
    （payload 带 `mode=backfill` 标记），不是原始运行的全配置哈希；每组笔数
    当时未打印，记 null，不估算。
    """
    base_cfg = load_config(BaseConfig, _resolve(root, args.base))
    log_path = experiments_log_path(_resolve(root, str(base_cfg.paths.artifact_root)))
    existing = {str(row.get("run_id")) for row in read_experiments_log(log_path)}
    written = 0
    for trial in _BACKFILL_TRIALS:
        run_id = _trial_run_id(trial)
        if run_id in existing:
            print(f"EXPERIMENTS.log 已有 {run_id}，跳过")
            continue
        metrics: dict[str, float] = {
            "net_return_usdt": trial.net,
            "profit_factor": trial.pf,
        }
        if trial.n_trades is not None:
            metrics["n_trades"] = trial.n_trades
        payload = {
            "mode": "backfill",
            "round": trial.round,
            "group": trial.group,
            "h_max": trial.h_max,
            "k_trail": trial.k_trail,
            "k_sl": trial.k_sl,
            "sl_min": trial.sl_min,
            "sl_max": trial.sl_max,
            "theta": trial.theta,
        }
        append_experiments_log(log_path, run_id, compute_experiment_hash(payload, {}), metrics)
        written += 1
    total = len(_BACKFILL_TRIALS)
    print(f"补登完成：新写入 {written} 行，历史组共 {total} 行 → {log_path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T4-3 第 1 步：出场/止损搜索与组体检")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--universe", default="configs/universe.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--exits", default="configs/exits.yaml")
    parser.add_argument("--sizing", default="configs/sizing.yaml")
    parser.add_argument("--backtest", default="configs/backtest.yaml")
    parser.add_argument(
        "--features-dir",
        type=Path,
        default=None,
        help="已构建数据集目录（data/features/{run_id}），直接读取落盘 fp16 特征（05 §6.1）",
    )
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个币（冒烟用）")
    parser.add_argument("--groups", type=int, default=6, help="跑前 N 组出场参数")
    parser.add_argument("--checkup", action="store_true", help="只跑组 N 体检（不参与选择）")
    parser.add_argument("--checkup-group", type=int, default=1, help="体检的组号（1 起）")
    parser.add_argument("--theta", type=float, default=0.34, help="体检使用的冻结阈值（05 §7.2）")
    parser.add_argument("--backfill-log", action="store_true", help="补登第 1 步历史组")
    parser.add_argument("--no-log", action="store_true", help="不写 EXPERIMENTS.log（冒烟用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """脚本入口：补登日志 / 组体检 / 出场参数搜索。"""
    root = Path(__file__).resolve().parents[1]
    args = _build_parser().parse_args(argv)
    if args.backfill_log:
        _backfill_log(root, args)
        return 0
    data = _prepare_data(root, args)
    if args.checkup:
        return _run_checkup(data, args)
    return _run_search(data, args)


if __name__ == "__main__":
    raise SystemExit(main())
