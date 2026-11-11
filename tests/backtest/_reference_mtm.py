"""盯市权益曲线的逐元素参考实现（等价性对拍用，不进生产代码）。

规格：docs/06-回测引擎.md §3.4、§5.1

本文件保存 `src/trade/backtest/engine.py` 向量化改造前的逐字实现：
`_close_lookup` 为每个币每分钟建 dict，`_mark_to_market_curve` 逐采样时刻遍历全部交易。
`tests/backtest/test_engine.py` 用它验证新实现逐点等价（AGENTS.md §6 红线 15）。
"""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from trade.backtest.engine import ExecutedTrade


def _close_lookup(bars_by_symbol: dict[str, pd.DataFrame] | None) -> dict[str, dict[int, float]]:
    """预建 open_time → close 映射，供逐分钟盯市（06 §3.4、§5.1）。"""
    if not bars_by_symbol:
        return {}
    lookup: dict[str, dict[int, float]] = {}
    for symbol, bars in bars_by_symbol.items():
        if "open_time" not in bars.columns or "close" not in bars.columns:
            continue
        open_time = bars["open_time"].to_numpy(dtype="int64")
        close = bars["close"].to_numpy(dtype="float64")
        lookup[symbol] = {int(open_time[i]): float(close[i]) for i in range(open_time.shape[0])}
    return lookup


def _mark_to_market_curve(
    trades: list[ExecutedTrade],
    close_lookup: dict[str, dict[int, float]],
    initial_equity: float,
) -> pd.DataFrame:
    """按分钟盯市的权益曲线（06 §3.4、§5.1）。

    权益 = 钱包余额（初始 + 已实现净盈亏） + 未实现盈亏；在每根持仓 K 线收盘处采样。
    """
    if not trades:
        return pd.DataFrame({"ts_ms": [0], "equity": [initial_equity], "drawdown": [0.0]})

    sample_ts: set[int] = set()
    for t in trades:
        sample_ts.add(t.exit_open_time)
        for ts in close_lookup.get(t.symbol, {}):
            if t.entry_open_time <= ts < t.exit_open_time:
                sample_ts.add(ts)

    by_exit: dict[int, float] = {}
    for t in trades:
        by_exit[t.exit_open_time] = by_exit.get(t.exit_open_time, 0.0) + t.net_pnl

    rows: list[tuple[int, float, float]] = []
    realized = 0.0
    peak = initial_equity
    for ts in sorted(sample_ts):
        realized += by_exit.get(ts, 0.0)
        equity = initial_equity + realized
        for t in trades:
            if t.entry_open_time <= ts < t.exit_open_time:
                close_at_ts = close_lookup.get(t.symbol, {}).get(ts)
                if close_at_ts is None or close_at_ts <= 0.0:
                    close_at_ts = t.p_fill
                equity += t.notional * (close_at_ts / t.p_fill - 1.0)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0.0 else 0.0
        rows.append((ts, equity, drawdown))

    if not rows or rows[0][0] != 0:
        rows.insert(0, (0, initial_equity, 0.0))
    return pd.DataFrame(rows, columns=["ts_ms", "equity", "drawdown"])
