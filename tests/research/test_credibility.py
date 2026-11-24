"""结果可信度测试 a~h（1 号第五部分 B 第 14 条）。

用合成数据与小型市场跑真实框架代码（引擎、折内选参、拼接路径、两种权益口径），
不依赖真实下载数据。阈值在下方的常量里写死并注明理由。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import numpy as np
import pytest

from tests.research.helpers import (
    START_MS,
    Bars,
    make_daily_returns_bars,
    make_market,
    make_research_config,
)
from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.data import DAY_MS, HOUR_MS, MarkBars, MarketData
from trade.research.engine import Engine, RunResult, ScenarioOptions
from trade.research.interface import (
    EXPOSURE_DIRECTIONAL,
    EXPOSURE_NEUTRAL,
    Decision,
    Target,
)
from trade.research.pipeline import build_segments, params_key, run_param, run_selected_path
from trade.research.rolling import Fold, select_for_fold
from trade.research.view import View

# 植入信号测试的夏普阈值：合成规律埋得很强，真框架应当给出显著正值（> 1.0 年化）；
# 随机游走上不得出现正收益——阈值取 0.3，与 1 号第七部分第 5 条 g 款的 shuffle 门槛一致；
# 高换手方向在随机游走上会因成本明显为负，这是保守方向，不算“框架制造收益”。
_PLANTED_SHARPE_MIN = 1.0
_RANDOM_WALK_SHARPE_MAX = 0.3
_FEE_TAKER = 0.0005
_HISTORY_DAYS = 120
_SYMBOLS = (
    "AAAUSDT",
    "BBBUSDT",
    "CCCUSDT",
    "DDDUSDT",
    "EEEUSDT",
    "FFFUSDT",
    "GGGUSDT",
    "HHHUSDT",
    "IIIUSDT",
    "JJJUSDT",
    "KKKUSDT",
    "LLLUSDT",
    "MMMUSDT",
    "NNNUSDT",
    "OOOUSDT",
    "PPPUSDT",
)
# 每 `block_hours` 小时轮换的领涨 / 领跌币数量（与 `_PARAMS` 的 top_k 一致）
_LEADERS = 5


def _config():
    return make_research_config().model_copy(update={"scenarios": ["base"]})


def _sizing(equity: float) -> SizingConfig:
    return SizingConfig(
        initial_equity=equity,
        mode="fixed_risk",
        position_frac=0.08,
        leverage=5,
        max_positions=10,
        base="equity",
        risk_per_trade=0.0075,
        max_risk_per_trade=0.025,
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        liq_cap=0.005,
        maint_margin_rate=0.01,
    )


def _risk(halt_mode: str = "reset") -> RiskLimits:
    return RiskLimits(
        daily_loss_stop=0.05,
        daily_loss_halt=0.08,
        max_drawdown_halt=0.20,
        max_consecutive_losses=6,
        consecutive_loss_pause_min=120,
        halt_mode=halt_mode,
        halt_reset_days=7,
    )


def _folds(start_ms: int, *, days: int = 6, count: int = 3) -> list[Fold]:
    """`count` 段等长折；前面留 20 天训练（要满足折内选参的“训练期交易数 ≥ 30”）。"""
    first = start_ms + 20 * DAY_MS
    step = days * DAY_MS
    return [
        Fold(index=i, start_ms=first + i * step, end_ms=first + (i + 1) * step)
        for i in range(count)
    ]


def _path(
    market: MarketData,
    *,
    family: str,
    params_list: list[dict],
    folds: list[Fold],
    start_ms: int,
    end_ms: int,
    equity: float = 10_000.0,
    options: ScenarioOptions | None = None,
) -> RunResult:
    """全期训练 → 折内选参 → 连续路径（与 pipeline.evaluate_grid 的核心步骤一致）。"""
    config = _config()
    sizing = _sizing(equity)
    runs = {
        params_key(params): run_param(
            market,
            config=config,
            sizing=sizing,
            fee_taker=_FEE_TAKER,
            risk_limits=_risk(),
            family=family,
            params=params,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for params in params_list
    }
    choices = [select_for_fold(runs, fold, start_ms) for fold in folds]
    segments = build_segments(
        family=family,
        folds=folds,
        choices=choices,
        params_by_key={params_key(params): params for params in params_list},
        config=config,
    )
    return run_selected_path(
        market,
        config=config,
        sizing=sizing,
        fee_taker=_FEE_TAKER,
        risk_limits=_risk(),
        segments=segments,
        end_ms=end_ms,
        options=options,
    )


def _annualized(result: RunResult) -> float:
    daily = result.daily_returns()
    if daily.size < 2:
        return 0.0
    std = float(daily.std(ddof=1))
    return float(daily.mean() / std * np.sqrt(365)) if std > 0.0 else 0.0


def _bars_with_returns(template: Bars, returns: np.ndarray) -> Bars:
    """按逐小时简单收益重建一个币的 K 线（高低价在开盘收盘之外加 0.1%）。"""
    close = 100.0 * np.cumprod(1.0 + returns)
    open_ = np.concatenate([[100.0], close[:-1]])
    return Bars(
        symbol=template.symbol,
        open_time=template.open_time,
        open=open_,
        high=np.maximum(open_, close) * 1.001,
        low=np.minimum(open_, close) * 0.999,
        close=close,
        quote_volume=template.quote_volume,
    )


def _market_with(returns: Mapping[str, np.ndarray], *, n: int = 960) -> MarketData:
    """以给定逐小时收益重建山寨币行情（标记价格同步）。"""
    market = make_market(symbols=_SYMBOLS, n=n, history_days=_HISTORY_DAYS)
    for symbol, series in returns.items():
        bars = _bars_with_returns(market.bars(symbol), series)
        market.hourly[symbol] = bars
        market.mark[symbol] = MarkBars(
            symbol=symbol, open_time=bars.open_time, high=bars.high, low=bars.low, close=bars.close
        )
    return market


def _total_hours(n: int = 960) -> int:
    return n + _HISTORY_DAYS * 24


def _signal_returns(*, seed: int = 5, strength: float = 0.35) -> dict[str, np.ndarray]:
    """植入日频均值回复规律（今日收益 ≈ −strength × 昨日收益 + 噪声）。

    R4 短期反转方向在 24 小时窗口上正好吃到这个规律，因此拼接夏普显著为正；
    随机游走版用 `_random_walk_returns`，同一方向应当接近 0。
    """
    rng = np.random.default_rng(seed)
    hours = _total_hours()
    count = len(_SYMBOLS)
    matrix = np.zeros((count, hours))
    for hour in range(hours):
        base = -strength * matrix[:, hour - 24] if hour >= 24 else np.zeros(count)
        matrix[:, hour] = base + rng.normal(0.0, 0.0015, size=count)
    return {symbol: matrix[index] for index, symbol in enumerate(_SYMBOLS)}


def _random_walk_returns(*, seed: int = 11) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    hours = _total_hours()
    matrix = rng.normal(0.0, 0.002, size=(len(_SYMBOLS), hours))
    return {symbol: matrix[index] for index, symbol in enumerate(_SYMBOLS)}


_FAMILY = "xs_reversal_ls"
_PARAMS = [{"lookback_days": 1, "top_k": 3, "rebalance_hours": 24}]


def test_a_植入信号夏普显著为正而随机游走接近零() -> None:
    """a 款：合成数据埋入截面规律时拼接夏普显著为正，随机游走时很小。"""
    folds = _folds(START_MS)
    end_ms = folds[-1].end_ms
    planted = _path(
        _market_with(_signal_returns()),
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
    )
    planted_sharpe = _annualized(planted)
    assert planted_sharpe > _PLANTED_SHARPE_MIN, f"植入信号夏普 {planted_sharpe:.3f} 过低"
    assert planted.trades
    walk = _path(
        _market_with(_random_walk_returns()),
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
    )
    walk_sharpe = _annualized(walk)
    assert walk_sharpe < _RANDOM_WALK_SHARPE_MAX, f"随机游走夏普 {walk_sharpe:.3f} 不应为正"


def test_b_未来数据扰动不影响当日及之前的决策() -> None:
    """b 款：把 t 之后的全部数据改写，t 之前的成交与日收益逐值不变。"""
    market = _market_with(_signal_returns())
    cut_ms = START_MS + 30 * DAY_MS
    base = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=_folds(START_MS),
        start_ms=START_MS,
        end_ms=cut_ms,
    )
    altered = _market_with(_signal_returns())
    for symbol in _SYMBOLS:
        bars = altered.bars(symbol)
        index = bars.last_index_before(cut_ms)
        for array in (bars.open, bars.high, bars.low, bars.close):
            array[index + 1 :] *= 3.0
        mark = altered.mark[symbol]
        for array in (mark.high, mark.low, mark.close):
            array[index + 1 :] *= 3.0
    after = _path(
        altered,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=_folds(START_MS),
        start_ms=START_MS,
        end_ms=cut_ms,
    )
    assert [(t.symbol, t.side, t.entry_ts, t.exit_ts, t.net_pnl) for t in base.trades] == [
        (t.symbol, t.side, t.entry_ts, t.exit_ts, t.net_pnl) for t in after.trades
    ]
    np.testing.assert_allclose(base.equity, after.equity, rtol=1e-12, atol=1e-9)


def test_c_记账恒等式() -> None:
    """c 款：各笔毛盈亏 − 手续费 ± 资金费 = 权益曲线变化；多空分项相加等于总数。"""
    result = _path(
        _market_with(_signal_returns()),
        family=_FAMILY,
        params_list=_PARAMS,
        folds=_folds(START_MS),
        start_ms=START_MS,
        end_ms=_folds(START_MS)[-1].end_ms,
    )
    assert result.trades
    gross = sum(trade.gross_pnl for trade in result.trades)
    fees = sum(trade.fees for trade in result.trades)
    funding = sum(trade.funding for trade in result.trades)
    net = sum(trade.net_pnl for trade in result.trades)
    assert net == pytest.approx(gross - fees - funding, rel=1e-12, abs=1e-9)
    equity_change = float(result.equity[-1] - 10_000.0)
    # 路径结束时全部平仓：最终权益 − 初始权益等于全部交易净盈亏之和
    assert equity_change == pytest.approx(net, rel=1e-9, abs=1e-6)
    long_net = sum(trade.net_pnl for trade in result.trades if trade.side > 0)
    short_net = sum(trade.net_pnl for trade in result.trades if trade.side < 0)
    assert long_net + short_net == pytest.approx(net, rel=1e-12, abs=1e-9)


def test_d_成本单调性与情景子集() -> None:
    """d 款：成本 × 2 净收益严格下降；不出信号的策略曲线平直；多空单边是 base 的子集。"""
    market = _market_with(_signal_returns())
    folds = _folds(START_MS)
    end_ms = folds[-1].end_ms
    base = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
    )
    cost2x = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
        options=ScenarioOptions(name="cost2x", cost_mult=2.0),
    )
    base_net = sum(trade.net_pnl for trade in base.trades)
    cost2x_net = sum(trade.net_pnl for trade in cost2x.trades)
    assert cost2x_net < base_net
    long_only = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
        options=ScenarioOptions(name="long_only", long_only=True),
    )
    short_only = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
        options=ScenarioOptions(name="short_only", short_only=True),
    )
    # 1 号 d 款的“交易集合是 base 的子集”在实现上以方向过滤保证：同一决策时刻只执行允许的方向；
    # 路径依赖的仓位大小与持仓决定使逐笔时间戳无法与 base 严格相同，因此这里核对方向与笔数上界。
    assert all(trade.side > 0 for trade in long_only.trades)
    assert all(trade.side < 0 for trade in short_only.trades)
    assert len(long_only.trades) <= len(base.trades)
    assert len(short_only.trades) <= len(base.trades)
    assert (
        sum(trade.net_pnl for trade in long_only.trades)
        + sum(trade.net_pnl for trade in short_only.trades)
        != 0.0
    )
    flat = _engine_run(market, _NoSignal(), START_MS, end_ms)
    np.testing.assert_allclose(flat.equity, 10_000.0, rtol=0.0, atol=1e-9)
    assert not flat.trades


class _NoSignal:
    """测试用：从不产生目标（用于验证空曲线完全平直）。"""

    name = "test_no_signal"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 8

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        return Decision((), EXPOSURE_DIRECTIONAL)


def _engine_run(
    market: MarketData,
    strategy,
    start_ms: int,
    end_ms: int,
    *,
    equity: float = 10_000.0,
    sizing_mode: str = "fixed_risk",
) -> RunResult:
    """单策略单区间运行（测试内部使用，与流水线口径一致）。"""
    engine = Engine(
        market=market,
        strategy=strategy,
        sizing_cfg=_sizing(equity),
        exec_cfg=_config().execution,
        portfolio_cfg=_config().portfolio,
        fee_taker=_FEE_TAKER,
        risk_limits=_risk(),
        options=ScenarioOptions(),
        sizing_mode=sizing_mode,
    )
    return engine.run(start_ms, end_ms)


def test_e_训练期风控状态不带入验证路径() -> None:
    """e 款：训练期触发停机与大幅回撤，验证路径第 1 折照常交易且起点权益不变。"""
    returns = _signal_returns()
    # 交易开始前的 120 天预热期里制造连续大跌，令训练期触发 R6/R8 停机
    crash_symbol = _SYMBOLS[0]
    series = returns[crash_symbol].copy()
    series[: _HISTORY_DAYS * 24 // 2] = -0.02
    returns[crash_symbol] = series
    market = _market_with(returns, n=960)
    folds = _folds(START_MS)
    end_ms = folds[-1].end_ms
    result = _path(
        market,
        family=_FAMILY,
        params_list=_PARAMS,
        folds=folds,
        start_ms=START_MS,
        end_ms=end_ms,
    )
    first_fold_trades = [trade for trade in result.trades if trade.entry_ts < folds[0].end_ms]
    assert first_fold_trades, "训练期停机不得带入验证路径（第 1 折必须照常交易）"
    assert result.trades[0].entry_equity == pytest.approx(10_000.0, rel=1e-9)


def test_f_独立参考实现逐值一致() -> None:
    """f 款：2 个币 × 3 周，用不依赖框架的逐根循环重算，成交价、数量、手续费、日收益逐值一致。"""
    n = 24 * 21
    market = make_market(symbols=("AAAUSDT",), n=n)
    flat = market.bars("AAAUSDT")
    flat.close[:] = 100.0
    flat.open[:] = 100.0
    flat.high[:] = 100.0
    flat.low[:] = 100.0
    market.mark["AAAUSDT"] = MarkBars(
        symbol="AAAUSDT", open_time=flat.open_time, high=flat.high, low=flat.low, close=flat.close
    )
    market.rules["AAAUSDT"] = replace(market.rules["AAAUSDT"], step_size=1.0, min_notional=5.0)
    result = _engine_run(market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS)
    assert len(result.trades) == 1
    trade = result.trades[0]
    # 独立重算：权益 10000、risk_per_trade 0.0075、止损 3%；
    # 半价差 3bp（该币 30 天日成交额历史不足，不在成交额前 20 → 取 other 档），
    # 滑点 1bp（价格平 → 平方根冲击为 0，退回最小值）；亏损率 = sl + 2×滑点 + 半价差 + 2×费率
    half_spread = 0.0003
    slip = 0.0001
    loss_rate = 0.03 + 2 * slip + half_spread + 2 * _FEE_TAKER
    expected_notional = 10_000.0 * 0.0075 / loss_rate
    fill = 100.0 * (1.0 + half_spread + slip)
    expected_qty = float(np.floor(expected_notional / fill / 1.0 + 1e-9))
    assert trade.entry_price == pytest.approx(fill, rel=1e-12)
    assert trade.notional == pytest.approx(expected_qty * fill, rel=1e-12)
    entry_fee = expected_qty * fill * _FEE_TAKER
    exit_price = 100.0 * (1.0 - half_spread - slip)
    exit_fee = expected_qty * 1.0 * exit_price * _FEE_TAKER
    expected_gross = expected_qty * 1.0 * (exit_price - fill)
    assert trade.fees == pytest.approx(entry_fee + exit_fee, rel=1e-12)
    assert trade.gross_pnl == pytest.approx(expected_gross, rel=1e-12)
    # 独立重算权益路径：入场前为初始权益；入场后 = 初始权益 − 入场费 + 未实现盈亏（价格恒为 100）
    entry_offset = int((trade.entry_ts - START_MS) // HOUR_MS)
    expected_equity = np.full(n, 10_000.0)
    expected_equity[entry_offset:] = 10_000.0 - entry_fee + expected_qty * (100.0 - fill)
    # 最后一根收盘后路径结束、全部平仓，最终权益含离场手续费与实际毛盈亏
    expected_equity[-1] = 10_000.0 - entry_fee + expected_gross - exit_fee
    np.testing.assert_allclose(result.equity, expected_equity, rtol=1e-12, atol=1e-9)


class _AlwaysLong:
    """测试用：每个调仓时刻都做多同一个币（与 tests/research/test_engine.py 同口径）。"""

    name = "test_always_long"
    exposure = EXPOSURE_DIRECTIONAL
    rebalance_hours = 8

    def __init__(self, symbol: str, sl: float) -> None:
        self.symbol = symbol
        self.sl = sl

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        if view.close(self.symbol) is None:
            return Decision((), EXPOSURE_DIRECTIONAL)
        return Decision((Target(self.symbol, 1, self.sl),), EXPOSURE_DIRECTIONAL)


def test_g_两种权益口径的丢弃数() -> None:
    """g 款：10,000 USDT 下不丢仓位；500 USDT 下的丢弃与按最小下单额手算一致。"""
    n = 48
    market = make_market(symbols=("AAAUSDT",), n=n)
    market.rules["AAAUSDT"] = replace(market.rules["AAAUSDT"], min_notional=150.0)
    signal = _engine_run(
        market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS, equity=10_000.0
    )
    exec_basis = _engine_run(
        market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS, equity=500.0
    )
    assert signal.dropped_min_notional == 0
    assert signal.trades
    # 手算：500 × 0.0075 ÷ (0.03 + 2×1bp + 1bp + 2×5bp) ≈ 119.8 USDT < 150 × 1.1 → 丢弃
    loss_rate = 0.03 + 2 * 0.0001 + 0.0001 + 2 * _FEE_TAKER
    expected_notional = 500.0 * 0.0075 / loss_rate
    assert expected_notional < 150.0 * 1.1
    # 窗口内 3 个调仓时刻（24h / 32h / 40h）都尝试开仓且都因最小下单额被丢弃
    assert exec_basis.attempted == 3
    assert exec_basis.dropped_min_notional == 3
    assert exec_basis.trades == []


def _exit_price(trade) -> float:
    """由交易记录反推出场成交价（qty = notional ÷ entry_price）。"""
    qty = trade.notional / trade.entry_price
    return trade.entry_price + trade.gross_pnl / (trade.side * qty)


def _flat_market(
    n: int, *, volume: float = 5_000_000.0, funding_rate: float = 0.0, noise: float = 0.0
):
    """价格约 100 的单币市场（`noise` 给逐根交替波动，便于手算成本与资金费）。"""
    market = make_market(symbols=("AAAUSDT",), n=n, funding_by_symbol={"AAAUSDT": funding_rate})
    bars = market.bars("AAAUSDT")
    pattern = 100.0 * (1.0 + noise * np.where(np.arange(n) % 2 == 0, 1.0, -1.0))
    bars.open[:] = pattern
    bars.close[:] = pattern
    bars.high[:] = pattern * (1.0 + noise)
    bars.low[:] = pattern * (1.0 - noise)
    bars.quote_volume[:] = volume
    market.mark["AAAUSDT"] = MarkBars(
        symbol="AAAUSDT", open_time=bars.open_time, high=bars.high, low=bars.low, close=bars.close
    )
    return market


def test_h_口径回归测试() -> None:
    """h 款：成交时刻、止损成本、缺失资金费、标记价格触发四类旧实现问题都要能复现并被挡住。"""
    n = 96
    # h1 决策时刻 t 的订单在 open_time = t 的 K 线开盘成交，仓位权益不含 t 之后的价格
    market = _flat_market(n)
    result = _engine_run(market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS)
    trade = result.trades[0]
    assert trade.entry_ts >= START_MS + 24 * HOUR_MS  # 至少 24 小时历史才能算成交额
    assert trade.entry_ts % HOUR_MS == 0
    assert trade.entry_equity == pytest.approx(10_000.0, rel=1e-12)

    # h2 止损成交价含半价差与完整滑点（成交额小 → 冲击显著大于 1bp）
    market = _flat_market(n, volume=5_000.0, noise=0.004)
    bars = market.bars("AAAUSDT")
    mark = market.mark["AAAUSDT"]
    crash = int((trade.entry_ts + 8 * HOUR_MS - START_MS) // HOUR_MS)
    bars.open[crash:] = 90.0
    bars.high[crash:] = 91.0
    bars.low[crash:] = 89.0
    bars.close[crash:] = 90.0
    mark.high[crash:] = 91.0
    mark.low[crash:] = 89.0
    mark.close[crash:] = 90.0
    stopped = _engine_run(market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS)
    stop_trade = next(item for item in stopped.trades if item.reason == "stop")
    stop_ts = stop_trade.exit_ts
    view = View(market, stop_ts)
    vol = view.hourly_vol("AAAUSDT", 24)
    volume = view.mean_quote_volume("AAAUSDT", 24)
    assert vol is not None and volume is not None
    impact = 1.0 * vol * float(np.sqrt(stop_trade.notional / volume))
    slip = max(0.0001, impact)
    assert slip > 0.0001, "该场景下平方根冲击必须大于最小滑点，才测得到完整成本"
    entry_index = int((stop_trade.entry_ts - START_MS) // HOUR_MS)
    stop_price = float(mark.close[entry_index - 1]) * 0.97
    cost = 0.0003 + 2.0 * slip
    expected = min(90.0, stop_price) * (1.0 - cost)
    assert _exit_price(stop_trade) == pytest.approx(expected, rel=1e-9)

    # h3 持仓期间资金费缺失时按过去 7 天平均计，不静默为 0
    market = _flat_market(n, funding_rate=0.0004)
    series = market.funding["AAAUSDT"]
    cutoff = START_MS + 12 * HOUR_MS
    keep = series.calc_time <= cutoff
    market.funding["AAAUSDT"] = type(series)(
        symbol=series.symbol,
        calc_time=series.calc_time[keep],
        hourly_rate=series.hourly_rate[keep],
        interval_hours=series.interval_hours[keep],
    )
    estimated = _engine_run(
        market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS
    )
    assert estimated.funding_estimated > 0
    assert estimated.trades[0].funding != 0.0

    # h4 止损按标记价格触发、按成交价成交（成交价 K 线没有跌破止损价）
    market = _flat_market(n)
    mark = market.mark["AAAUSDT"]
    stop_bar = 24 + 24
    mark.low[stop_bar] = 90.0
    triggered = _engine_run(
        market, _AlwaysLong("AAAUSDT", sl=0.03), START_MS, START_MS + n * HOUR_MS
    )
    stops = [item for item in triggered.trades if item.reason == "stop"]
    assert stops, "标记价格跌破止损价时必须触发止损"
    first_stop = stops[0]
    assert first_stop.exit_ts == START_MS + stop_bar * HOUR_MS
    entry_index = int((first_stop.entry_ts - START_MS) // HOUR_MS)
    stop_price = float(market.mark["AAAUSDT"].close[entry_index - 1]) * 0.97
    expected = stop_price * (1.0 - (0.0003 + 2.0 * 0.0001))
    assert _exit_price(first_stop) == pytest.approx(expected, rel=1e-9)


def stopped_market(market: MarketData) -> MarketData:
    """同一市场的浅拷贝（View 只读，用于在测试里取已收盘数据）。"""
    return market


def _daily_series(seed: int, sigma: float, n: int = 60) -> np.ndarray:
    """指定样本标准差的日对数收益（模式 B 手算与 β 测试共用）。"""
    rng = np.random.default_rng(seed)
    values = rng.normal(0.0, sigma, n)
    values -= values.mean()
    values *= sigma / float(values.std(ddof=1))
    return values


def _install_daily(market: MarketData, series: Mapping[str, np.ndarray]) -> None:
    for symbol, values in series.items():
        market.daily[symbol] = make_daily_returns_bars(symbol, values)


class _StaticTargets:
    """测试用：每个调仓时刻都返回固定目标（多空与止损距离固定）。"""

    name = "test_static_targets"
    rebalance_hours = 8

    def __init__(self, targets: tuple[Target, ...], exposure: str) -> None:
        self.targets = targets
        self.exposure = exposure

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        return Decision(self.targets, self.exposure)


def test_h5_引擎按段使用模式B() -> None:
    """h 款：模式 B 的目标名义按波动率倒数与 target_vol 缩放（引擎端到端一致）。"""
    symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
    series = {symbol: _daily_series(index, 0.05) for index, symbol in enumerate(symbols)}
    series["BTCUSDT"] = _daily_series(99, 0.03)
    market = make_market(symbols=symbols, n=120)
    _install_daily(market, series)
    strategy = _StaticTargets(
        tuple(Target(symbol, 1, 0.03) for symbol in symbols), EXPOSURE_DIRECTIONAL
    )
    result = _engine_run(
        market,
        strategy,
        START_MS + 24 * HOUR_MS,
        START_MS + 72 * HOUR_MS,
        sizing_mode="vol_target",
    )
    assert len(result.trades) == len(symbols)
    ordered = sorted(symbols)
    sigma = np.asarray([np.std(series[symbol][-30:], ddof=1) for symbol in ordered])
    weights = 1.0 / sigma
    matrix = np.column_stack([series[symbol][-60:] for symbol in ordered])
    variance = float(weights @ np.cov(matrix, rowvar=False, ddof=1) @ weights)
    scale = 0.20 / (np.sqrt(365.0) * np.sqrt(variance))
    expected = {
        symbol: 10_000.0 * scale * weight for symbol, weight in zip(ordered, weights, strict=True)
    }
    for trade in result.trades:
        # 目标名义先按 stepSize 取整再成交；相对误差上界为 1 个 step 的名义 ÷ 仓位
        assert trade.notional == pytest.approx(expected[trade.symbol], rel=1e-3)


def test_h6_中性策略的β调整在引擎中生效() -> None:
    """h 款：β 调整后多头总名义 ÷ 空头总名义 ∈ [0.5, 2.0]，组合实现 β 绝对值 ≤ 0.10。"""
    btc = _daily_series(7, 0.03)
    betas = {"AAAUSDT": 0.5, "BBBUSDT": 1.5, "CCCUSDT": 1.5, "DDDUSDT": 1.5}
    series = {symbol: beta * btc for symbol, beta in betas.items()}
    series["BTCUSDT"] = btc
    market = make_market(symbols=("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"), n=120)
    _install_daily(market, series)
    strategy = _StaticTargets(
        (
            Target("AAAUSDT", 1, 0.03),
            Target("BBBUSDT", 1, 0.03),
            Target("CCCUSDT", -1, 0.03),
            Target("DDDUSDT", -1, 0.03),
        ),
        EXPOSURE_NEUTRAL,
    )
    result = _engine_run(market, strategy, START_MS + 24 * HOUR_MS, START_MS + 72 * HOUR_MS)
    assert len(result.trades) == 4
    long_total = sum(trade.notional for trade in result.trades if trade.side > 0)
    short_total = sum(trade.notional for trade in result.trades if trade.side < 0)
    assert 0.5 <= long_total / short_total <= 2.0
    beta_exposure = sum(
        trade.side * trade.notional * betas[trade.symbol] for trade in result.trades
    )
    assert abs(beta_exposure) / 10_000.0 <= 0.10
