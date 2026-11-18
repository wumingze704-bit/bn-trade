"""把 artifacts/reports/r3/r2_*.json 的标签级试验登记到 EXPERIMENTS.log（kind=tuning）。

同一 run_id 已登记则跳过，避免重复计预算。
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from trade.backtest.report import append_experiments_log, experiments_log_path, read_experiments_log
from trade.common.config import BaseConfig, load_config

_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """逐条登记未记录的 trial，返回登记条数。"""
    base = load_config(BaseConfig, _ROOT / "configs" / "base.yaml")
    log_path = experiments_log_path(base.paths.artifact_root)
    existing = {str(row.get("run_id")) for row in read_experiments_log(log_path)}
    added = 0
    for path in sorted(glob.glob(str(_ROOT / "artifacts" / "reports" / "r3" / "r2_*.json"))):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        trial_id = str(payload["trial_id"])
        if trial_id in existing:
            continue
        params = payload.get("params") or {}
        config_hash = json.dumps(params, sort_keys=True, ensure_ascii=False)
        append_experiments_log(
            log_path,
            trial_id,
            config_hash,
            {
                # 标签级筛选：净收益以 bp 计，记入 net_return_usdt 会把单位混进 S11，
                # 因此只写 PF / 笔数，净 bp 留在 r3/*.json 里
                "profit_factor": float(payload["pf_bp"]),
                "n_trades": float(payload["label_samples"]),
            },
            kind="tuning",
            theta=None,
            failed=bool(payload["label_samples"] == 0),
        )
        added += 1
    print(f"登记 {added} 条 trial（已有 {len(existing)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
