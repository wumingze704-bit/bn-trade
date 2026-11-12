"""批量出场入口 `trade.common.batch.batch_exit` 测试（06 §3.4）。"""

from __future__ import annotations

import numpy as np

from tests.common.test_anchors import (
    _EXIT_CFG,
    _FEE_MAKER,
    _FEE_TAKER,
    _bars,
    _series,
    _spec,
)
from trade.common.batch import batch_exit
from trade.common.exits import simulate_exit


def test_batch_exit与逐笔出场逐字段一致() -> None:
    """`batch_exit`（信号位置 → 锚点切片 → 批量状态机）与逐笔结果相同（06 §3.4）。"""
    arrays = _bars()
    anchors = _series(arrays, spec=_spec(), use_numba=True)
    signals = np.array([4, 19, 63, 119], dtype="int64")
    batch = batch_exit(
        open_=arrays["open"],
        high=arrays["high"],
        low=arrays["low"],
        close=arrays["close"],
        p_entry=arrays["p_entry"],
        hi_post=arrays["hi_post"],
        lo_post=arrays["lo_post"],
        is_filled=arrays["is_filled"],
        anchors=anchors,
        signal_bars=signals,
        atr_abs=arrays["atr_abs"],
        h_max=_EXIT_CFG.H_max,
        cfg=_EXIT_CFG,
        tick_size=0.01,
        fee_taker=_FEE_TAKER,
        fee_maker=_FEE_MAKER,
    )
    assert len(batch) == signals.shape[0]
    for slot, signal_bar in enumerate(signals):
        t = int(signal_bar)
        single = simulate_exit(
            arrays["open"],
            arrays["high"],
            arrays["low"],
            arrays["close"],
            arrays["p_entry"],
            arrays["hi_post"],
            arrays["lo_post"],
            arrays["is_filled"],
            t_entry=t + 1,
            p_fill=float(anchors.p_fill[t]),
            sl=float(anchors.sl[t]),
            atr_abs=float(arrays["atr_abs"][t]),
            tick_size=0.01,
            H_max=_EXIT_CFG.H_max,
            cfg=_EXIT_CFG,
            slip_stop=float(anchors.slip_stop[t]),
            slip_exit=float(anchors.slip[t]),
            half_spread=float(anchors.half_spread[t]),
            fee_taker=_FEE_TAKER,
            fee_maker=_FEE_MAKER,
            use_numba=False,
        )
        assert batch.result(slot) == single
