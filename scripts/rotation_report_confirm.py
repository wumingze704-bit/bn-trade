"""确认回测（测试段 M13~M22）结果整理：从 rot_confirm 产物切出测试段指标并逐项判定。

规格：docs/11-轮动策略.md §6、§7；判定标准见 docs/rotation/phase2.md

只读脚本：不重算策略、不改变任何计算。它把同一次 `--stage confirm` 运行写出的
`summary.json` / `equity.parquet` / `trades.parquet` / `neighbors.json` 中的
M13~M22 区间切出来，再调用冻结的 `trade.rotation.metrics.compute_metrics` 计算指标
（切片区间首日的基准权益取 M12 最后一日权益）。

用法（仓库根目录）：
    python scripts/rotation_report_confirm.py --run-dir artifacts/reports/rotation/rot_confirm
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from trade.rotation.data import MS_DAY, month_keys
from trade.rotation.engine import RotationTrade, SimResult
from trade.rotation.metrics import compute_metrics, eval_segments

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("base", "cost2x", "nohalt", "delay1", "shuffle")

# 测试段 M13~M22：2025-10-01 ~ 2026-08-01（11 §7）
TEST_START_MS = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000)

# 判定标准（任务指令第五部分，事先固定）
SHARPE_MIN = 0.8
DRAWDOWN_MAX = 0.25
WORST_MONTH_MIN = -0.12
LOSING_MONTHS_MAX = 4
NEIGHBOR_POSITIVE_SHARE = 0.5
# 泄露检查触发条件（任务指令第三部分第 7 条）
LEAK_MONTHLY_RETURN_MAX = 0.20
LEAK_SHARPE_MAX = 3.0


def build_parser() -> argparse.ArgumentParser:
    """命令行参数。"""
    parser = argparse.ArgumentParser(description="整理确认回测的测试段（M13~M22）指标")
    parser.add_argument(
        "--run-dir",
        default=str(ROOT / "artifacts" / "reports" / "rotation" / "rot_confirm"),
        help="rot_confirm 产物目录",
    )
    return parser


def _slice_trades(frame: pd.DataFrame, scenario: str) -> list[RotationTrade]:
    """把 trades.parquet 的行还原成 RotationTrade（按出场时间落在测试段内筛选）。"""
    picked = frame[
        (frame["scenario"] == scenario)
        & (frame["exit_ts_ms"] >= TEST_START_MS)
        & (frame["exit_ts_ms"] < TEST_END_MS)
    ]
    trades: list[RotationTrade] = []
    for row in picked.to_dict("records"):
        trades.append(
            RotationTrade(
                symbol=str(row["symbol"]),
                entry_ts_ms=int(row["entry_ts_ms"]),
                exit_ts_ms=int(row["exit_ts_ms"]),
                entry_p1s=float(row["entry_p1s"]),
                exit_p1s=float(row["exit_p1s"]),
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                qty=float(row["qty"]),
                notional=float(row["notional"]),
                stop_pct=float(row["stop_pct"]),
                entry_fee=float(row["entry_fee"]),
                exit_fee=float(row["exit_fee"]),
                funding=float(row["funding"]),
                gross_pnl_p1s=float(row["gross_pnl_p1s"]),
                net_pnl=float(row["net_pnl"]),
                exit_reason=str(row["exit_reason"]),
                entry_rank=int(row["entry_rank"]),
                entry_score=float(row["entry_score"]),
            )
        )
    return trades


def slice_result(
    scenario: str, trades_frame: pd.DataFrame, equity_frame: pd.DataFrame
) -> tuple[SimResult, float]:
    """切出某情景的测试段模拟结果与区间起点权益（M12 最后一日权益）。

    Args:
        scenario: 情景名。
        trades_frame: trades.parquet 的内容。
        equity_frame: equity.parquet 的内容。

    Returns:
        (SimResult, initial_equity)：`daily` 只含测试段内日末权益。

    Raises:
        ValueError: 该情景缺少测试段之前的权益点或测试段内没有权益点。
    """
    curve = equity_frame[equity_frame["scenario"] == scenario].sort_values("ts_ms")
    prior = curve[curve["ts_ms"] < TEST_START_MS]
    if prior.empty:
        raise ValueError(f"情景 {scenario} 缺少测试段之前的权益点，无法切片（11 §6）")
    initial = float(prior["equity"].iloc[-1])
    window = curve[(curve["ts_ms"] >= TEST_START_MS) & (curve["ts_ms"] < TEST_END_MS)]
    if window.empty:
        raise ValueError(f"情景 {scenario} 在测试段内没有日末权益（11 §6）")
    daily = [(int(ts), float(eq)) for ts, eq in zip(window["ts_ms"], window["equity"], strict=True)]
    return (
        SimResult(
            scenario=scenario,
            trades=_slice_trades(trades_frame, scenario),
            daily=daily,
            final_equity=float(window["equity"].iloc[-1]),
        ),
        initial,
    )


def test_segment_metrics(
    scenario: str, trades_frame: pd.DataFrame, equity_frame: pd.DataFrame
) -> tuple[dict[str, Any], dict[str, float]]:
    """计算某情景测试段（M13~M22）的全部指标（复用冻结的 compute_metrics）。"""
    months = month_keys(TEST_START_MS, TEST_END_MS)
    result, initial = slice_result(scenario, trades_frame, equity_frame)
    bundle = compute_metrics(
        result,
        initial_equity=initial,
        months=months,
        segments=eval_segments(months),
        eval_years=(TEST_END_MS - TEST_START_MS) / (365.0 * MS_DAY),
    )
    values: dict[str, Any] = dict(bundle.values)
    values["start_equity"] = initial
    values["end_equity"] = float(result.final_equity)
    values["months"] = list(months)
    return values, dict(bundle.monthly)


def _load_neighbors(path: Path) -> list[dict[str, Any]]:
    """读取 neighbors.json（缺失时抛错，不静默跳过）。"""
    if not path.is_file():
        raise ValueError(f"缺少 neighbors.json：{path}（11 §7）")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("neighbors")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} 中没有任何 neighbors 记录（11 §7）")
    return [dict(row) for row in rows]


def _fmt_pct(value: float) -> str:
    """百分比格式（NaN 写 —）。"""
    return "—" if value != value else f"{value * 100:+.2f}%"


def _fmt_ratio(value: float) -> str:
    """比例格式（NaN 写 —，表示无定义）。"""
    return "—" if value != value else f"{value:.3f}"


def _variant_label(variant: dict[str, Any]) -> str:
    """邻居组标签：N{ma_days} L{lookback_days} K{top_k}。"""
    return f"N{int(variant['ma_days'])} L{int(variant['lookback_days'])} K{int(variant['top_k'])}"


def judge(
    values: dict[str, dict[str, Any]], neighbors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """逐项判定（阈值事先固定，见模块常量）。"""
    base = values["base"]
    cost2x = values["cost2x"]
    positive_neighbors = [row for row in neighbors if float(row["net_return"]) > 0.0]
    return [
        {
            "item": "base 净收益 > 0 且年化夏普 > 0.8",
            "requirement": "net_return > 0，sharpe > 0.8",
            "actual": (
                f"net_return={_fmt_pct(float(base['net_return']))}，sharpe={base['sharpe']:.3f}"
            ),
            "passed": bool(float(base["net_return"]) > 0.0 and float(base["sharpe"]) > SHARPE_MIN),
        },
        {
            "item": "base 最大回撤 ≤ 25%",
            "requirement": "max_drawdown ≤ 0.25",
            "actual": f"max_drawdown={float(base['max_drawdown']) * 100:.2f}%",
            "passed": bool(float(base["max_drawdown"]) <= DRAWDOWN_MAX),
        },
        {
            "item": "cost2x 净收益 > 0",
            "requirement": "net_return > 0",
            "actual": f"net_return={_fmt_pct(float(cost2x['net_return']))}",
            "passed": bool(float(cost2x["net_return"]) > 0.0),
        },
        {
            "item": "10 个月中亏损月 ≤ 4，最差月 ≥ −12%",
            "requirement": "losing_months ≤ 4 且 worst_month ≥ −0.12",
            "actual": (
                f"losing_months={int(float(base['losing_months']))}，"
                f"worst_month={_fmt_pct(float(base['worst_month']))}"
            ),
            "passed": bool(
                float(base["losing_months"]) <= LOSING_MONTHS_MAX
                and float(base["worst_month"]) >= WORST_MONTH_MIN
            ),
        },
        {
            "item": "bootstrap 日收益均值 95% 置信区间下限 > 0",
            "requirement": "bootstrap_mean_low > 0",
            "actual": (
                f"下限={float(base['bootstrap_mean_low']):.6f}，"
                f"上限={float(base['bootstrap_mean_high']):.6f}"
            ),
            "passed": bool(float(base["bootstrap_mean_low"]) > 0.0),
        },
        {
            "item": "neighbors 中至少一半净收益 > 0",
            "requirement": "净收益为正的邻居组 ≥ 一半",
            "actual": f"{len(positive_neighbors)}/{len(neighbors)} 组为正",
            "passed": bool(len(positive_neighbors) / len(neighbors) >= NEIGHBOR_POSITIVE_SHARE),
        },
    ]


def leakage_check(
    slices: dict[str, dict[str, Any]], full: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """泄露检查：触发条件 + delay1 / shuffle 对照（同一次运行）。"""
    base = slices["base"]
    max_monthly = max(float(value) for value in full["base"]["monthly"].values())
    triggered = bool(
        max_monthly > LEAK_MONTHLY_RETURN_MAX or float(base["sharpe"]) > LEAK_SHARPE_MAX
    )
    return {
        "triggered": triggered,
        "thresholds": {
            "monthly_return_gt": LEAK_MONTHLY_RETURN_MAX,
            "annual_sharpe_gt": LEAK_SHARPE_MAX,
        },
        "max_monthly_return_full_window": max_monthly,
        "base_sharpe_test_segment": float(base["sharpe"]),
        "delay1": {
            "sharpe": float(slices["delay1"]["sharpe"]),
            "net_return": float(slices["delay1"]["net_return"]),
        },
        "shuffle": {
            "sharpe": float(slices["shuffle"]["sharpe"]),
            "net_return": float(slices["shuffle"]["net_return"]),
            "n_trades": int(float(slices["shuffle"]["n_trades"])),
        },
        "nohalt_equals_base": bool(
            abs(float(slices["nohalt"]["net_return"]) - float(base["net_return"])) < 1e-12
        ),
    }


def _scenario_table(slices: dict[str, dict[str, Any]]) -> list[str]:
    """情景对比表（测试段 M13~M22）。"""
    lines = [
        "| 情景 | 年化夏普 | 净收益 | 最大回撤 | 交易笔数 | 亏损月数 | 最差月 | 成本占比 | "
        "换手（年化） | bootstrap 下限 | bootstrap 上限 |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in SCENARIOS:
        values = slices[name]
        lines.append(
            f"| {name} | {values['sharpe']:.3f} | {_fmt_pct(float(values['net_return']))} | "
            f"{float(values['max_drawdown']) * 100:.2f}% | {int(float(values['n_trades']))} | "
            f"{int(float(values['losing_months']))} | {_fmt_pct(float(values['worst_month']))} | "
            f"{_fmt_ratio(float(values['cost_ratio']))} | {float(values['turnover_annual']):.1f} | "
            f"{float(values['bootstrap_mean_low']):.6f} | "
            f"{float(values['bootstrap_mean_high']):.6f} |"
        )
    return lines


def _full_table(full_summary: dict[str, Any]) -> list[str]:
    """完整 confirm 窗口（M2~M22）情景表（summary.json 原值）。"""
    lines = [
        "| 情景 | 年化夏普 | 净收益 | 最大回撤 | 交易笔数 | 亏损月数 | 最差月 | 成本占比 |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in SCENARIOS:
        values = full_summary["scenarios"][name]["values"]
        lines.append(
            f"| {name} | {values['sharpe']:.3f} | {_fmt_pct(float(values['net_return']))} | "
            f"{float(values['max_drawdown']) * 100:.2f}% | {int(float(values['n_trades']))} | "
            f"{int(float(values['losing_months']))} | {_fmt_pct(float(values['worst_month']))} | "
            f"{float(values['cost_ratio']):.3f} |"
        )
    return lines


def _monthly_table(months: dict[str, float]) -> list[str]:
    """base 月收益表（M13~M22）。"""
    header = "| 月份 | " + " | ".join(months) + " |"
    divider = "| :--- | " + " | ".join("---:" for _ in months) + " |"
    row = "| 收益 | " + " | ".join(_fmt_pct(value) for value in months.values()) + " |"
    return [header, divider, row]


def main(argv: list[str] | None = None) -> int:
    """入口：读产物 → 切片 → 判定 → 写 confirm_report.md 与 confirm_summary.json。"""
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    trades_frame = pd.read_parquet(run_dir / "trades.parquet")
    equity_frame = pd.read_parquet(run_dir / "equity.parquet")
    neighbors = _load_neighbors(run_dir / "neighbors.json")

    slices: dict[str, dict[str, Any]] = {}
    monthly: dict[str, dict[str, float]] = {}
    for name in SCENARIOS:
        slices[name], monthly[name] = test_segment_metrics(name, trades_frame, equity_frame)

    verdict_items = judge(slices, neighbors)
    verdict = "PASS" if all(item["passed"] for item in verdict_items) else "FAIL"
    leak = leakage_check(slices, summary["scenarios"])
    base = slices["base"]
    lines = [
        f"# 轮动策略确认回测（测试段 M13~M22）：{verdict}",
        "",
        f"- run_id：`{summary['run_id']}`；stage：`{summary['stage']}`",
        f"- 运行产物：`{run_dir.as_posix()}`",
        f"- 测试段：2025-10-01 ~ 2026-08-01（10 个 UTC 自然月）；区间起点权益 "
        f"{float(base['start_equity']):.4f} USDT（M12 最后一日权益），"
        f"期末权益 {float(base['end_equity']):.4f} USDT",
        "",
        "## 1. 测试段情景对比（判定口径）",
        "",
        *_scenario_table(slices),
        "",
        "成本占比 = Σ(按 p_1s 计的毛收益 − 净收益) ÷ Σ毛收益；测试段毛收益 ≤ 0 时无定义，写 —。",
        "",
        "## 2. base 月收益（M13~M22）",
        "",
        *_monthly_table(monthly["base"]),
        "",
        "## 3. 完整 confirm 窗口（M2~M22）情景对比（summary.json 原值）",
        "",
        *_full_table(summary),
        "",
        "## 4. 判定逐项",
        "",
        "| 判据 | 要求 | 实测 | 结论 |",
        "| :--- | :--- | :--- | :--- |",
        *[
            f"| {item['item']} | {item['requirement']} | {item['actual']} | "
            f"{'通过' if item['passed'] else '不通过'} |"
            for item in verdict_items
        ],
        "",
        "## 5. neighbors（冻结组 + 冻结开关，base 情景）",
        "",
        "| 变体 | 夏普 | 净收益 | 最大回撤 | 笔数 |",
        "| :--- | ---: | ---: | ---: | ---: |",
        *[
            f"| {_variant_label(dict(row['variant']))} | {float(row['sharpe']):.3f} | "
            f"{_fmt_pct(float(row['net_return']))} | "
            f"{float(row['max_drawdown']) * 100:.2f}% | {int(float(row['n_trades']))} |"
            for row in neighbors
        ],
        "",
        "## 6. 泄露检查",
        "",
        f"- 触发条件（月收益 > 20% 或年化夏普 > 3）："
        f"{'触发' if leak['triggered'] else '未触发'}"
        f"（完整窗口最高月收益 {_fmt_pct(leak['max_monthly_return_full_window'])}，"
        f"测试段夏普 {leak['base_sharpe_test_segment']:.3f}）",
        f"- delay1（测试段）：夏普 {leak['delay1']['sharpe']:.3f}、"
        f"净收益 {_fmt_pct(leak['delay1']['net_return'])}",
        f"- shuffle（测试段）：夏普 {leak['shuffle']['sharpe']:.3f}、"
        f"净收益 {_fmt_pct(leak['shuffle']['net_return'])}、{leak['shuffle']['n_trades']} 笔",
        f"- nohalt 与 base 完全相同：{'是' if leak['nohalt_equals_base'] else '否'}",
    ]
    (run_dir / "confirm_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "run_id": summary["run_id"],
        "verdict": verdict,
        "test_segment": {"start_ms": TEST_START_MS, "end_ms": TEST_END_MS, "months": 10},
        "scenarios": {name: values for name, values in slices.items()},
        "monthly": monthly,
        "judgement": verdict_items,
        "leakage": leak,
        "neighbors": neighbors,
        "full_window": {name: summary["scenarios"][name]["values"] for name in SCENARIOS},
    }
    (run_dir / "confirm_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8"
    )
    # ensure_ascii=True：Windows 控制台默认 GBK，转义后不会因中文/减号编码报错
    print(json.dumps({"verdict": verdict, "judgement": verdict_items}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
