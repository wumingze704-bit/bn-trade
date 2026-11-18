"""汇总 artifacts/reports/r3/*.json 成对比表（R2/R3 网格结果）。"""

from __future__ import annotations

import glob
import json
from pathlib import Path


def main() -> int:
    """打印每个 trial 的关键指标（净 bp、净 R、PF、正收益率、样本数）。"""
    root = Path(__file__).resolve().parents[1]
    paths = sorted(glob.glob(str(root / "artifacts" / "reports" / "r3" / "r2_*.json")))
    rows = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    header = f"{'trial':40s} {'net_bp':>8s} {'net_R':>8s} {'PF':>6s} {'pos':>6s} {'n':>10s}"
    print(header)
    for row in rows:
        print(
            f"{row['trial_id']:40s} {row['mean_net_bp']:+8.2f} {row['mean_net_r']:+8.4f} "
            f"{row['pf_bp']:6.3f} {row['positive_rate']:6.3f} {row['label_samples']:10d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
