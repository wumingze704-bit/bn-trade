"""出场策略状态机测试（06 §3.3）。

覆盖：入场 K 线规则、保本、追踪、只升不降、最小改动、无进展、H_max、封顶止盈、
同根按止损、跳空、数据末尾、fixed 模式，以及 numba 与 Python 参考一致。
"""

from __future__ import annotations

import numpy as np
import pytest

from trade.common.exits import ExitConfig, exit_policy_hash, simulate_exit

_TOL = 1e-9


def _cfg(**kwargs) -> ExitConfig:
    defaults = dict(type="trail", tp_penetration=0.0001)
    defaults.update(kwargs)
    return ExitConfig(**defaults)


def test_exit_policy_hash_确定性():
    assert exit_policy_hash(_cfg()) == exit_policy_hash(_cfg())
    assert len(exit_policy_hash(_cfg())) == 64


def test_exit_policy_hash_参数改变即变():
    base = exit_policy_hash(_cfg())
    assert exit_policy_hash(_cfg(be_trigger_r=1.1)) != base
    assert exit_policy_hash(_cfg(H_max=60)) != base
    assert exit_policy_hash(ExitConfig(type="fixed", rr=3.0)) != base


def _run(
    open_,
    high,
    low,
    close,
    p_entry,
    hi_post,
    lo_post,
    is_filled,
    *,
    t_entry=1,
    p_fill=100.0,
    sl=0.01,
    atr_abs=0.5,
    tick_size=0.01,
    H_max=120,
    cfg=None,
    slip_stop=0.0,
    slip_exit=0.0,
    half_spread=0.0,
    use_numba=True,
):
    cfg = cfg or _cfg()
    return simulate_exit(
        open_,
        high,
        low,
        close,
        p_entry,
        hi_post,
        lo_post,
        is_filled,
        t_entry=t_entry,
        p_fill=p_fill,
        sl=sl,
        atr_abs=atr_abs,
        tick_size=tick_size,
        H_max=H_max,
        cfg=cfg,
        slip_stop=slip_stop,
        slip_exit=slip_exit,
        half_spread=half_spread,
        fee_taker=0.0005,
        fee_maker=0.0002,
        use_numba=use_numba,
    )


def _flat(n: int, price: float = 100.0):
    open_ = np.full(n, price)
    high = np.full(n, price)
    low = np.full(n, price)
    close = np.full(n, price)
    p_entry = np.full(n, price)
    hi_post = np.full(n, price)
    lo_post = np.full(n, price)
    is_filled = np.zeros(n, dtype="int64")
    return open_, high, low, close, p_entry, hi_post, lo_post, is_filled


def test_入场K线同时低于止损按初始止损():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 98.5  # < D0=99
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_idx == 1
    assert r.exit_type == "initial_stop"
    assert r.exit_price == pytest.approx(99.0, abs=_TOL)


def test_入场K线用post高低点而非整根():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    h[1] = 106.0
    l[1] = 90.0
    hp[1] = 100.5
    lp[1] = 99.5  # > D0，不触发止损
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_idx != 1
    assert r.exit_type != "initial_stop"


def test_保本触发():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.2
    lp[1] = 99.8
    h[2] = 101.3  # HH=101.3 >= 101 → 保本
    l[2] = 100.0
    o[2] = 100.2
    l[3] = 100.0  # <= 保本价 100.15
    o[3] = 100.2
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "breakeven"
    assert r.exit_price == pytest.approx(100.15, abs=_TOL)


def test_追踪上移():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    h[2] = 102.0  # HH=102 → stop 101.25
    l[2] = 100.5
    o[2] = 100.5
    h[3] = 102.5  # HH=102.5 → stop 101.75（上移）
    l[3] = 101.5
    o[3] = 102.0
    h[4] = 102.6
    l[4] = 101.5  # <= 101.75 → 追踪离场
    o[4] = 102.5
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "trail"
    assert r.exit_idx == 4
    assert r.exit_price == pytest.approx(101.75, abs=_TOL)
    assert r.stop_updates >= 2


def test_止损只升不降():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    h[2] = 101.2  # HH=101.2 ≥ 101 → 保本 stop 100.15
    l[2] = 100.5
    o[2] = 100.5
    h[3] = 101.6  # trail candidate = 101.6 - 1.5*1.0 = 100.1 < 100.15 → 不下移
    l[3] = 100.3
    o[3] = 101.0
    h[4] = 101.7
    l[4] = 100.3
    o[4] = 101.5
    l[5] = 100.1  # <= 100.15 → 按保本价离场（未下移到 100.1）
    o[5] = 101.0
    r = _run(o, h, l, c, pe, hp, lp, f, atr_abs=1.0)
    assert r.exit_price == pytest.approx(100.15, abs=_TOL)


def test_最小改动阈值不更新():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    h[2] = 102.0  # stop = 101.25
    l[2] = 101.0
    o[2] = 101.0
    h[3] = 102.05  # candidate 101.30，差 0.05 < 0.1 → 不更新
    l[3] = 101.0
    o[3] = 102.0
    l[4] = 101.2  # <= 101.25（旧 stop）→ trail 离场
    o[4] = 102.0
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "trail"
    assert r.exit_price == pytest.approx(101.25, abs=_TOL)


def test_无进展离场():
    o, h, l, c, pe, hp, lp, f = _flat(40, price=100.0)
    hp[1] = 100.1
    lp[1] = 99.9
    for j in range(2, 21):
        h[j] = 100.1
        l[j] = 99.9
        o[j] = 100.0
    r = _run(o, h, l, c, pe, hp, lp, f, cfg=_cfg(stall_bars=20, stall_r=0.5))
    assert r.exit_type == "stall"
    assert r.exit_idx == 1 + 20


def test_Hmax超时离场():
    o, h, l, c, pe, hp, lp, f = _flat(30, price=100.0)
    hp[1] = 100.1
    lp[1] = 99.9
    for j in range(2, 20):
        h[j] = 100.4
        l[j] = 99.9
        o[j] = 100.0
    r = _run(o, h, l, c, pe, hp, lp, f, cfg=_cfg(H_max=10), H_max=10)
    assert r.exit_type == "timeout"
    assert r.exit_idx == 1 + 10


def test_封顶止盈():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    h[2] = 105.0  # >= 104 * (1+1bp) → 封顶止盈
    l[2] = 100.0
    o[2] = 100.5
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "tp"
    assert r.exit_price == pytest.approx(104.0, abs=_TOL)
    assert r.fee_rate == pytest.approx(0.0002, abs=_TOL)


def test_同根触发止盈止损按止损():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    h[2] = 105.0
    l[2] = 98.0
    o[2] = 100.0
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "initial_stop"
    assert r.fee_rate == pytest.approx(0.0005, abs=_TOL)


def test_跳空按开盘价出场():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.5
    lp[1] = 99.8
    o[2] = 98.0  # 跳空低开 < D0=99
    l[2] = 97.0
    h[2] = 99.5
    r = _run(o, h, l, c, pe, hp, lp, f)
    assert r.exit_type == "initial_stop"
    assert r.exit_price == pytest.approx(98.0, abs=_TOL)


def test_数据末尾按eod():
    o, h, l, c, pe, hp, lp, f = _flat(8, price=100.0)
    hp[1] = 100.1
    lp[1] = 99.9
    r = _run(o, h, l, c, pe, hp, lp, f, cfg=_cfg(H_max=120), H_max=120)
    assert r.exit_type == "eod"
    assert r.exit_idx == 7


def test_fixed模式固定止损止盈():
    o, h, l, c, pe, hp, lp, f = _flat(30)
    hp[1] = 100.1
    lp[1] = 99.8
    h[2] = 103.0  # >= 102 * (1+1bp) → fixed TP
    l[2] = 100.0
    o[2] = 100.5
    r = _run(o, h, l, c, pe, hp, lp, f, cfg=_cfg(type="fixed", rr=2.0), H_max=60)
    assert r.exit_type == "tp"
    assert r.exit_price == pytest.approx(102.0, abs=_TOL)


def test_numba与python一致():
    rng = np.random.default_rng(7)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.002, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.002, n))
    p_entry = close.copy()
    hi_post = high.copy()
    lo_post = low.copy()
    is_filled = np.zeros(n, dtype="int64")
    is_filled[50] = 1

    a = _run(open_, high, low, close, p_entry, hi_post, lo_post, is_filled, use_numba=True)
    b = _run(open_, high, low, close, p_entry, hi_post, lo_post, is_filled, use_numba=False)
    assert a == b
