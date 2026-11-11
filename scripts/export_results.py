"""把调参/回测结果导出为 `docs/回测结果.md` 记录。

数据来源（均为真实运行产物，不伪造）：
- 组级指标：`artifacts/reports/EXPERIMENTS.log` 中 `run_id` 以 `t4_3b_` 开头的行（06 §8 试验登记）；
- θ 与失败标记：`tune_models*.log` 中「`run_id: PF=... net=... n=... θ=...`」行（05 §6.1）。

用法（仓库根目录）：
    python scripts/export_results.py
    python scripts/export_results.py --out docs/回测结果.md
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

from trade.backtest.report import experiments_log_path, read_experiments_log
from trade.common.config import BaseConfig, load_config

_ROW = re.compile(r"^(t4_3b_\w+): PF=(\S+) net=(\S+) n=(\S+) θ=(\S+)(?: 失败=(.*))?$")


def parse_run_logs(root: Path) -> dict[str, dict[str, float | str | bool | None]]:
    """从 `tune_models*.log` 解析每组的 θ 与失败标记（05 §6.1）。

    Args:
        root: 仓库根目录。

    Returns:
        run_id -> {"theta": float|None, "failed": bool}。
    """
    info: dict[str, dict[str, float | str | bool | None]] = {}
    for path in sorted(root.glob("tune_models*.log")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            matched = _ROW.match(line.strip())
            if not matched:
                continue
            theta_raw = matched.group(5)
            info[matched.group(1)] = {
                "theta": None if theta_raw == "None" else float(theta_raw),
                "failed": bool(matched.group(6)),
            }
    return info


def build_markdown(rows: list[dict[str, object]], theta_info: dict) -> str:
    """按组级结果生成 Markdown 表格。"""
    lines = [
        "# 回测结果记录",
        "",
        f"> 生成时间：{datetime.now(UTC).isoformat()}（UTC）",
        "> 数据集：`ds_20261107`；口径：reference 初始本金 10,000 USDT，验证段 M10~M12"
        "（3 个月，非年化）。",
        "> 数据来源：`artifacts/reports/EXPERIMENTS.log`（06 §8）与 `tune_models*.log`。",
        "",
        "## 第 2 步模型超参搜索（12 组）",
        "",
        "| # | 配置 | PF | 净收益(USDT) | 笔数 | θ | 状态 |",
        "|:-|:--|--:|--:|--:|--:|:--|",
    ]
    for index, row in enumerate(rows, 1):
        run_id = str(row["run_id"])
        pf = row.get("profit_factor")
        net = row.get("net_return_usdt")
        n_trades = row.get("n_trades")
        info = theta_info.get(run_id, {})
        failed = bool(info.get("failed", pf is None))
        pf_s = "—" if pf is None else f"{float(pf):.4f}"
        net_s = "—" if net is None else f"{float(net):.2f}"
        n_s = "—" if n_trades is None else str(int(n_trades))
        theta = info.get("theta")
        theta_s = "—" if theta is None else f"{float(theta):.2f}"
        status = "失败" if failed else "完成"
        lines.append(f"| {index} | `{run_id}` | {pf_s} | {net_s} | {n_s} | {theta_s} | {status} |")

    lines.append("")
    done = [
        r
        for r in rows
        if r.get("profit_factor") is not None and r.get("net_return_usdt") is not None
    ]
    if done:
        positive = [r for r in done if float(r["net_return_usdt"]) > 0]
        pool = positive or done
        best = max(pool, key=lambda r: float(r["profit_factor"]))
        lines.append(
            f"当前最优：`{best['run_id']}`（PF={float(best['profit_factor']):.4f}，"
            f"净收益={float(best['net_return_usdt']):.2f} USDT）"
            + ("（无正收益组，按 PF 最高降级选择）" if not positive else "")
        )
        lines.append("")
    lines.append(f"已登记 {len(rows)} / 12 组。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出调参/回测结果到 docs/")
    parser.add_argument("--out", default="docs/回测结果.md")
    parser.add_argument("--base", default="configs/base.yaml")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out

    base = load_config(BaseConfig, root / args.base)
    rows = [
        row
        for row in read_experiments_log(experiments_log_path(base.paths.artifact_root))
        if str(row.get("run_id", "")).startswith("t4_3b_")
    ]
    markdown = build_markdown(rows, parse_run_logs(root))
    out.write_text(markdown, encoding="utf-8")
    print(f"已写入 {out}（{len(rows)} 组）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
