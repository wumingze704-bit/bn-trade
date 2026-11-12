"""锚点数组与批量出场测试（06 §3.2、§3.3、§3.4）。

覆盖：锚点数组与 `compute_entry` 逐字段一致、numba 与 Python 参考一致、
`simulate_exit_batch` 与逐笔 `simulate_exit` 逐字段一致、输入长度不一致时报错。
"""

from __future__ import annotations

import numpy as np
import pytest

from trade.common.anchors import AnchorSpec, anchor_series, anchor_series_py
from trade.common.entry import compute_entry
from trade.common.exits import (
    ExitConfig,
    exit_anchor_spec,
    initial_stop,
    simulate_exit,
    simulate_exit_batch,
    take_profit_price,
)

_EXIT_CFG = ExitConfig(type="trail")
_FEE_TAKER = 0.0005
_FEE_MAKER = 0.0002


def _bars(n: int = 200, seed: int = 7) -> dict[str, np.ndarray]:
    """构造一段随机游走 minutes 数组（测试用，不依赖真实数据）。"""
    gen = np.random.default_rng(seed)
    returns = gen.normal(0.0, 0.002, n)
    close = 100.0 * np.cumprod(1.0 + returns)
    open_ = np.r_[100.0, close[:-1]]
    high = np.maximum(open_, close) * (1.0 + np.abs(gen.normal(0.0, 0.0005, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(gen.normal(0.0, 0.0005, n)))
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "p_entry": close * (1.0 + gen.normal(0.0, 0.0002, n)),
        "hi_post": high * 1.0002,
        "lo_post": low * 0.9998,
        "is_filled": np.zeros(n, dtype="int64"),
        "atr_abs": np.abs(gen.normal(0.15, 0.01, n)),
        "rv15": np.abs(gen.normal(0.0004, 0.00005, n)),
        "qv": gen.uniform(5000.0, 50000.0, n),
    }


def _spec(*, fixed_slip: float | None = None) -> AnchorSpec:
    return exit_anchor_spec(
        _EXIT_CFG,
        k_sl=1.0,
        sl_min=0.005,
        sl_max=0.015,
        half_spread_min=0.0001,
        slip_min=0.0001,
        Y=1.0,
        fixed_slip=fixed_slip,
        fee_taker=_FEE_TAKER,
        notional_ref=10.0,
    )


def _series(arrays: dict[str, np.ndarray], *, spec: AnchorSpec, use_numba: bool) -> object:
    return anchor_series(
        close=arrays["close"],
        p_entry=arrays["p_entry"],
        atr_pct=arrays["atr_abs"] / arrays["close"],
        rv15=arrays["rv15"],
        qv=arrays["qv"],
        tick_size=0.01,
        spec=spec,
        use_numba=use_numba,
    )


def test_锚点数组与compute_entry逐字段一致() -> None:
    """锚点数组的入场成本与单笔 `compute_entry` 相同（06 §3.2、§3.4）。"""
    arrays = _bars()
    spec = _spec()
    series = _series(arrays, spec=spec, use_numba=False)
    tick = 0.01
    for t in (0, 1, 37, 150):
        entry = compute_entry(
            c_t=float(arrays["close"][t]),
            p_raw=float(arrays["p_entry"][t + 1]),
            sigma=float(arrays["atr_abs"][t] / arrays["close"][t]),
            atr_abs=float(arrays["atr_abs"][t]),
            rv15=float(arrays["rv15"][t]),
            qv=float(arrays["qv"][t]),
            tick_size=tick,
            k_sl=spec.k_sl,
            sl_min=spec.sl_min,
            sl_max=spec.sl_max,
            notional_ref=spec.notional_ref,
            half_spread_min=spec.half_spread_min,
            slip_min=spec.slip_min,
            Y=spec.Y,
            fixed_slip=None,
            fee_taker=spec.fee_taker,
        )
        assert series.p_fill[t] == entry.p_fill
        assert series.sl[t] == entry.sl
        assert series.slip[t] == entry.slip
        assert series.slip_stop[t] == entry.slip_stop
        assert series.half_spread[t] == entry.half_spread
        assert series.loss_rate[t] == entry.loss_rate
        assert series.down[t] == initial_stop(entry.p_fill, entry.sl, tick)
        assert series.up[t] == take_profit_price(entry.p_fill, entry.sl, _EXIT_CFG, tick)
        assert bool(series.valid[t])


def test_锚点数组numba与python一致() -> None:
    """numba 版锚点数组与纯 Python 参考逐字段相同（AGENTS.md §3.2）。"""
    arrays = _bars()
    fields = (
        "down",
        "up",
        "p_fill",
        "sl",
        "r_dist",
        "min_move",
        "slip",
        "slip_stop",
        "half_spread",
        "loss_rate",
    )
    for spec in (_spec(), _spec(fixed_slip=0.0003)):
        fast = _series(arrays, spec=spec, use_numba=True)
        slow = anchor_series_py(
            close=arrays["close"],
            p_entry=arrays["p_entry"],
            atr_pct=arrays["atr_abs"] / arrays["close"],
            rv15=arrays["rv15"],
            qv=arrays["qv"],
            tick_size=0.01,
            spec=spec,
        )
        for name in fields:
            np.testing.assert_array_equal(getattr(fast, name), getattr(slow, name), err_msg=name)
        np.testing.assert_array_equal(fast.valid, slow.valid)


def test_锚点数组长度不一致抛错() -> None:
    """输入数组长度不一致时报错，不静默截断（红线 13）。"""
    arrays = _bars()
    with pytest.raises(ValueError, match="锚点输入数组长度不一致"):
        anchor_series(
            close=arrays["close"],
            p_entry=arrays["p_entry"][:-1],
            atr_pct=arrays["atr_abs"] / arrays["close"],
            rv15=arrays["rv15"],
            qv=arrays["qv"],
            tick_size=0.01,
            spec=_spec(),
        )


def _batch(arrays: dict[str, np.ndarray], spec: AnchorSpec, t_entries: np.ndarray) -> object:
    """构造锚点数组并跑批量出场（测试辅助）。"""
    series = _series(arrays, spec=spec, use_numba=True)
    slots = t_entries - 1
    batch = simulate_exit_batch(
        arrays["open"],
        arrays["high"],
        arrays["low"],
        arrays["close"],
        arrays["p_entry"],
        arrays["hi_post"],
        arrays["lo_post"],
        arrays["is_filled"],
        t_entries=t_entries,
        down=series.down[slots],
        up=series.up[slots],
        r_dist=series.r_dist[slots],
        min_move=series.min_move[slots],
        p_fill=series.p_fill[slots],
        atr_abs=arrays["atr_abs"][slots],
        slip_stop=series.slip_stop[slots],
        slip_exit=series.slip[slots],
        half_spread=series.half_spread[slots],
        H_max=_EXIT_CFG.H_max,
        cfg=_EXIT_CFG,
        tick_size=0.01,
        fee_taker=_FEE_TAKER,
        fee_maker=_FEE_MAKER,
    )
    return series, batch


def test_批量出场与逐笔出场逐字段一致() -> None:
    """`simulate_exit_batch` 与逐笔 `simulate_exit` 结果逐字段相同（06 §3.4）。"""
    arrays = _bars()
    spec = _spec()
    t_entries = np.array([5, 20, 64, 120], dtype="int64")
    series, batch = _batch(arrays, spec, t_entries)
    assert len(batch) == t_entries.shape[0]
    for slot, t_entry in enumerate(t_entries):
        t = int(t_entry) - 1
        single = simulate_exit(
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["p_entry"],
            arrays["hi_post"],
            arrays["lo_post"],
            arrays["is_filled"],
            t_entry=int(t_entry),
            p_fill=float(series.p_fill[t]),
            sl=float(series.sl[t]),
            atr_abs=float(arrays["atr_abs"][t]),
            tick_size=0.01,
            H_max=_EXIT_CFG.H_max,
            cfg=_EXIT_CFG,
            slip_stop=float(series.slip_stop[t]),
            slip_exit=float(series.slip[t]),
            half_spread=float(series.half_spread[t]),
            fee_taker=_FEE_TAKER,
            fee_maker=_FEE_MAKER,
            use_numba=False,
        )
        assert batch.result(slot) == single


def test_批量出场长度不一致抛错() -> None:
    """每笔数组长度不一致时报错（红线 13）。"""
    arrays = _bars(40)
    series, _ = _batch(arrays, _spec(), np.array([3], dtype="int64"))
    with pytest.raises(ValueError, match="不一致"):
        simulate_exit_batch(
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["p_entry"],
            arrays["hi_post"],
            arrays["lo_post"],
            arrays["is_filled"],
            t_entries=np.array([3, 4], dtype="int64"),
            down=series.down[[2]],
            up=series.up[[2]],
            r_dist=series.r_dist[[2]],
            min_move=series.min_move[[2]],
            p_fill=series.p_fill[[2]],
            atr_abs=arrays["atr_abs"][[2]],
            slip_stop=series.slip_stop[[2]],
            slip_exit=series.slip[[2]],
            half_spread=series.half_spread[[2]],
            H_max=_EXIT_CFG.H_max,
            cfg=_EXIT_CFG,
            tick_size=0.01,
            fee_taker=_FEE_TAKER,
            fee_maker=_FEE_MAKER,
        )


def test_批量出场入场位置越界抛错() -> None:
    """入场位置越过 bars 范围时报错（红线 13）。"""
    arrays = _bars(40)
    series, _ = _batch(arrays, _spec(), np.array([3], dtype="int64"))
    with pytest.raises(ValueError, match="越界"):
        simulate_exit_batch(
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["p_entry"],
            arrays["hi_post"],
            arrays["lo_post"],
            arrays["is_filled"],
            t_entries=np.array([len(arrays["close"])], dtype="int64"),
            down=series.down[[2]],
            up=series.up[[2]],
            r_dist=series.r_dist[[2]],
            min_move=series.min_move[[2]],
            p_fill=series.p_fill[[2]],
            atr_abs=arrays["atr_abs"][[2]],
            slip_stop=series.slip_stop[[2]],
            slip_exit=series.slip[[2]],
            half_spread=series.half_spread[[2]],
            H_max=_EXIT_CFG.H_max,
            cfg=_EXIT_CFG,
            tick_size=0.01,
            fee_taker=_FEE_TAKER,
            fee_maker=_FEE_MAKER,
        )
