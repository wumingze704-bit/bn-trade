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
from dataclasses import dataclass, field, replace
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
    precompute_trades,
    precompute_trades_all,
    prepare_symbols,
    simulate_portfolio,
)
from trade.backtest.metrics import compute_metrics, monthly_return_stats
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
from trade.models.calibration import (
    calibration_metrics,
    platt_sigmoid,
    select_threshold,
    threshold_grid,
)
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

# 阈值网格：与模型训练侧共用 `trade.models.calibration.threshold_grid()`（05 §7.2），
# 不再在本脚本里单独写死 0.30~0.80。
_THETA_GRID: list[float] = threshold_grid()

# 验证段阈值约束：日均交易 ≥ 3 笔 × 90 天 ≈ 270 笔（05 §6.1 第 1 步）；阈值规则由
# `trade.models.calibration.select_threshold` 提供，这里不再保留单独的下限常量。

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
    """单币的行情、特征与样本位置（与出场参数无关，一次运行只算一次）。

    低内存模式（`--low-memory`）下 `bars`/`segments`/`x`/`open_time` 为 None，只保留
    样本位置与特征文件路径，按需从磁盘重新读取。
    """

    symbol: str
    bars: pd.DataFrame | None
    segments: np.ndarray | None
    x: np.ndarray | None
    open_time: np.ndarray | None
    sample_positions: np.ndarray
    features_path: Path | None = None
    # 与 bars 行对齐的交易范围掩码（06 §3.1 条件 8）：预计算阶段再校验一次
    trade_mask: np.ndarray | None = None


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
    # 低内存流式模式（05 §6.1）：按需从磁盘读 bars/特征，不再常驻内存
    low_memory: bool = False
    data_root: Path = field(default_factory=Path)
    features_dir: Path | None = None
    bars_start_ms: int = 0
    bars_end_ms: int = 0
    holdout_start_ms: int = 0
    # 交易范围（06 §3.1 条件 8）：流式模式重建掩码用
    membership: dict[str, frozenset[str]] = field(default_factory=dict)
    new_entry_delay_days: int = 0


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


def _validate_features_manifest(
    features_dir: Path,
    features_cfg: FeaturesConfig,
    feature_names: list[str],
    aliases: list[str],
    start_ms: int,
    end_ms: int,
) -> None:
    """只校验数据集 manifest 与文件存在性，不把所有特征读进内存（05 §6.1）。

    Args:
        features_dir: 数据集特征目录 `data/features/{run_id}`。
        features_cfg: 当前特征配置。
        feature_names: 当前启用特征名（有序）。
        aliases: 本次需要的币别名。
        start_ms: 需要的起点（毫秒，含）。
        end_ms: 需要的终点（毫秒，不含）。

    Raises:
        ValueError: manifest 缺失、`feature_hash`/特征列表与当前注册表不一致、
            数据集覆盖区间不包含所需区间，或缺某币的特征文件（红线 13）。
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
    missing = [alias for alias in aliases if not (features_dir / f"{alias}.parquet").is_file()]
    if missing:
        raise ValueError(
            f"数据集缺少币 {len(missing)} 个的特征文件（前 5 个：{missing[:5]}）：{features_dir}"
        )


def _read_coin_features(
    features_dir: Path, alias: str, feature_names: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """读取单个币的特征 parquet（低内存流式模式按币读取）。

    Returns:
        (`open_time` int64 数组, 特征 float16 矩阵)，`open_time` 严格递增。
    """
    path = features_dir / f"{alias}.parquet"
    if not path.is_file():
        raise ValueError(f"数据集缺少币 {alias} 的特征文件：{path}（05 §6.1）")
    frame = pd.read_parquet(path, columns=["open_time", *feature_names])
    times = frame["open_time"].to_numpy(dtype="int64")
    if times.size > 1 and not bool(np.all(np.diff(times) > 0)):
        raise ValueError(f"数据集 open_time 非严格递增：{path}")
    return times, frame[feature_names].to_numpy(dtype="float16")


def _align_values(
    dataset_time: np.ndarray, values: np.ndarray, open_time: np.ndarray, alias: str
) -> np.ndarray:
    """把落盘特征按 bars 的 `open_time` 对齐，返回 fp16 特征矩阵（05 §6.1）。

    Raises:
        ValueError: 数据集特征缺少 bars 的某些分钟，或存在非有限值。
    """
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


def _load_disk_features(
    features_dir: Path,
    features_cfg: FeaturesConfig,
    feature_names: list[str],
    aliases: list[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """读取已构建数据集的特征 parquet，并校验 manifest（05 §6.1）。

    内存版入口（`--low-memory` 请用 `_validate_features_manifest` + `_read_coin_features`）。

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
    _validate_features_manifest(
        features_dir, features_cfg, feature_names, aliases, start_ms, end_ms
    )
    frames: dict[str, tuple[np.ndarray, np.ndarray]] = {
        alias: _read_coin_features(features_dir, alias, feature_names) for alias in aliases
    }
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
    return _align_values(dataset_time, values, open_time, alias)


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
    features_dir = _resolve(root, str(args.features_dir)) if args.features_dir is not None else None
    low_memory = bool(getattr(args, "low_memory", False))
    if low_memory and features_dir is None:
        raise ValueError("--low-memory 需要 --features-dir（流式模式按币读取落盘特征，05 §6.1）")
    disk_features: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
    if features_dir is not None:
        if low_memory:
            # 流式模式只校验 manifest 与文件存在性，不把所有特征读进内存
            _validate_features_manifest(
                features_dir,
                features_cfg,
                feature_names,
                [coin.alias for coin in coins],
                wb.start_ms,
                tuning_end_ms,
            )
        else:
            disk_features = _load_disk_features(
                features_dir,
                features_cfg,
                feature_names,
                [coin.alias for coin in coins],
                wb.start_ms,
                tuning_end_ms,
            )
    # 复用落盘特征时不需要 BTC 上下文（只用于 compute_features）。
    btc_bars = (
        None
        if features_dir is not None
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
        x: np.ndarray | None = None
        if low_memory:
            x = None
        elif disk_features is not None:
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
            direction=features_cfg.prefilter.direction,
        )
        trade_mask = universe_trade_mask(times, coin.symbol, membership, delay)
        sample = sample & trade_mask
        per_coin[alias] = CoinData(
            symbol=coin.symbol,
            bars=None if low_memory else bars,
            segments=None if low_memory else seg,
            x=x,
            open_time=None if low_memory else times,
            sample_positions=np.nonzero(sample)[0],
            features_path=(features_dir / f"{alias}.parquet")
            if low_memory and features_dir
            else None,
            trade_mask=None if low_memory else trade_mask,
        )
        # 盯市权益曲线只需要验证段窗口的开盘时间与收盘价（06 §5.1）：预先切片，
        # 避免每次组合模拟都为全量 M1~M12 bars 重建 close 查找表。
        in_val = (times >= val_start_ms) & (times < tuning_end_ms)
        bars_mtm[alias] = pd.DataFrame(
            {"open_time": times[in_val], "close": bars["close"].to_numpy(dtype="float64")[in_val]}
        )
        if low_memory:
            del bars, seg, times

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
    fees = FeeSchedule(base_cfg.fees.maker, base_cfg.fees.taker, base_cfg.fees.rebate_rate)
    # 每币 numpy 数组只准备一次（06 §3.4），各轮出场参数与 θ 复用同一份数组。
    # 流式模式不常驻这些数组，`_precompute_all` 按币加载验证窗口后再准备。
    prepared = (
        {}
        if low_memory
        else prepare_symbols(
            {alias: info.bars for alias, info in per_coin.items() if info.bars is not None}
        )
    )
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
        low_memory=low_memory,
        data_root=data_root,
        features_dir=features_dir,
        bars_start_ms=wb.start_ms,
        bars_end_ms=tuning_end_ms,
        holdout_start_ms=wb.holdout_start_ms,
        membership=membership,
        new_entry_delay_days=delay,
    )


def _load_bars_for(data: TuningData, alias: str) -> pd.DataFrame:
    """重新读取单币调参期 bars（流式模式用；起止与 holdout 守卫与 `_prepare_data` 一致）。"""
    return load_bars(
        data.data_root,
        alias,
        data.bars_start_ms,
        data.bars_end_ms,
        holdout_start_ms=data.holdout_start_ms,
        allow_holdout=False,
    )


def _bar_times_for(data: TuningData, alias: str, info: CoinData) -> np.ndarray:
    """取单币 bars 的 `open_time` 数组（流式模式只读一列）。"""
    if info.open_time is not None:
        return info.open_time
    if info.bars is not None:
        return info.bars["open_time"].to_numpy(dtype="int64")
    bars = load_bars(
        data.data_root,
        alias,
        data.bars_start_ms,
        data.bars_end_ms,
        holdout_start_ms=data.holdout_start_ms,
        allow_holdout=False,
        columns=["open_time"],
    )
    return bars["open_time"].to_numpy(dtype="int64")


def _coin_labels(
    data: TuningData,
    alias: str,
    info: CoinData,
    *,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    notional_ref: float,
) -> dict[str, object] | None:
    """单币重建 `exit_sim` 标签并算独特性权重，返回紧凑数组（不保留特征）。

    Args:
        data: 运行配置与逐币元数据。
        alias: 币别名。
        info: 该币条目（流式模式下 `bars`/`segments` 为 None，按需从磁盘读取）。
        exit_cfg/k_sl/sl_min/sl_max: 该组出场与止损参数。
        notional_ref: 参考名义价值（滑点模型用）。

    Returns:
        含 `alias/kept/y/w/m/ot/tx` 的字典；该币无样本时返回 None。
    """
    label_cfg = data.features_cfg.label
    bars = info.bars if info.bars is not None else _load_bars_for(data, alias)
    segments = (
        info.segments
        if info.segments is not None
        else segment_ids(bars["open_time"].to_numpy(dtype="int64"))
    )
    labels_df, kept = exit_sim(
        bars,
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
        segments=segments,
    )
    if kept.size == 0:
        return None
    weights = uniqueness_weights(kept, labels_df["bars_held"].to_numpy(dtype="int64"))
    open_time = labels_df["open_time"].to_numpy(dtype="int64")
    return {
        "alias": alias,
        "kept": kept.astype("int64"),
        "y": labels_df["y"].to_numpy(dtype="int64"),
        "w": weights,
        "m": month_index_array(open_time, data.base_cfg.data_window.end_month, len(data.months)),
        "ot": open_time,
        "tx": labels_df["t_exit"].to_numpy(dtype="int64"),
    }


def _feature_block(data: TuningData, alias: str, info: CoinData, kept: np.ndarray) -> np.ndarray:
    """单币按需读取特征并构造 4F 表格化矩阵（流式模式）。

    与 `tabular_features_matrix(info.x, kept)` 结果逐元素一致：先按 bars 的 `open_time`
    对齐特征，再取 [X[t], X[t]−X[t−1], X[t]−X[t−5], X[t]−X[t−15]]。
    """
    if data.features_dir is None:  # pragma: no cover - 由 `_prepare_data` 保证
        raise ValueError("流式模式缺少 --features-dir（05 §6.1）")
    dataset_time, values = _read_coin_features(data.features_dir, alias, data.feature_names)
    times = _bar_times_for(data, alias, info)
    aligned = _align_values(dataset_time, values, times, alias)
    return tabular_features_matrix(aligned, kept)


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
    entries: list[dict[str, object]] = []
    for alias, info in data.per_coin.items():
        entry = _coin_labels(
            data,
            alias,
            info,
            exit_cfg=exit_cfg,
            k_sl=k_sl,
            sl_min=sl_min,
            sl_max=sl_max,
            notional_ref=notional_ref,
        )
        if entry is not None:
            entries.append(entry)
    if not entries:
        return None

    # 内存保护：样本过多时先按固定种子下采样，再拼接（避免峰值内存超限）。
    n_total = sum(int(np.asarray(entry["y"]).size) for entry in entries)
    rng = np.random.default_rng(data.base_cfg.seed)
    keep = (
        np.sort(rng.choice(n_total, _MAX_SAMPLES, replace=False))
        if n_total > _MAX_SAMPLES
        else None
    )
    n_out = _MAX_SAMPLES if keep is not None else n_total

    def local_rows(n: int, offset: int) -> np.ndarray:
        """该币在本次下采样中保留的局部行号（与内存模式同一套索引）。"""
        if keep is None:
            return np.arange(n)
        return keep[(keep >= offset) & (keep < offset + n)] - offset

    if not data.low_memory:
        x_parts: list[np.ndarray] = []
        y_parts: list[np.ndarray] = []
        w_parts: list[np.ndarray] = []
        m_parts: list[np.ndarray] = []
        ot_parts: list[np.ndarray] = []
        tx_parts: list[np.ndarray] = []
        sym_parts: list[np.ndarray] = []
        offset = 0
        for entry in entries:
            alias = str(entry["alias"])
            kept = np.asarray(entry["kept"], dtype="int64")
            local = local_rows(kept.size, offset)
            info = data.per_coin[alias]
            if info.x is None:  # pragma: no cover - 内存模式下必有特征矩阵
                raise ValueError(f"内存模式缺少币 {alias} 的特征矩阵（红线 13）")
            x_parts.append(tabular_features_matrix(info.x, kept[local]))
            y_parts.append(np.asarray(entry["y"])[local])
            w_parts.append(np.asarray(entry["w"])[local])
            m_parts.append(np.asarray(entry["m"])[local])
            ot_parts.append(np.asarray(entry["ot"])[local])
            tx_parts.append(np.asarray(entry["tx"])[local])
            sym_parts.append(np.full(local.size, alias, dtype=object))
            offset += kept.size
        return SampleSet(
            x=np.concatenate(x_parts),
            y=np.concatenate(y_parts),
            w=np.concatenate(w_parts),
            m=np.concatenate(m_parts),
            open_time=np.concatenate(ot_parts),
            symbol=np.concatenate(sym_parts),
            t_exit=np.concatenate(tx_parts),
        )

    # 流式模式（--low-memory）：预分配输出，逐币读取落盘特征后立即释放
    x_out = np.empty((n_out, len(data.feature_names) * 4), dtype=np.float32)
    y_out = np.empty(n_out, dtype=np.int64)
    w_out = np.empty(n_out, dtype=np.float64)
    m_out = np.empty(n_out, dtype=np.int64)
    ot_out = np.empty(n_out, dtype=np.int64)
    tx_out = np.empty(n_out, dtype=np.int64)
    sym_out = np.empty(n_out, dtype=object)
    offset = 0
    pos = 0
    for entry in entries:
        alias = str(entry["alias"])
        kept = np.asarray(entry["kept"], dtype="int64")
        local = local_rows(kept.size, offset)
        if local.size:
            block = _feature_block(data, alias, data.per_coin[alias], kept[local])
            x_out[pos : pos + local.size] = block
            y_out[pos : pos + local.size] = np.asarray(entry["y"])[local]
            w_out[pos : pos + local.size] = np.asarray(entry["w"])[local]
            m_out[pos : pos + local.size] = np.asarray(entry["m"])[local]
            ot_out[pos : pos + local.size] = np.asarray(entry["ot"])[local]
            tx_out[pos : pos + local.size] = np.asarray(entry["tx"])[local]
            sym_out[pos : pos + local.size] = alias
            pos += local.size
        offset += kept.size
    return SampleSet(
        x=x_out[:pos],
        y=y_out[:pos],
        w=w_out[:pos],
        m=m_out[:pos],
        open_time=ot_out[:pos],
        symbol=sym_out[:pos],
        t_exit=tx_out[:pos],
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
    p_val_raw = booster.predict(samples.x[val_mask], num_iteration=booster.best_iteration)
    n_val = int(val_mask.sum())
    # 校准（R0-3）：在验证集 logit 上拟合 Platt（a,b），与 tuning_models / 五折同口径
    logit_val = np.log(np.clip(p_val_raw, 1e-12, 1 - 1e-12) / np.clip(1 - p_val_raw, 1e-12, 1.0))
    calibration = calibration_metrics(logit_val, samples.y[val_mask])
    p_val = platt_sigmoid(logit_val, calibration["platt_a"], calibration["platt_b"])
    val_signals = pd.DataFrame(
        {
            "symbol": samples.symbol[val_mask],
            "open_time": samples.open_time[val_mask],
            "p": p_val,
            "m": np.zeros(n_val),
            "is_sample": np.ones(n_val, dtype="int64"),
            "theta": np.zeros(n_val, dtype="float64"),
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
        if data.low_memory:
            cached = _precompute_streaming(
                data,
                signals,
                cfg,
                exit_cfg,
                k_sl,
                sl_min,
                sl_max,
                scen,
                floor,
            )
        else:
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
                trade_masks={
                    alias: info.trade_mask
                    for alias, info in data.per_coin.items()
                    if info.trade_mask is not None
                }
                or None,
            )
        _TUNING_PRECOMPUTE_CACHE[key] = cached
    trades = [trade for trade in cached[0] if trade.p >= threshold]
    skipped = [item for item in cached[1] if item.p >= threshold]
    return trades, skipped


def _precompute_streaming(
    data: TuningData,
    signals: pd.DataFrame,
    cfg: BacktestConfig,
    exit_cfg: ExitConfig,
    k_sl: float,
    sl_min: float,
    sl_max: float,
    scen: Scenario,
    floor: float,
) -> tuple[list[PrecomputedTrade], list[SkippedSignal]]:
    """流式逐笔预计算：按币加载验证窗口 bars 后立即释放（05 §6.1、06 §3.4）。

    与 `precompute_trades_all(data.prepared, ...)` 结果一致：币的遍历顺序与
    `data.per_coin` 相同，`trade_mask`/`new_entry` 与内存模式一样不启用。

    Args:
        data: 运行配置与逐币元数据（低内存模式）。
        signals: 验证段信号（含 `theta` 列）。
        cfg: 预计算用的回测配置（`threshold = floor`）。
        exit_cfg/k_sl/sl_min/sl_max: 该组出场与止损参数。
        scen: 成本情景。
        floor: 预计算的 p 下限（各 θ 的最小值）。

    Returns:
        (逐笔预计算结果, 被放弃的候选)，顺序与内存模式一致。
    """
    by_symbol = {str(symbol): frame for symbol, frame in signals.groupby("symbol", sort=False)}
    trades: list[PrecomputedTrade] = []
    skipped: list[SkippedSignal] = []
    for alias in data.per_coin:
        frame = by_symbol.get(alias)
        if frame is None or frame.empty:
            continue
        # 验证窗口 + 2 天预热：ATR/mean_60(qv)/rv_15 在窗口起点即可用（06 §3.4）
        bars = load_bars(
            data.data_root,
            alias,
            data.val_start_ms - 2 * 86_400_000,
            data.bars_end_ms,
            holdout_start_ms=data.holdout_start_ms,
            allow_holdout=False,
        )
        prepared = prepare_symbols({alias: bars})[alias]
        coin_mask = universe_trade_mask(
            bars["open_time"].to_numpy(dtype="int64"),
            data.per_coin[alias].symbol,
            data.membership,
            data.new_entry_delay_days,
        )
        coin_trades, coin_skipped = precompute_trades(
            prepared,
            frame,
            symbol=alias,
            group=data.groups[alias],
            rule=data.rules[alias],
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
            trade_mask=coin_mask,
        )
        trades.extend(coin_trades)
        skipped.extend(coin_skipped)
        del bars, prepared
    return trades, skipped


def _simulate(
    data: TuningData,
    trades: list[PrecomputedTrade],
    skipped: list[SkippedSignal],
    initial_equity: float,
    sizing: SizingConfig | None = None,
) -> PortfolioResult:
    """组合模拟（06 §3.4 第 2 步），盯市曲线只用持仓可能触及的验证段 bars。

    `sizing` 为 None 时用关闭 R6/R8/R7 停机的 user 仓位（调参评估口径，R0-2）。
    """
    symbols = {t.symbol for t in trades}
    bars = {alias: data.bars_mtm[alias] for alias in symbols if alias in data.bars_mtm}
    return simulate_portfolio(
        trades,
        skipped,
        sizing_cfg=sizing if sizing is not None else _no_halt_sizing(data.sizing_cfg),
        backtest_cfg=data.backtest_cfg,
        fees=data.fees,
        initial_equity=initial_equity,
        bars_by_symbol=bars,
        entry_maker=data.backtest_cfg.entry_mode == "maker",
    )


def _no_halt_sizing(cfg: SizingConfig) -> SizingConfig:
    """调参评估用仓位：关闭 R6/R8/R7 停机（05 §7.2，正式回测仍用原配置）。"""
    return cfg.model_copy(
        update={
            "daily_loss_stop": 0.99,
            "daily_loss_halt": 0.995,
            "max_drawdown_halt": 0.999,
            "max_consecutive_losses": 1_000_000,
        }
    )


def _scan_theta(
    data: TuningData,
    trades: list[PrecomputedTrade],
    skipped: list[SkippedSignal],
    initial_equity: float,
) -> dict[str, float]:
    """在验证段按 05 §7.2 选阈值：调用 `trade.models.calibration.select_threshold`。

    选 θ 用 reference 仓位（与 backtest.py / G2 同口径）算 PF 与净收益，同时算
    user 仓位的月收益与回撤（R0-2）；两套都关闭 R6/R8 停机。
    """
    days = (data.val_end_ms - data.val_start_ms) / _MS_PER_DAY
    months = _month_keys(data.val_start_ms, data.val_end_ms)
    reference_sizing = _no_halt_sizing(
        data.sizing_cfg.model_copy(
            update={
                "initial_equity": data.backtest_cfg.reference.initial_equity,
                "mode": data.backtest_cfg.reference.mode,
                "risk_per_trade": data.backtest_cfg.reference.risk_per_trade,
                "base": data.backtest_cfg.reference.base,
            }
        )
    )
    user_sizing = _no_halt_sizing(data.sizing_cfg)
    metrics_by_theta: dict[float, dict[str, float]] = {}
    for theta in _THETA_GRID:
        selected = [t for t in trades if t.p >= theta]
        reference_result = _simulate(
            data, selected, skipped, data.backtest_cfg.reference.initial_equity, reference_sizing
        )
        reference_row = compute_metrics(
            reference_result, data.backtest_cfg.reference.initial_equity, months=months
        )
        user_result = _simulate(data, selected, skipped, user_sizing.initial_equity, user_sizing)
        user_stats = monthly_return_stats(
            user_result.equity_curve, user_sizing.initial_equity, months=months
        )
        user_dd = (
            float(user_result.equity_curve["drawdown"].max())
            if not user_result.equity_curve.empty
            else 0.0
        )
        metrics_by_theta[theta] = {
            "net_return": float(reference_row.get("net_return_usdt") or 0.0),
            "profit_factor": float(reference_row.get("profit_factor") or 0.0),
            "n_trades": float(reference_row.get("n_trades") or 0.0),
            "user_median_monthly_return": float(user_stats["median_monthly_return"]),
            "user_worst_monthly_return": float(user_stats["worst_monthly_return"]),
            "user_max_drawdown": user_dd,
        }
    daily_counts = {
        f"{theta:.2f}": round(metrics["n_trades"] / days, 2)
        for theta, metrics in metrics_by_theta.items()
    }
    try:
        choice = select_threshold(metrics_by_theta, n_days=days)
    except ValueError as error:
        raise ValueError(
            f"{error}；各 θ 的日均笔数：{daily_counts}"
            f"（候选 {len(trades)} 笔，验证段 {days:.0f} 天）"
        ) from error
    chosen = metrics_by_theta.get(choice.theta, {})
    return {
        "theta": choice.theta,
        "pf": choice.profit_factor,
        "net": choice.net_return,
        "n_trades": float(choice.n_trades),
        "best_theta": choice.best_theta,
        "best_pf": choice.best_profit_factor,
        "user_median_monthly_return": float(chosen.get("user_median_monthly_return") or 0.0),
        "user_worst_monthly_return": float(chosen.get("user_worst_monthly_return") or 0.0),
        "user_max_drawdown": float(chosen.get("user_max_drawdown") or 0.0),
    }


def _month_keys(start_ms: int, end_ms: int) -> list[str]:
    """[start_ms, end_ms) 覆盖的 UTC 月份列表（调参评估的月度补零网格）。"""
    first = pd.Timestamp(start_ms, unit="ms", tz="UTC")
    last = pd.Timestamp(end_ms - 1, unit="ms", tz="UTC")
    return [
        f"{moment.year:04d}-{moment.month:02d}"
        for moment in pd.date_range(
            first.normalize().replace(day=1), last.normalize().replace(day=1), freq="MS"
        )
    ]


def _nested_signals(
    data: TuningData,
    samples: SampleSet,
    booster: lgb.Booster,
    month_mask: np.ndarray,
    *,
    calib_mask: np.ndarray | None = None,
) -> pd.DataFrame:
    """对指定月份掩码内的样本做预测，返回带 `theta = 0` 的 signals 表（05 §6.1）。

    概率先按 `calib_mask`（默认早停月 M10）的 logit 拟合 Platt 校准，再应用到
    `month_mask`，避免用被评估月份自己拟合校准（R0-3、05 §7.1）。
    """
    n = int(month_mask.sum())
    p_raw = booster.predict(samples.x[month_mask], num_iteration=booster.best_iteration)
    logit = np.log(np.clip(p_raw, 1e-12, 1 - 1e-12) / np.clip(1 - p_raw, 1e-12, 1.0))
    platt_a, platt_b = 1.0, 0.0
    if calib_mask is not None and int(calib_mask.sum()) > 0:
        raw_cal = booster.predict(samples.x[calib_mask], num_iteration=booster.best_iteration)
        logit_cal = np.log(np.clip(raw_cal, 1e-12, 1 - 1e-12) / np.clip(1 - raw_cal, 1e-12, 1.0))
        calibration = calibration_metrics(logit_cal, samples.y[calib_mask])
        platt_a = float(calibration["platt_a"])
        platt_b = float(calibration["platt_b"])
    p = platt_sigmoid(logit, platt_a, platt_b)
    return pd.DataFrame(
        {
            "symbol": samples.symbol[month_mask],
            "open_time": samples.open_time[month_mask],
            "p": p,
            "m": np.zeros(n),
            "is_sample": np.ones(n, dtype="int64"),
            "theta": np.zeros(n, dtype="float64"),
        }
    )


def _run_nested_check(data: TuningData, args: argparse.Namespace) -> int:
    """调参期内部嵌套确认：M10 早停、M11 选 θ、M12 只评估一次（05 §6.1）。

    判定规则在运行前固定：组 N 在 M12 上 PF 排名前 2 且 PF > 1.05 → 保留冻结参数；
    否则回报中位数对应组，由使用者决定是否写回配置。
    """
    tuning_months = data.base_cfg.data_window.tuning_months
    month_stop = data.months[tuning_months - 3]  # M10（早停）
    month_scan = data.months[tuning_months - 2]  # M11（选 θ）
    month_eval = data.months[tuning_months - 1]  # M12（只评估）
    start_stop = rebalance_ms_of(month_stop)
    start_scan = rebalance_ms_of(month_scan)
    start_eval = rebalance_ms_of(month_eval)
    scan_days = (start_eval - start_scan) / _MS_PER_DAY
    eval_days = (data.val_end_ms - start_eval) / _MS_PER_DAY
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
            print(f"嵌套确认组 {gi + 1}：无样本", flush=True)
            continue
        train_mask = (samples.m >= 1) & (samples.m <= 9) & (samples.t_exit < start_stop)
        stop_mask = samples.m == (tuning_months - 2)
        if train_mask.sum() == 0 or stop_mask.sum() == 0:
            raise ValueError(f"嵌套确认组 {gi + 1} 训练/早停样本为空（05 §6.1）")
        booster = train_lgbm(
            samples.x[train_mask],
            samples.y[train_mask],
            samples.w[train_mask],
            samples.x[stop_mask],
            samples.y[stop_mask],
            samples.w[stop_mask],
            seed=data.base_cfg.seed,
        )
        signals_scan = _nested_signals(
            data, samples, booster, samples.m == (tuning_months - 1), calib_mask=stop_mask
        )
        p_scan = signals_scan["p"].to_numpy(dtype="float64")
        print(
            f"组 {gi + 1}: M11 信号 {p_scan.size} 条，p 分位 "
            f"P50={np.quantile(p_scan, 0.50):.3f} P90={np.quantile(p_scan, 0.90):.3f} "
            f"P99={np.quantile(p_scan, 0.99):.3f} max={p_scan.max():.3f}，"
            f"p≥0.30 的条数 {int((p_scan >= 0.30).sum())}",
            flush=True,
        )
        if args.dump_signals is not None and gi == 0:
            # θ 未选出时用 0.0 占位（该文件只用于信号密度统计，05 §7.2）
            _dump_validation_signals(
                args.dump_signals,
                samples,
                booster,
                0.0,
                m_from=tuning_months - 2,
                m_to=tuning_months,
                label=f"M0-{month_stop}..{month_eval}",
            )
        trades_scan, skipped_scan = _precompute_all(
            data, signals_scan, exit_cfg, k_sl, sl_min, sl_max, scenario("base"), _THETA_GRID[0]
        )
        try:
            choice = _scan_theta(data, trades_scan, skipped_scan, initial_equity)
        except ValueError as error:
            print(f"组 {gi + 1}: θ 选择失败（{error}）", flush=True)
            results.append(
                {
                    "group": gi + 1,
                    "net_return_usdt": float("nan"),
                    "profit_factor": float("nan"),
                    "n_trades": 0.0,
                    "expectancy_bp": float("nan"),
                    "max_drawdown": float("nan"),
                    "trades_per_day": 0.0,
                    "theta": None,
                    "scan_days": scan_days,
                    "failure": str(error),
                }
            )
            continue
        signals_eval = _nested_signals(
            data, samples, booster, samples.m == tuning_months, calib_mask=stop_mask
        )
        trades_eval, skipped_eval = _precompute_all(
            data, signals_eval, exit_cfg, k_sl, sl_min, sl_max, scenario("base"), _THETA_GRID[0]
        )
        result = _simulate(
            data,
            [t for t in trades_eval if t.p >= choice["theta"]],
            skipped_eval,
            initial_equity,
        )
        row = {
            "group": gi + 1,
            **_scenario_row(result, initial_equity, eval_days),
            "theta": choice["theta"],
            "scan_days": scan_days,
        }
        results.append(row)
        print(
            f"嵌套确认组 {gi + 1}: θ={choice['theta']} M12 PF={row['profit_factor']:.3f} "
            f"净收益={row['net_return_usdt']:.2f} 笔数={int(row['n_trades'])}",
            flush=True,
        )
        if not args.no_log:
            _log_trial(
                data,
                f"t4-3-s1-nested-g{gi + 1}_H{int(grid['H_max'])}_ksl{k_sl:g}"
                f"_slmin{sl_min * 100:g}_th{choice['theta']:.2f}_evalM12",
                _trial_payload(
                    data,
                    mode="nested",
                    group=gi + 1,
                    k_sl=k_sl,
                    sl_min=sl_min,
                    sl_max=sl_max,
                    h_max=int(grid["H_max"]),
                    k_trail=grid["k_trail"],
                    theta=choice["theta"],
                    split="M1-M9/M10-M12(nested: stop M10, scan M11, eval M12)",
                ),
                {
                    "net_return_usdt": row["net_return_usdt"],
                    "profit_factor": row["profit_factor"],
                    "n_trades": row["n_trades"],
                },
            )
        if args.dump_signals is not None and gi == 0:
            _dump_validation_signals(
                args.dump_signals,
                samples,
                booster,
                choice["theta"],
                m_from=tuning_months - 2,
                m_to=tuning_months,
                label=f"M0-{month_stop}..{month_eval}",
            )
    if not results:
        raise ValueError("嵌套确认没有任何一组产出结果（05 §6.1）")
    failed = [row for row in results if row.get("theta") is None]
    print("\n==== 嵌套确认汇总（按 M12 PF 降序）====")
    ranked = sorted(
        (row for row in results if row.get("theta") is not None),
        key=lambda row: float(row["profit_factor"]),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        print(
            f"排名 {rank}: 组 {row['group']} θ={row['theta']} PF={float(row['profit_factor']):.3f} "
            f"净收益={float(row['net_return_usdt']):.2f} 笔数={int(float(row['n_trades']))}"
        )
    for row in failed:
        print(f"失败: 组 {row['group']} 无法选出 θ（无任何 θ 达到日均 3 笔），原因见上")
    if failed:
        print(
            f"\n判定：{len(failed)}/{len(results)} 组无法选出 θ，嵌套确认无法执行；"
            "按事先约定保留冻结参数（不修改 exits.yaml / features.yaml），原因写入完成报告。"
        )
        return 0
    frozen = results[0]
    frozen_rank = next(
        index for index, row in enumerate(ranked, start=1) if row["group"] == frozen["group"]
    )
    keep = frozen_rank <= 2 and float(frozen["profit_factor"]) > 1.05
    print(
        f"\n判定：组 1 在 M12 排名 {frozen_rank}，PF={float(frozen['profit_factor']):.3f} → "
        + ("保留冻结参数" if keep else "需按中位数改选（由使用者决定，见 05 §6.1）")
    )
    return 0


def _dump_validation_signals(
    path: Path,
    samples: SampleSet,
    booster: lgb.Booster,
    theta: float,
    *,
    m_from: int,
    m_to: int,
    label: str,
) -> None:
    """把验证段的 M0 预测概率写成 signals.parquet（05 §7.2）。

    供 `scripts/analyze_signal_density.py --signals` 统计不同 θ 下的日均信号数；
    `p` 为模型概率（本脚本只做密度统计，不做温度缩放）。

    Args:
        path: 输出路径。
        samples: 该组样本数组。
        booster: 已训练的 M0。
        theta: 本组验证段选定的阈值（写入契约的 `theta` 列）。
        m_from/m_to: 验证段的月序号范围（含两端，1 基）。
        label: `fold_id` 取值（记录验证段月份）。
    """
    mask = (samples.m >= m_from) & (samples.m <= m_to)
    count = int(mask.sum())
    p = booster.predict(samples.x[mask], num_iteration=booster.best_iteration)
    frame = pd.DataFrame(
        {
            "symbol": samples.symbol[mask],
            "open_time": samples.open_time[mask],
            "p": p,
            "m": np.zeros(count, dtype="float64"),
            "is_sample": np.ones(count, dtype="int64"),
            "fold_id": label,
            "theta": np.full(count, float(theta)),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    print(f"验证段信号已导出：{path}（{count} 行）", flush=True)


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
    parser.add_argument(
        "--nested-check",
        action="store_true",
        help="调参期内部嵌套确认（M10 早停、M11 选 θ、M12 评估，05 §6.1）",
    )
    parser.add_argument(
        "--low-memory",
        action="store_true",
        help=(
            "流式低内存模式：按币从磁盘读 bars/特征，"
            "峰值内存 ≈ 2,000,000 行样本（需 --features-dir）"
        ),
    )
    parser.add_argument(
        "--dump-signals",
        type=Path,
        default=None,
        help="把嵌套确认第 1 组的验证段（M10~M12）预测概率写成 signals.parquet（05 §7.2）",
    )
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
    if args.nested_check:
        return _run_nested_check(data, args)
    return _run_search(data, args)


if __name__ == "__main__":
    raise SystemExit(main())
