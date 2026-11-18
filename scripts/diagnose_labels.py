"""R1 只读诊断：标签质量、特征饱和、方向子集、排序能力与成本分解。

规格：docs/04-特征与标签.md §5~§7；docs/06-回测引擎.md §4、§6

只在调参期 M1~M12 上诊断（不加载测试段 M13~M22，不加载封存集 M23~M24）。
逐币流式读取数据集特征与标签，峰值内存只与单币有关。

用法：
    python scripts/diagnose_labels.py --features-dir data/features/ds_r0_20261118 \
        --labels-dir data/labels/ds_r0_20261118 --out artifacts/reports/diagnose_r1.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from trade.backtest.engine import TradeRule
from trade.backtest.r0 import TrainConfig
from trade.common.anchors import anchor_series
from trade.common.config import BaseConfig, load_config
from trade.common.exits import ExitConfig, exit_anchor_spec
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    month_index_array,
    universe_months,
    window_bounds,
)
from trade.features import FeaturesConfig, enabled_features
from trade.features.kline import rv_15, true_range, wilder_atr
from trade.models.calibration import calibration_metrics, platt_sigmoid
from trade.models.lightgbm import tabular_features_matrix, train_lgbm

_TOP_FRACTIONS = (0.005, 0.01, 0.02, 0.05)


def _load_rules(rules_path: Path, aliases_path: Path) -> dict[str, TradeRule]:
    """读 exchangeInfo 快照（alias → TradeRule，03 §4.6）。"""
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    symbol_to_alias = json.loads(aliases_path.read_text(encoding="utf-8"))
    out: dict[str, TradeRule] = {}
    for symbol, alias in symbol_to_alias.items():
        rule = raw.get(symbol)
        if rule is None:
            continue
        out[alias] = TradeRule(
            tick_size=float(rule["tick_size"]),
            step_size=float(rule["step_size"]),
            min_notional=float(rule["min_notional"]),
        )
    return out


def _summary(values: np.ndarray) -> dict[str, float]:
    """均值与 P10/P25/P50/P75/P90。"""
    if values.size == 0:
        return {"n": 0.0, "mean": float("nan")}
    qs = np.quantile(values, [0.1, 0.25, 0.5, 0.75, 0.9])
    return {
        "n": float(values.size),
        "mean": float(values.mean()),
        "p10": float(qs[0]),
        "p25": float(qs[1]),
        "p50": float(qs[2]),
        "p75": float(qs[3]),
        "p90": float(qs[4]),
    }


def _profit_factor(net_r: np.ndarray) -> float:
    """按 R 计的单笔 PF。"""
    wins = float(net_r[net_r > 0].sum())
    losses = float(-net_r[net_r < 0].sum())
    return wins / losses if losses > 0 else float("inf")


def _top_fraction_stats(scores: np.ndarray, net_r: np.ndarray) -> dict[str, dict[str, float]]:
    """按分数取 top 0.5%/1%/2%/5%，返回每档的样本数、平均净 R 与 PF。"""
    order = np.argsort(-scores, kind="stable")
    out: dict[str, dict[str, float]] = {}
    for fraction in _TOP_FRACTIONS:
        k = max(1, int(round(scores.size * fraction)))
        picked = net_r[order[:k]]
        out[f"{fraction:.3%}"] = {
            "n": float(k),
            "mean_r": float(picked.mean()),
            "pf": _profit_factor(picked),
            "positive_rate": float((picked > 0.0).mean()),
        }
    return out


def _rank_eval(samples: list[dict[str, object]], train_cfg: TrainConfig) -> dict[str, object]:
    """R1-4：训练 M0（分类）与净 R 回归，比较 M10~M12 上按分数排序的顶端表现。"""
    import lightgbm as lgb  # type: ignore[import-untyped]

    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    r_parts: list[np.ndarray] = []
    m_parts: list[np.ndarray] = []
    for item in samples:
        x = np.asarray(item["x"], dtype=np.float32)
        # x 已按标签行取成子集：行号用子集内的 0..n-1，不能再拿原始特征行号
        x_parts.append(tabular_features_matrix(x, np.arange(x.shape[0], dtype="int64")))
        y_parts.append(np.asarray(item["y"], dtype="int64"))
        r_parts.append(np.asarray(item["net_r"], dtype="float64"))
        m_parts.append(np.asarray(item["m"], dtype="int64"))
    x_all = np.concatenate(x_parts)
    y_all = np.concatenate(y_parts)
    r_all = np.concatenate(r_parts)
    m_all = np.concatenate(m_parts)

    train_mask = (m_all >= 1) & (m_all <= 9)
    val_mask = (m_all >= 10) & (m_all <= 12)
    if train_mask.sum() == 0 or val_mask.sum() == 0:
        return {"status": "not_run（训练或验证样本为空）"}

    classifier = train_lgbm(
        x_all[train_mask],
        y_all[train_mask],
        np.ones(int(train_mask.sum()), dtype="float64"),
        x_all[val_mask],
        y_all[val_mask],
        np.ones(int(val_mask.sum()), dtype="float64"),
        num_boost_round=train_cfg.model.lgbm_num_boost_round,
        early_stopping_rounds=train_cfg.model.lgbm_early_stopping_rounds,
        seed=42,
    )
    p_val = np.asarray(
        classifier.predict(x_all[val_mask], num_iteration=classifier.best_iteration),
        dtype="float64",
    )
    logit = np.log(np.clip(p_val, 1e-12, 1 - 1e-12) / np.clip(1 - p_val, 1e-12, 1.0))
    calibration = calibration_metrics(logit, y_all[val_mask].astype("float64"))
    p_cal = platt_sigmoid(logit, calibration["platt_a"], calibration["platt_b"])

    regressor = lgb.train(
        {"objective": "huber", "learning_rate": 0.05, "num_leaves": 31, "verbose": -1, "seed": 42},
        lgb.Dataset(x_all[train_mask], label=r_all[train_mask]),
        num_boost_round=int(train_cfg.model.lgbm_num_boost_round),
        valid_sets=[lgb.Dataset(x_all[val_mask], label=r_all[val_mask])],
        callbacks=[lgb.early_stopping(train_cfg.model.lgbm_early_stopping_rounds, verbose=False)],
    )
    r_hat = np.asarray(
        regressor.predict(x_all[val_mask], num_iteration=regressor.best_iteration), dtype="float64"
    )
    expected = p_cal * r_hat

    net_val = r_all[val_mask]
    return {
        "status": "ok",
        "n_train": int(train_mask.sum()),
        "n_val": int(val_mask.sum()),
        "positive_rate_val": float(y_all[val_mask].mean()),
        "platt_a": float(calibration["platt_a"]),
        "platt_b": float(calibration["platt_b"]),
        "p": _top_fraction_stats(p_cal, net_val),
        "m": _top_fraction_stats(r_hat, net_val),
        "ev": _top_fraction_stats(expected, net_val),
        "baseline": {
            "n": float(net_val.size),
            "mean_r": float(net_val.mean()),
            "pf": _profit_factor(net_val),
        },
    }


def _coin_stats(
    feature_frame: pd.DataFrame,
    label_frame: pd.DataFrame,
    *,
    label_rows: np.ndarray,
    feature_names: list[str],
    clip: float,
    sl_min: float,
    groups: dict[str, dict[str, object]],
    exit_stats: dict[str, dict[str, float]],
    direction_sets: dict[str, dict[str, object]],
    cost_bp: np.ndarray,
) -> np.ndarray:
    """累计单币的 R1-1/R1-3/R1-5 统计，返回该币的净 R 数组。"""
    sl = label_frame["sl"].to_numpy(dtype="float64")
    net_r = label_frame["ret_net"].to_numpy(dtype="float64") / sl
    positive = label_frame["y"].to_numpy(dtype="int64") == 1
    clamped = np.isclose(sl, sl_min, rtol=0.0, atol=1e-12)
    for key, mask in (("clamped", clamped), ("unclamped", ~clamped)):
        groups[key]["n"] = int(groups[key]["n"]) + int(mask.sum())
        groups[key]["pos"] = int(groups[key]["pos"]) + int(positive[mask].sum())
        stored = groups[key]["r"]
        assert isinstance(stored, list)
        stored.append(net_r[mask])

    r1 = feature_frame["r_1"].to_numpy(dtype="float64")[label_rows]
    body = feature_frame["body"].to_numpy(dtype="float64")[label_rows]
    brk = feature_frame["brk_60"].to_numpy(dtype="float64")[label_rows]
    subsets = {
        "all": np.ones(net_r.shape[0], dtype=bool),
        "r1_up_body_up": (r1 > 0.0) & (body > 0.0),
        "brk_60_up": brk > 0.0,
    }
    for key, mask in subsets.items():
        direction_sets[key]["n"] = int(direction_sets[key]["n"]) + int(mask.sum())
        direction_sets[key]["pos"] = int(direction_sets[key]["pos"]) + int(positive[mask].sum())
        stored = direction_sets[key]["r"]
        assert isinstance(stored, list)
        stored.append(net_r[mask])

    for index, exit_type in enumerate(label_frame["exit_type"].astype(str).to_numpy()):
        if not np.isfinite(cost_bp[index]):
            continue
        entry = exit_stats[exit_type]
        entry["n"] += 1.0
        entry["r"] += float(net_r[index])
        entry["entry_bp"] += float(cost_bp[index])
        entry["exit_bp"] += float(cost_bp[index])
    del clip, feature_names
    return net_r


def diagnose(args: argparse.Namespace) -> str:
    """执行 R1-1~R1-5，返回 markdown 报告文本（只读，不写回测结果）。"""
    root = Path(__file__).resolve().parents[1]

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    base_cfg = load_config(BaseConfig, resolve(args.base))
    features_cfg = load_config(FeaturesConfig, resolve(args.features))
    exit_cfg = load_config(ExitConfig, resolve(args.exits))
    train_cfg = load_config(TrainConfig, resolve(args.train))
    features_dir = resolve(args.features_dir)
    labels_dir = resolve(args.labels_dir)

    months = universe_months(base_cfg)
    tuning = months[: base_cfg.data_window.tuning_months]
    wb = window_bounds(base_cfg)
    end_ms = int(
        pd.Timestamp(f"{months[base_cfg.data_window.tuning_months]}-01", tz="UTC").timestamp()
        * 1000
    )
    coins = collect_universe_coins(base_cfg.paths.data_root, tuning)
    if args.limit_coins is not None:
        coins = coins[: args.limit_coins]
    feature_names = [feature.name for feature in enabled_features(features_cfg)]
    rules = _load_rules(
        base_cfg.paths.artifact_root / "exchange_info" / "rules.json",
        base_cfg.paths.data_root / "universe" / "aliases.json",
    )
    missing_rules = sorted(coin.alias for coin in coins if coin.alias not in rules)
    if missing_rules:
        raise ValueError(f"交易规则缺少 {missing_rules[:5]}（03 §4.6、红线 13）")

    clip = features_cfg.ewm.clip
    sl_min = features_cfg.label.sl_min
    groups: dict[str, dict[str, object]] = {
        "clamped": {"n": 0, "pos": 0, "r": []},
        "unclamped": {"n": 0, "pos": 0, "r": []},
    }
    exit_stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0.0, "r": 0.0, "entry_bp": 0.0, "exit_bp": 0.0}
    )
    direction_sets: dict[str, dict[str, object]] = {
        key: {"n": 0, "pos": 0, "r": []} for key in ("all", "r1_up_body_up", "brk_60_up")
    }
    saturation: dict[str, int] = defaultdict(int)
    saturation_total = 0
    samples: list[dict[str, object]] = []
    missing: list[str] = []

    for coin in coins:
        alias = coin.alias
        feature_path = features_dir / f"{alias}.parquet"
        label_path = labels_dir / f"{alias}.parquet"
        if not feature_path.is_file() or not label_path.is_file():
            if args.require_complete:
                raise FileNotFoundError(
                    f"数据集缺少 {alias} 的特征或标签：{feature_path} / {label_path}"
                )
            missing.append(alias)
            continue
        feature_frame = pd.read_parquet(feature_path)
        label_frame = pd.read_parquet(label_path)
        if label_frame.empty:
            continue
        open_time = feature_frame["open_time"].to_numpy(dtype="int64")
        sample_rows = np.nonzero(feature_frame["is_sample"].to_numpy(dtype="int64") == 1)[0]
        if sample_rows.size:
            saturation_total += int(sample_rows.size)
            for name in feature_names:
                values = feature_frame[name].to_numpy(dtype="float64")[sample_rows]
                saturation[name] += int(np.count_nonzero(np.abs(values) >= clip - 1e-9))

        row_of = {int(ts): index for index, ts in enumerate(open_time)}
        label_ts = label_frame["open_time"].to_numpy(dtype="int64")
        label_rows = np.asarray([row_of.get(int(ts), -1) for ts in label_ts], dtype="int64")
        valid = label_rows >= 0
        if not valid.any():
            continue
        label_rows = label_rows[valid]
        frame = label_frame.loc[valid].reset_index(drop=True)
        cost_bp = _cost_bp(
            base_cfg,
            features_cfg,
            exit_cfg,
            rules[alias],
            base_cfg.paths.data_root,
            alias,
            wb,
            end_ms,
            label_rows,
            float(args.position_frac),
            float(args.leverage),
        )
        _coin_stats(
            feature_frame,
            frame,
            label_rows=label_rows,
            feature_names=feature_names,
            clip=clip,
            sl_min=sl_min,
            groups=groups,
            exit_stats=exit_stats,
            direction_sets=direction_sets,
            cost_bp=cost_bp,
        )
        samples.append(
            {
                "rows": label_rows,
                "x": np.asarray(
                    feature_frame[feature_names].to_numpy(dtype="float16")[label_rows],
                    dtype=np.float16,
                ),
                "y": frame["y"].to_numpy(dtype="int64"),
                "net_r": frame["ret_net"].to_numpy(dtype="float64")
                / frame["sl"].to_numpy(dtype="float64"),
                "m": month_index_array(
                    label_ts[valid], base_cfg.data_window.end_month, len(months)
                ),
            }
        )
        del feature_frame, label_frame, frame

    rank = _rank_eval(samples, train_cfg) if samples else {"status": "not_run（无样本）"}
    return _render(
        args,
        tuning=tuning,
        groups=groups,
        exit_stats=exit_stats,
        direction_sets=direction_sets,
        saturation=saturation,
        saturation_total=saturation_total,
        clip=clip,
        rank=rank,
        coverage=(len(coins) - len(missing), len(coins)),
    )


def _cost_bp(
    base_cfg: BaseConfig,
    features_cfg: FeaturesConfig,
    exit_cfg: ExitConfig,
    rule: TradeRule,
    data_root: Path,
    alias: str,
    wb: object,
    end_ms: int,
    label_rows: np.ndarray,
    position_frac: float,
    leverage: float,
) -> np.ndarray:
    """单笔入场/出场成本（bp）：吃单费率 + 半价差 + 滑点（06 §4）。"""
    bars = load_bars(
        data_root,
        alias,
        int(wb.start_ms),
        end_ms,
        holdout_start_ms=int(wb.holdout_start_ms),
        allow_holdout=False,
    )
    close = bars["close"].to_numpy(dtype="float64")
    anchors = anchor_series(
        close=close,
        p_entry=bars["p_1s"].to_numpy(dtype="float64"),
        atr_pct=wilder_atr(
            true_range(
                bars["high"].to_numpy(dtype="float64"),
                bars["low"].to_numpy(dtype="float64"),
                close,
            ),
            14,
        )
        / close,
        rv15=rv_15(close),
        qv=bars["quote_volume"].to_numpy(dtype="float64"),
        tick_size=rule.tick_size,
        spec=exit_anchor_spec(
            exit_cfg,
            k_sl=features_cfg.label.k_sl,
            sl_min=features_cfg.label.sl_min,
            sl_max=features_cfg.label.sl_max,
            half_spread_min=0.0001,
            slip_min=0.0001,
            Y=1.0,
            fixed_slip=None,
            fee_taker=base_cfg.fees.taker,
            notional_ref=100.0 * position_frac * leverage,
            entry_maker=False,
            fee_maker=base_cfg.fees.maker,
        ),
    )
    size = int(anchors.half_spread.shape[0])
    if size == 0:
        return np.full(label_rows.shape[0], np.nan)
    inside = label_rows < size
    idx = np.clip(label_rows, 0, size - 1)
    cost = (base_cfg.fees.taker + anchors.half_spread[idx] + anchors.slip[idx]) * 1e4
    # 标签行可能比 bars 多出一分钟（段尾/末行）：这类行不计入成本统计
    return np.where(inside, cost, np.nan)


def _render(
    args: argparse.Namespace,
    *,
    tuning: list[str],
    groups: dict[str, dict[str, object]],
    exit_stats: dict[str, dict[str, float]],
    direction_sets: dict[str, dict[str, object]],
    saturation: dict[str, int],
    saturation_total: int,
    clip: float,
    rank: dict[str, object],
    coverage: tuple[int, int] = (0, 0),
) -> str:
    """把 R1 结果渲染成 markdown。"""
    lines = [
        "# R1 标签与特征诊断（调参期，只读）",
        "",
        f"- 数据集：`{args.features_dir}` / `{args.labels_dir}`",
        f"- 月份：{tuning[0]} ~ {tuning[-1]}（不加载测试段与封存集）",
        f"- 币覆盖：{coverage[0]} / {coverage[1]}（缺数据集文件的币不计入）",
        f"- 预过滤样本行数（is_sample=1）：{saturation_total}",
        "",
        "## R1-1 按 sl 是否被 sl_min 截断分组",
        "",
        "| 组 | 样本数 | 占比 | 正样本率 | 净 R 均值 | P10 | P50 | P90 |",
        "| :-- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    total = max(1, int(groups["clamped"]["n"]) + int(groups["unclamped"]["n"]))
    for key, label in (("clamped", "被 sl_min 截断"), ("unclamped", "未截断")):
        stored = groups[key]["r"]
        assert isinstance(stored, list)
        values = np.concatenate(stored) if stored else np.empty(0)
        stats = _summary(values)
        lines.append(
            f"| {label} | {int(groups[key]['n'])} | {int(groups[key]['n']) / total:.2%} "
            f"| {int(groups[key]['pos']) / max(1, int(groups[key]['n'])):.2%} "
            f"| {stats.get('mean', float('nan')):+.4f} | {stats.get('p10', float('nan')):+.3f} "
            f"| {stats.get('p50', float('nan')):+.3f} | {stats.get('p90', float('nan')):+.3f} |"
        )
    lines += [
        "",
        "## R1-1b 出场类型分布与平均净 R",
        "",
        "| 出场类型 | 笔数 | 占比 | 平均净 R | 入场成本 bp | 出场成本 bp |",
        "| :-- | --: | --: | --: | --: | --: |",
    ]
    for exit_type, entry in sorted(exit_stats.items(), key=lambda kv: -kv[1]["n"]):
        count = max(1.0, entry["n"])
        lines.append(
            f"| {exit_type} | {int(entry['n'])} | {entry['n'] / total:.2%} "
            f"| {entry['r'] / count:+.4f} | {entry['entry_bp'] / count:.2f} "
            f"| {entry['exit_bp'] / count:.2f} |"
        )
    lines += [
        "",
        "## R1-2 特征饱和占比（|z| ≥ clip，is_sample=1）",
        "",
        f"clip = {clip}；占比 = 饱和行数 / 预过滤样本行数",
        "",
        "| 特征 | 饱和占比 |",
        "| :-- | --: |",
    ]
    for name, count in sorted(saturation.items(), key=lambda kv: -kv[1])[:20]:
        lines.append(f"| {name} | {count / max(1, saturation_total):.4%} |")
    lines += [
        "",
        "## R1-3 方向子集",
        "",
        "| 子集 | 样本数 | 正样本率 | 净 R 均值 | P50 |",
        "| :-- | --: | --: | --: | --: |",
    ]
    for key, label in (
        ("all", "全部样本"),
        ("r1_up_body_up", "r_1>0 且 body>0"),
        ("brk_60_up", "brk_60>0"),
    ):
        stored = direction_sets[key]["r"]
        assert isinstance(stored, list)
        values = np.concatenate(stored) if stored else np.empty(0)
        stats = _summary(values)
        n = max(1, int(direction_sets[key]["n"]))
        lines.append(
            f"| {label} | {int(direction_sets[key]['n'])} "
            f"| {int(direction_sets[key]['pos']) / n:.2%} "
            f"| {stats.get('mean', float('nan')):+.4f} | {stats.get('p50', float('nan')):+.3f} |"
        )
    return _render_rank(lines, rank)


def _render_rank(lines: list[str], rank: dict[str, object]) -> str:
    """渲染 R1-4 排序能力与成本说明。"""
    lines += ["", "## R1-4 M0 排序能力（M1~M9 训练，M10~M12 评估）", ""]
    if rank.get("status") != "ok":
        lines.append(f"- 未运行：{rank.get('status')}")
    else:
        lines.append(
            f"- 训练样本 {rank['n_train']}，评估样本 {rank['n_val']}，"
            f"验证集正样本率 {float(rank['positive_rate_val']):.4f}，"
            f"Platt a={float(rank['platt_a']):.4f} b={float(rank['platt_b']):+.4f}"
        )
        baseline = rank["baseline"]
        assert isinstance(baseline, dict)
        lines.append(
            f"- 评估集整体：平均净 R {float(baseline['mean_r']):+.4f}、"
            f"PF {float(baseline['pf']):.3f}"
        )
        lines += [
            "",
            "| 排序分数 | top 0.5% 平均净 R / PF | top 1% | top 2% | top 5% |",
            "| :-- | :-- | :-- | :-- | :-- |",
        ]
        for key, label in (("p", "分类概率 p"), ("m", "净 R 回归 m"), ("ev", "p×m")):
            table = rank[key]
            assert isinstance(table, dict)
            cells = " | ".join(
                f"{table[name]['mean_r']:+.4f} / {table[name]['pf']:.3f}"
                for name in ("0.500%", "1.000%", "2.000%", "5.000%")
            )
            lines.append(f"| {label} | {cells} |")
    lines += [
        "",
        "## R1-5 成本分解（按出场类型，bp）",
        "",
        "成本 = 吃单费率 + 半价差 + 滑点（入场与出场各计一次）；资金费未单列"
        "（持仓 ≤ H_max 分钟，量级 < 0.1bp）。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """diagnose_labels.py 入口：写 artifacts/reports/diagnose_r1.md。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="R1 标签与特征诊断（只读，调参期）")
    parser.add_argument("--base", default="configs/base.yaml")
    parser.add_argument("--features", default="configs/features.yaml")
    parser.add_argument("--exits", default="configs/exits.yaml")
    parser.add_argument("--train", default="configs/train.yaml")
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--position-frac", type=float, default=0.2)
    parser.add_argument("--leverage", type=float, default=5.0)
    parser.add_argument("--limit-coins", type=int, default=None, help="只诊断前 N 个币（冒烟）")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="数据集必须覆盖全部币（缺文件即报错）；默认允许部分覆盖并在报告里写明",
    )
    parser.add_argument("--out", default="artifacts/reports/diagnose_r1.md")
    args = parser.parse_args(argv)
    text = diagnose(args)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"诊断报告：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
