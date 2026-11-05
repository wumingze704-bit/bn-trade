"""逐值核对 bars 的 `oi` / `funding_rate` 与原始 metrics / fundingRate 对齐（T2-7）。

规格：docs/03-数据与选币.md §2.3；docs/04-特征与标签.md §2 第 2~3 条。

核对用独立实现（`numpy.searchsorted`）重算期望值，**不复用** `_align_metrics` / `_align_funding`：
- metrics 时刻 T 的值只能被 `close_time >= T + 300000` 的分钟使用；
- 资金费只用最近已结算的一期，结算前不得出现下一期。
任一分钟不一致即以非零状态退出。只应传入调参期 M1~M12 的日期。
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

_AVAILABILITY_LAG_MS = 300_000


def read_zip_csv(path: Path) -> pd.DataFrame:
    """读取单 CSV 的 zip（兼容有无表头，03 §1.1）。"""
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"ZIP 内没有 CSV：{path}")
        with archive.open(names[0]) as handle:
            first_line = handle.readline().decode("utf-8").strip()
    header = None if first_line.replace(".", "", 1).isdigit() else 0
    return pd.read_csv(path, compression="zip", header=header)


def _last_available(
    values_ms: np.ndarray, values: np.ndarray, close_times: np.ndarray
) -> np.ndarray:
    """对每个 close_time 取 `values_ms <= close_time` 的最后一条值（无则 NaN）。"""
    index = np.searchsorted(values_ms, close_times, side="right") - 1
    return np.where(index >= 0, values[np.clip(index, 0, None)], np.nan)


def _mismatch_count(expected: np.ndarray, actual: np.ndarray) -> int:
    both_nan = np.isnan(expected) & np.isnan(actual)
    return int((~(expected == actual) & ~both_nan).sum())


def main(argv: list[str] | None = None) -> int:
    """核对一个「币·月」的若干天，返回 0 表示对齐正确。"""
    parser = argparse.ArgumentParser(description="核对 oi / funding_rate 对齐（T2-7）")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM，必须是调参期 M1~M12")
    parser.add_argument("--days", required=True, help="逗号分隔，如 2025-03-10,2025-03-11")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--bars-dir", default="data/bars")
    args = parser.parse_args(argv)

    raw_root = Path(args.raw_dir)
    days = args.days.split(",")
    start_ms = int(pd.Timestamp(days[0], tz="UTC").timestamp() * 1000)
    end_ms = int((pd.Timestamp(days[-1], tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000)

    bars = pd.read_parquet(
        Path(args.bars_dir) / args.alias / f"{args.month.replace('-', '')}.parquet"
    )
    window = bars[(bars["close_time"] >= start_ms) & (bars["close_time"] < end_ms)]
    window = window.reset_index(drop=True)
    close_times = window["close_time"].to_numpy()

    metrics_paths = sorted(
        (raw_root / "metrics" / args.alias).glob(f"{args.symbol}-metrics-{args.month}-*.zip")
    )
    metrics = pd.concat([read_zip_csv(path) for path in metrics_paths])
    metrics["t_ms"] = (
        pd.to_datetime(metrics["create_time"], utc=True).dt.as_unit("ms").astype("int64")
    )
    metrics["oi_value"] = pd.to_numeric(metrics["sum_open_interest"], errors="coerce")
    metrics = metrics.dropna(subset=["oi_value"]).sort_values("t_ms")
    expected_oi = _last_available(
        (metrics["t_ms"] + _AVAILABILITY_LAG_MS).to_numpy(),
        metrics["oi_value"].to_numpy(),
        close_times,
    )

    funding_path = (
        raw_root / "fundingrate" / args.alias / f"{args.symbol}-fundingRate-{args.month}.zip"
    )
    funding = read_zip_csv(funding_path)
    funding["t_ms"] = pd.to_numeric(funding["calc_time"], errors="coerce").astype("int64")
    funding["rate"] = pd.to_numeric(funding["last_funding_rate"], errors="coerce")
    funding = funding.dropna(subset=["rate"]).sort_values("t_ms")
    expected_funding = _last_available(
        funding["t_ms"].to_numpy(), funding["rate"].to_numpy(), close_times
    )

    oi_mismatch = _mismatch_count(expected_oi, window["oi"].to_numpy(dtype="float64"))
    funding_mismatch = _mismatch_count(
        expected_funding, window["funding_rate"].to_numpy(dtype="float64")
    )

    oi_samples = []
    metrics_in_window = metrics[
        (metrics["t_ms"] >= start_ms) & (metrics["t_ms"] < end_ms)
    ].reset_index(drop=True)
    for position in np.linspace(0, len(metrics_in_window) - 1, 3).astype(int):
        row = metrics_in_window.iloc[int(position)]
        matches = window.index[window["oi"].to_numpy(dtype="float64") == float(row["oi_value"])]
        oi_samples.append(
            {
                "metrics_time_ms": int(row["t_ms"]),
                "available_ms": int(row["t_ms"]) + _AVAILABILITY_LAG_MS,
                "raw_oi": float(row["oi_value"]),
                "first_bar_close_time_ms": int(window.loc[matches[0], "close_time"])
                if len(matches)
                else None,
            }
        )

    funding_samples = []
    for position in range(1, len(funding)):
        settle = int(funding["t_ms"].iloc[position])
        if not (start_ms <= settle < end_ms):
            continue
        before = window[window["close_time"] < settle]
        after = window[window["close_time"] >= settle]
        funding_samples.append(
            {
                "calc_time_ms": settle,
                "raw_rate": float(funding["rate"].iloc[position]),
                "last_bar_before_settlement_rate": None
                if before.empty
                else float(before.iloc[-1]["funding_rate"]),
                "first_bar_at_or_after_settlement_rate": None
                if after.empty
                else float(after.iloc[0]["funding_rate"]),
            }
        )

    print(
        json.dumps(
            {
                "symbol": args.symbol,
                "alias": args.alias,
                "month": args.month,
                "days": days,
                "bars_rows": int(len(window)),
                "oi_mismatch_minutes": oi_mismatch,
                "oi_nan_minutes": int(window["oi"].isna().sum()),
                "funding_mismatch_minutes": funding_mismatch,
                "oi_samples": oi_samples,
                "funding_samples": funding_samples[:3],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if oi_mismatch == 0 and funding_mismatch == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
