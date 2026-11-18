"""R2/R3 网格批跑：按一次只改一个维度的顺序调用 `r2_screen`，输出对比表。

规格：docs/05-模型与训练.md §6.1；docs/06-回测引擎.md §4

用法：
    python scripts/r2_grid.py --batch base --stride 4
    python scripts/r2_grid.py --batch combo --stride 4
"""

from __future__ import annotations

import argparse
import json
import time
from argparse import Namespace
from pathlib import Path

from scripts.r2_screen import screen

# 每批实验：一次只改一个维度（与当前基线对比），名称即 trial_id 后缀
BATCHES: dict[str, list[dict[str, object]]] = {
    "base": [
        {"name": "base", "params": {}},
        {"name": "chase3", "params": {"max_chase": 0.003}},
        {"name": "slmin15", "params": {"sl_min": 0.015}},
        {"name": "slmin20", "params": {"sl_min": 0.020}},
        {"name": "stall40", "params": {"stall_bars": 40}},
        {"name": "stall400", "params": {"stall_bars": 400}},
        {"name": "hmax240", "params": {"H_max": 240}},
        {"name": "be15", "params": {"be_trigger_r": 1.5}},
        {"name": "trail20", "params": {"trail_trigger_r": 2.0}},
        {"name": "tp6", "params": {"tp_cap_r": 6.0}},
        {"name": "ksl30", "params": {"k_sl": 3.0}},
        {"name": "ksl20", "params": {"k_sl": 2.0}},
    ],
    "combo": [
        {"name": "slmin25", "params": {"sl_min": 0.025}},
        {"name": "slmin20_ksl30", "params": {"sl_min": 0.020, "k_sl": 3.0}},
        {
            "name": "slmin20_wide",
            "params": {"sl_min": 0.020, "k_sl": 3.0, "tp_cap_r": 6.0, "H_max": 240},
        },
        {"name": "slmin20_stall40", "params": {"sl_min": 0.020, "stall_bars": 40}},
        {
            "name": "slmin20_ksl30_stall400_tp6",
            "params": {"sl_min": 0.020, "k_sl": 3.0, "stall_bars": 400, "tp_cap_r": 6.0},
        },
        {"name": "ksl30_be15", "params": {"k_sl": 3.0, "be_trigger_r": 1.5}},
        {
            "name": "ksl30_trail10",
            "params": {"k_sl": 3.0, "trail_trigger_r": 1.0, "k_trail": 1.0},
        },
        {
            "name": "ksl30_trail25",
            "params": {"k_sl": 3.0, "trail_trigger_r": 2.0, "k_trail": 2.5},
        },
        {"name": "max_chase5", "params": {"max_chase": 0.005}},
        {"name": "max_chase1", "params": {"max_chase": 0.001}},
    ],
    "select": [
        {"name": "volz2", "params": {"vol_z": 2.0}},
        {"name": "volz3", "params": {"vol_z": 3.0}},
        {"name": "brk05", "params": {"brk_60": 0.005}},
        {"name": "brk10", "params": {"brk_60": 0.010}},
        {"name": "r1sig3", "params": {"r1_sigma": 3.0}},
        {"name": "volz2_brk05", "params": {"vol_z": 2.0, "brk_60": 0.005}},
        {"name": "fixed_rr2", "params": {"exit_type": "fixed", "rr": 2.0}},
        {"name": "fixed_rr3", "params": {"exit_type": "fixed", "rr": 3.0}},
        {"name": "volz2_slmin20", "params": {"vol_z": 2.0, "sl_min": 0.020}},
        {
            "name": "volz2_ksl30_trail10",
            "params": {"vol_z": 2.0, "k_sl": 3.0, "trail_trigger_r": 1.0, "k_trail": 1.0},
        },
    ],
    "tf": [
        {
            "name": "c5_atr5_sl20_h720",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 720,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
            },
        },
        {
            "name": "c5_atr5_sl15_h720",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.015,
                "sl_max": 0.040,
                "H_max": 720,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
            },
        },
        {
            "name": "c5_atr15_sl20_h720",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 15,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 720,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
            },
        },
        {
            "name": "c5_atr5_sl20_h240",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 240,
                "stall_bars": 60,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
            },
        },
        {
            "name": "c5_atr5_sl20_h1440",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 1440,
                "stall_bars": 240,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 8.0,
            },
        },
        {
            "name": "c15_atr15_sl20_h1440",
            "params": {
                "cadence_min": 15,
                "atr_timeframe_min": 15,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 1440,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 8.0,
            },
        },
        {
            "name": "c5_atr5_sl20_h720_lim30",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.020,
                "sl_max": 0.040,
                "H_max": 720,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
            },
        },
        {
            "name": "c5_atr5_sl30_h720",
            "params": {
                "cadence_min": 5,
                "atr_timeframe_min": 5,
                "sl_min": 0.030,
                "sl_max": 0.050,
                "H_max": 720,
                "stall_bars": 120,
                "be_trigger_r": 1.5,
                "trail_trigger_r": 2.0,
                "tp_cap_r": 6.0,
            },
        },
    ],
    "entry2": [
        {
            "name": "lim30n5_slmin25",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.025,
            },
        },
        {
            "name": "lim30n5_slmin30",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.030,
                "sl_max": 0.040,
            },
        },
        {
            "name": "lim50n5_slmin20",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 50.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
            },
        },
        {
            "name": "lim50n10_slmin20",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 50.0,
                "entry_expiry_bars": 10,
                "sl_min": 0.020,
            },
        },
        {
            "name": "lim80n5_slmin20",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 80.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
            },
        },
        {
            "name": "lim50n5_slmin20_ksl30",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 50.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
                "k_sl": 3.0,
            },
        },
        {
            "name": "lim30n5_slmin20_trail10",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
                "trail_trigger_r": 1.0,
                "k_trail": 1.0,
            },
        },
        {
            "name": "lim30n5_slmin20_be15",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
                "be_trigger_r": 1.5,
            },
        },
        {
            "name": "lim30n5_slmin20_tp6_hmax240",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
                "tp_cap_r": 6.0,
                "H_max": 240,
            },
        },
        {
            "name": "lim30n5_slmin20_stall40",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
                "stall_bars": 40,
            },
        },
    ],
    "entry": [
        {
            "name": "lim10n3",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 10.0,
                "entry_expiry_bars": 3,
            },
        },
        {
            "name": "lim20n5",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 20.0,
                "entry_expiry_bars": 5,
            },
        },
        {
            "name": "lim30n5",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
            },
        },
        {
            "name": "lim50n5",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 50.0,
                "entry_expiry_bars": 5,
            },
        },
        {
            "name": "lim30n10",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 10,
            },
        },
        {
            "name": "lim30n5_slmin20",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "sl_min": 0.020,
            },
        },
        {
            "name": "lim20n5_ksl30_trail10",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 20.0,
                "entry_expiry_bars": 5,
                "k_sl": 3.0,
                "trail_trigger_r": 1.0,
                "k_trail": 1.0,
            },
        },
        {
            "name": "lim30n5_volz2",
            "params": {
                "entry_mode": "limit_pullback",
                "entry_offset_bp": 30.0,
                "entry_expiry_bars": 5,
                "vol_z": 2.0,
            },
        },
    ],
}


def _base_args(stride: int) -> dict[str, object]:
    """一次运行的公共参数（只读调参期）。"""
    return {
        "base": "configs/base.yaml",
        "features": "configs/features.yaml",
        "exits": "configs/exits.yaml",
        "stride": stride,
        "limit_coins": None,
        "k_sl": None,
        "sl_min": None,
        "sl_max": None,
        "H_max": None,
        "stall_bars": None,
        "be_trigger_r": None,
        "trail_trigger_r": None,
        "k_trail": None,
        "tp_cap_r": None,
        "max_chase": None,
        "entry_mode": None,
        "entry_offset_bp": None,
        "entry_expiry_bars": None,
        "cadence_min": None,
        "atr_timeframe_min": None,
        "exit_type": None,
        "rr": None,
        "vol_z": None,
        "r1_sigma": None,
        "brk_60": None,
        "position_frac": 0.18,
        "leverage": 5.0,
        "out_dir": "artifacts/reports/r3",
    }


def main(argv: list[str] | None = None) -> int:
    """批跑入口：写每个 trial 的 JSON 并打印对比表。"""
    parser = argparse.ArgumentParser(description="R2/R3 标签级网格")
    parser.add_argument("--batch", default="base", choices=sorted(BATCHES))
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    out_dir = root / "artifacts" / "reports" / "r3"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for item in BATCHES[args.batch]:
        name = f"r2_{item['name']}{args.suffix}"
        params = dict(_base_args(args.stride))
        params.update(item["params"])  # type: ignore[arg-type]
        params["trial_id"] = name
        started = time.time()
        result = screen(Namespace(**params))
        result["elapsed_sec"] = round(time.time() - started, 1)
        (out_dir / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows.append(result)
        print(
            f"{name}: net {float(result['mean_net_bp']):+.2f}bp "
            f"R {float(result['mean_net_r']):+.4f} "
            f"PF {float(result['pf_bp']):.3f}  pos {float(result['positive_rate']):.1%}  "
            f"n {int(result['label_samples'])}  {result['elapsed_sec']}s",
            flush=True,
        )
    print("\n| trial | mean bp | mean R | PF | 正收益率 | 样本 |")
    print("| :-- | --: | --: | --: | --: | --: |")
    for row in rows:
        print(
            f"| {row['trial_id']} | {float(row['mean_net_bp']):+.2f} | "
            f"{float(row['mean_net_r']):+.4f} | {float(row['pf_bp']):.3f} | "
            f"{float(row['positive_rate']):.1%} | {int(row['label_samples'])} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
