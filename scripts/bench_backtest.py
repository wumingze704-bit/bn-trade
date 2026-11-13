"""回测性能基准：分阶段计时 + 结果快照（后续优化的“标准答案”）。

规格：docs/06-回测引擎.md §3.4、§5；结果对拍：`scripts/bench_compare.py`（红线 15）。

本脚本只做两件事：给现有回测函数在外层计时、把结果原样落盘。**不修改任何计算逻辑**，
不复制交易规则（入场、出场、仓位、组合约束全部走 `trade.backtest.engine` 与
`trade.common.*` 的现有函数）；只跑调参期 M1~M12 的月份，不读测试段与封存集
（红线 2、3）。bars 加载起点固定为 `window_bounds` 的 `start_ms`（Wilder ATR 递推，
起点不同数值会变，红线 1）。

用法（仓库根目录）：
    python scripts/bench_backtest.py --coins 5 --months 2025-07:2025-07 --suite full \
        --out artifacts/bench/baseline_5x1
    python scripts/bench_backtest.py --coins 2 --months 2025-07:2025-07 --suite base --profile
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import platform
import pstats
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from types import ModuleType
from typing import Any, get_type_hints

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from trade.backtest import engine as engine_module
from trade.backtest.costs import FeeSchedule, Scenario, scenario
from trade.backtest.engine import (
    PRECOMPUTE_BAR_COLUMNS,
    BacktestConfig,
    ExecutedTrade,
    PortfolioResult,
    PreparedSymbol,
    SkippedSignal,
    TradeRule,
    prepare_symbols,
    run_backtest,
)
from trade.backtest.metrics import compute_metrics
from trade.backtest.r0 import TrainConfig, r0_signals_for_symbol
from trade.backtest.sanity import (
    compute_random_entry_candidates,
    random_entry_baseline,
    s4_threshold_perturb,
)
from trade.backtest.sweep import sizing_sweep
from trade.common.config import BaseConfig, load_config
from trade.common.costs import breakeven_slip
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.data.loader import (
    collect_universe_coins,
    load_bars,
    load_universe_membership,
    new_entry_symbols,
    rebalance_ms_of,
    universe_months,
    universe_trade_mask,
    window_bounds,
)
from trade.data.universe import SnapshotCoin, load_universe_config
from trade.features.registry import FeaturesConfig

# 盈亏平衡滑点二分参数：与 `scripts/backtest.py` 正式回测一致（06 §4.3）
_BREAKEVEN_HI = 0.01
_BREAKEVEN_ITERS = 10

# 计时挂钩的引擎函数名（06 §3.4：预计算与组合模拟两步，组合模拟内再拆两项）
_HOOKS = ("precompute_trades_all", "_close_arrays", "_mark_to_market_curve")


def _bench_columns() -> tuple[str, ...]:
    """bars 只需读这些列：预计算用列 + R0 信号要的 taker_buy_quote_volume（03 §5）。"""
    return (*PRECOMPUTE_BAR_COLUMNS, "taker_buy_quote_volume")


class Timing:
    """分阶段耗时收集器：每阶段墙钟 + 引擎函数累计耗时（06 §3.4）。"""

    def __init__(self) -> None:
        self.phases: dict[str, dict[str, float]] = {}
        self._current = "startup"

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """记录一个阶段的墙钟耗时；阶段内引擎挂钩的计时归到该阶段。"""
        previous = self._current
        self._current = name
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, "wall_s", time.perf_counter() - started)
            self._current = previous

    def add(self, phase: str, key: str, value: float) -> None:
        """把 value 累加到 phase 的 key。"""
        bucket = self.phases.setdefault(phase, {})
        bucket[key] = bucket.get(key, 0.0) + value

    def add_stage(self, stage: str, seconds: float) -> None:
        """记录当前阶段内某个引擎函数的耗时与调用次数。"""
        self.add(self._current, f"{stage}_s", seconds)
        self.add(self._current, f"{stage}_calls", 1.0)

    def count(self, key: str, value: float) -> None:
        """把统计量（如交易笔数）写到当前阶段。"""
        self.phases.setdefault(self._current, {})[key] = value

    def total_s(self) -> float:
        """所有阶段墙钟之和（秒）。"""
        return float(sum(bucket.get("wall_s", 0.0) for bucket in self.phases.values()))


@contextmanager
def _instrument(engine: ModuleType, sink: Callable[[str, float], None]) -> Iterator[None]:
    """给引擎内部函数套计时挂钩（返回值原样透传，不改变任何计算）。

    `run_backtest` 与 `simulate_portfolio` 通过模块全局名调用这些函数，因此替换模块
    属性即可在不改动 `src/` 的前提下分项计时。
    """

    def build(name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                sink(name, time.perf_counter() - started)

        return wrapper

    originals = {name: getattr(engine, name) for name in _HOOKS}
    try:
        for name, func in originals.items():
            setattr(engine, name, build(name.lstrip("_"), func))
        yield
    finally:
        for name, func in originals.items():
            setattr(engine, name, func)


@dataclass
class BenchContext:
    """一次基准运行的全部输入（配置、行情、信号、规则）。"""

    base: BaseConfig
    features: FeaturesConfig
    backtest_cfg: BacktestConfig
    exit_cfg: ExitConfig
    sizing_cfg: SizingConfig
    reference_sizing: SizingConfig
    fees: FeeSchedule
    coins: list[SnapshotCoin]
    months: list[str]
    start_ms: int
    end_ms: int
    bars_by_symbol: dict[str, pd.DataFrame]
    prepared: dict[str, PreparedSymbol]
    trade_masks: dict[str, np.ndarray]
    signals: pd.DataFrame
    rules: dict[str, TradeRule]
    groups: dict[str, str]
    new_entry_aliases: set[str]

    @property
    def normal_scenarios(self) -> list[str]:
        """`base`/`stress`/`hell`（06 §4.3）；breakeven 由二分单独求。"""
        return [name for name in self.backtest_cfg.scenarios if name != "breakeven"]

    @property
    def runs(self) -> dict[str, tuple[SizingConfig, float]]:
        """两次运行：reference（06 §5.1）与 user（06 §5.4）。"""
        return {
            "reference": (self.reference_sizing, self.backtest_cfg.reference.initial_equity),
            "user": (self.sizing_cfg, self.sizing_cfg.initial_equity),
        }


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数（见模块 docstring 的用法）。"""
    parser = argparse.ArgumentParser(description="回测性能基准（只跑调参期 M1~M12）")
    parser.add_argument(
        "--coins", type=int, required=True, help="调参期快照按 alias 排序的前 N 个币"
    )
    parser.add_argument(
        "--months", type=str, required=True, help="调参期内的月份范围，如 2025-07:2025-09"
    )
    parser.add_argument("--signals", choices=("r0",), default="r0", help="信号来源（当前仅 r0）")
    parser.add_argument("--suite", choices=("base", "full"), default="base")
    parser.add_argument(
        "--out", type=Path, default=None, help="输出目录（缺省 artifacts/bench/...）"
    )
    parser.add_argument("--profile", action="store_true", help="cProfile 前 30 写入 profile.txt")
    parser.add_argument("--overwrite", action="store_true", help="允许写入非空输出目录")
    parser.add_argument("--base", type=Path, default=Path("configs/base.yaml"))
    parser.add_argument("--backtest", type=Path, default=Path("configs/backtest.yaml"))
    parser.add_argument("--exits", type=Path, default=Path("configs/exits.yaml"))
    parser.add_argument("--sizing", type=Path, default=Path("configs/sizing.yaml"))
    parser.add_argument("--features", type=Path, default=Path("configs/features.yaml"))
    parser.add_argument("--train", type=Path, default=Path("configs/train.yaml"))
    parser.add_argument("--universe", type=Path, default=Path("configs/universe.yaml"))
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path("artifacts/exchange_info/rules.json"),
        help="交易规则快照",
    )
    return parser


def _resolve_months(spec: str, all_months: list[str], tuning: list[str]) -> list[str]:
    """把 `2025-07:2025-09` 展开为月份列表，并校验全部落在调参期。

    Args:
        spec: `YYYY-MM` 或 `YYYY-MM:YYYY-MM`（含两端）。
        all_months: 数据窗口内的全部月份（`universe_months`）。
        tuning: 调参期月份（M1~M12）。

    Returns:
        连续月份的字符串列表。

    Raises:
        ValueError: 月份不在数据窗口内、起止顺序颠倒或落在调参期之外（红线 2、3）。
    """
    start, sep, end = spec.partition(":")
    end = end if sep else start
    for month in (start, end):
        if month not in all_months:
            raise ValueError(f"--months 的 {month} 不在数据窗口 {all_months[0]}~{all_months[-1]}")
    first, last = all_months.index(start), all_months.index(end)
    if last < first:
        raise ValueError(f"--months 起止顺序颠倒：{spec}")
    selected = all_months[first : last + 1]
    outside = [month for month in selected if month not in tuning]
    if outside:
        raise ValueError(
            f"--months 含非调参期月份 {outside}（只允许 {tuning[0]}~{tuning[-1]}，红线 2、3）"
        )
    return selected


def _select_coins(data_root: Path, tuning: list[str], count: int) -> list[SnapshotCoin]:
    """取调参期快照里按 alias 排序的前 N 个币（确定性；不改选币规则）。"""
    available = sorted(collect_universe_coins(data_root, tuning), key=lambda coin: coin.alias)
    if count < 1:
        raise ValueError(f"--coins 必须 ≥ 1，收到 {count}")
    if count > len(available):
        raise ValueError(f"--coins={count} 超过调参期可选币数 {len(available)}")
    return available[:count]


def _rules_by_alias(coins: list[SnapshotCoin], rules_path: Path) -> dict[str, TradeRule]:
    """读 rules.json（键为 symbol）并转成引擎使用的 alias -> TradeRule。

    Raises:
        FileNotFoundError: 规则快照不存在。
        ValueError: 快照缺少该币或缺少必需字段（红线 13，不回退默认值）。
    """
    if not rules_path.is_file():
        raise FileNotFoundError(f"未找到交易规则快照：{rules_path}")
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    rules: dict[str, TradeRule] = {}
    for coin in coins:
        entry = raw.get(coin.symbol)
        if entry is None:
            raise ValueError(f"rules.json 缺少 {coin.symbol}（alias {coin.alias}）：{rules_path}")
        missing = [key for key in ("tick_size", "step_size", "min_notional") if key not in entry]
        if missing:
            raise ValueError(f"rules.json 的 {coin.symbol} 缺字段 {missing}：{rules_path}")
        rules[coin.alias] = TradeRule(
            tick_size=float(entry["tick_size"]),
            step_size=float(entry["step_size"]),
            min_notional=float(entry["min_notional"]),
        )
    return rules


def _load_data(args: argparse.Namespace, timing: Timing) -> BenchContext:
    """加载配置、行情、交易范围掩码与 R0 信号（06 §2）。

    Args:
        args: 命令行参数。
        timing: 耗时收集器（bars、掩码、信号分阶段记录）。

    Returns:
        BenchContext，含两次运行所需的全部输入。

    Raises:
        ValueError: 月份越界、币数不合法或请求与封存集重叠（红线 2、3）。
    """
    base = load_config(BaseConfig, args.base)
    features = load_config(FeaturesConfig, args.features)
    backtest_cfg = load_config(BacktestConfig, args.backtest)
    exit_cfg = load_config(ExitConfig, args.exits)
    sizing_cfg = load_config(SizingConfig, args.sizing)
    train_cfg = load_config(TrainConfig, args.train)
    universe_cfg = load_universe_config(args.universe)

    all_months = universe_months(base)
    tuning = all_months[: base.data_window.tuning_months]
    months = _resolve_months(args.months, all_months, tuning)
    last_index = all_months.index(months[-1])
    if last_index + 1 >= len(all_months):
        raise ValueError(f"--months 末月 {months[-1]} 是数据窗口最后一个月，无法确定终点")
    end_ms = rebalance_ms_of(all_months[last_index + 1])
    wb = window_bounds(base)
    if end_ms > wb.holdout_start_ms:
        raise ValueError(f"回测终点 {months[-1]} 越过封存集起点 {wb.holdout_start_ms}（红线 2）")

    coins = _select_coins(base.paths.data_root, tuning, args.coins)
    dev_months = all_months[: len(all_months) - base.data_window.holdout_months]
    membership = load_universe_membership(base.paths.data_root, dev_months)
    ne_symbols = new_entry_symbols(membership, base.data_window.tuning_months)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    trade_masks: dict[str, np.ndarray] = {}
    with timing.phase("load_bars"):
        for coin in coins:
            # 起点必须是 wb.start_ms：Wilder ATR 递推，缩短起点会改变数值（红线 1）
            bars = load_bars(
                base.paths.data_root,
                coin.alias,
                wb.start_ms,
                end_ms,
                holdout_start_ms=wb.holdout_start_ms,
                allow_holdout=False,
                columns=_bench_columns(),
            )
            bars_by_symbol[coin.alias] = bars
            trade_masks[coin.alias] = universe_trade_mask(
                bars["open_time"].to_numpy(dtype="int64"),
                coin.symbol,
                membership,
                universe_cfg.rule.new_entry_delay_days,
            )
        timing.count("n_coins", float(len(coins)))
        timing.count("n_bars_rows", float(sum(len(bars) for bars in bars_by_symbol.values())))

    with timing.phase("prepare_symbols"):
        prepared = prepare_symbols(bars_by_symbol)

    with timing.phase("r0_signals"):
        frames = [
            r0_signals_for_symbol(
                bars_by_symbol[coin.alias],
                train_cfg.r0,
                vol_z_60_thr=features.prefilter.vol_z_60,
                r1_sigma=features.prefilter.r1_sigma,
                brk_60_thr=features.prefilter.brk_60,
                warmup_bars=features.warmup_bars,
                symbol=coin.alias,
                trade_mask=trade_masks[coin.alias],
            )
            for coin in coins
        ]
        signals = pd.concat(frames, ignore_index=True).sort_values(["open_time", "symbol"])
        timing.count("n_signal_rows", float(len(signals)))
        timing.count("n_signals_p1", float((signals["p"] == 1).sum()))

    reference_sizing = sizing_cfg.model_copy(
        update={
            "initial_equity": backtest_cfg.reference.initial_equity,
            "mode": backtest_cfg.reference.mode,
            "risk_per_trade": backtest_cfg.reference.risk_per_trade,
            "base": backtest_cfg.reference.base,
        }
    )
    return BenchContext(
        base=base,
        features=features,
        backtest_cfg=backtest_cfg,
        exit_cfg=exit_cfg,
        sizing_cfg=sizing_cfg,
        reference_sizing=reference_sizing,
        fees=FeeSchedule(base.fees.maker, base.fees.taker, base.fees.rebate_rate),
        coins=coins,
        months=months,
        start_ms=wb.start_ms,
        end_ms=end_ms,
        bars_by_symbol=bars_by_symbol,
        prepared=prepared,
        trade_masks=trade_masks,
        signals=signals,
        rules=_rules_by_alias(coins, args.rules),
        groups={coin.alias: coin.role for coin in coins},
        new_entry_aliases={coin.alias for coin in coins if coin.symbol in ne_symbols},
    )


def _run_scenario(
    ctx: BenchContext,
    *,
    scen: str,
    sizing_cfg: SizingConfig,
    initial_equity: float,
    backtest_cfg: BacktestConfig | None = None,
    scenario_override: Scenario | None = None,
) -> PortfolioResult:
    """跑单个成本情景（参数映射与 `scripts/backtest.py::_run_scenario` 一致，06 §3.4）。"""
    result, _ = run_backtest(
        ctx.prepared,
        ctx.signals,
        ctx.rules,
        ctx.groups,
        scen_name=scen,
        fees=ctx.fees,
        backtest_cfg=ctx.backtest_cfg if backtest_cfg is None else backtest_cfg,
        exit_cfg=ctx.exit_cfg,
        sizing_cfg=sizing_cfg,
        k_sl=ctx.features.label.k_sl,
        sl_min=ctx.features.label.sl_min,
        sl_max=ctx.features.label.sl_max,
        H_label=ctx.features.label.H_label,
        initial_equity=initial_equity,
        trade_masks=ctx.trade_masks,
        new_entry_symbols=ctx.new_entry_aliases,
        scenario_override=scenario_override,
    )
    return result


def _run_normal_scenarios(
    ctx: BenchContext,
    timing: Timing,
    results: dict[tuple[str, str], PortfolioResult],
) -> None:
    """两次运行 × 三个成本情景（06 §4.3、§5.1）。"""
    for run_name, (sizing, equity) in ctx.runs.items():
        for scen in ctx.normal_scenarios:
            with timing.phase(f"{run_name}/{scen}"):
                result = _run_scenario(ctx, scen=scen, sizing_cfg=sizing, initial_equity=equity)
                timing.count("n_trades", float(len(result.trades)))
                timing.count("n_skipped", float(len(result.skipped)))
            results[(run_name, scen)] = result


def _run_breakeven(
    ctx: BenchContext,
    timing: Timing,
    results: dict[tuple[str, str], PortfolioResult],
) -> dict[str, float]:
    """两次运行的盈亏平衡滑点（06 §4.3）：二分搜索 + 用最终滑点再跑一次。"""
    breakeven: dict[str, float] = {}
    for run_name, (sizing, equity) in ctx.runs.items():

        def _net(slip: float, sizing: SizingConfig = sizing, equity: float = equity) -> float:
            result = _run_scenario(
                ctx,
                scen="base",
                sizing_cfg=sizing,
                initial_equity=equity,
                scenario_override=replace(scenario("base"), name="breakeven", fixed_slip=slip),
            )
            return float(sum(trade.net_pnl for trade in result.trades))

        with timing.phase(f"breakeven/{run_name}"):
            slip = breakeven_slip(net_return=_net, hi=_BREAKEVEN_HI, max_iter=_BREAKEVEN_ITERS)
            final = _run_scenario(
                ctx,
                scen="base",
                sizing_cfg=sizing,
                initial_equity=equity,
                scenario_override=replace(scenario("base"), name="breakeven", fixed_slip=slip),
            )
            timing.count("n_trades", float(len(final.trades)))
            timing.count("breakeven_slip", float(slip))
        results[(run_name, "breakeven")] = final
        breakeven[run_name] = float(slip)
    return breakeven


def _run_sweep(ctx: BenchContext, timing: Timing) -> pd.DataFrame:
    """仓位比例敏感性扫描（06 §5.5）：每个比例重跑完整组合模拟。"""
    with timing.phase("sizing_sweep"):
        frame = sizing_sweep(
            ctx.prepared,
            ctx.signals,
            ctx.rules,
            ctx.groups,
            fees=ctx.fees,
            backtest_cfg=ctx.backtest_cfg,
            exit_cfg=ctx.exit_cfg,
            sizing_cfg=ctx.sizing_cfg,
            features_cfg=ctx.features,
            fractions=list(ctx.backtest_cfg.sizing_sweep.position_frac),
            initial_equity=ctx.sizing_cfg.initial_equity,
            trade_masks=ctx.trade_masks,
        )
    return frame


def _run_s1(
    ctx: BenchContext, timing: Timing, reference: PortfolioResult
) -> tuple[dict[str, float], int]:
    """S1 随机入场基准（06 §7 S1）：候选逐笔净盈亏 + 固定种子抽样。"""
    with timing.phase("s1_candidates"):
        candidates = compute_random_entry_candidates(
            ctx.prepared,
            ctx.signals,
            ctx.rules,
            ctx.fees,
            ctx.backtest_cfg,
            ctx.exit_cfg,
            ctx.reference_sizing,
            ctx.features.label.k_sl,
            ctx.features.label.sl_min,
            ctx.features.label.sl_max,
            ctx.features.label.H_label,
        )
        timing.count("n_candidates", float(len(candidates)))
    with timing.phase("s1_baseline"):
        s1 = random_entry_baseline(
            reference,
            candidates,
            n_iters=ctx.backtest_cfg.sanity.random_iters,
            seed=ctx.backtest_cfg.sanity.random_seed,
        )
    return {key: float(value) for key, value in s1.items()}, len(candidates)


def _run_s4(ctx: BenchContext, timing: Timing) -> dict[str, float]:
    """S4 阈值扰动（06 §7 S4）：θ ± Δ 两侧的 base 情景 PF。"""
    pfs: dict[str, float] = {}
    with timing.phase("s4"):
        for direction, delta in (
            ("below", -ctx.backtest_cfg.sanity.threshold_perturb),
            ("above", ctx.backtest_cfg.sanity.threshold_perturb),
        ):
            perturbed = ctx.backtest_cfg.model_copy(
                update={"threshold": ctx.backtest_cfg.threshold + delta}
            )
            result = _run_scenario(
                ctx,
                scen="base",
                sizing_cfg=ctx.reference_sizing,
                initial_equity=ctx.backtest_cfg.reference.initial_equity,
                backtest_cfg=perturbed,
            )
            pfs[direction] = float(
                compute_metrics(result, ctx.backtest_cfg.reference.initial_equity)["profit_factor"]
            )
    pfs["passed"] = float(s4_threshold_perturb(pfs["below"], pfs["above"]))
    return pfs


def _empty_dtype(hint: object) -> str:
    """空结果帧的列 dtype（避免全空 DataFrame 写出 null 列后无法对拍）。"""
    if hint is int:
        return "int64"
    if hint is float:
        return "float64"
    return "object"


def _frame_for(item_type: type[Any], rows: list[dict[str, Any]]) -> pd.DataFrame:
    """dataclass 列表 -> 固定列顺序的 DataFrame；空列表时按字段类型建空帧。"""
    names = [spec.name for spec in fields(item_type)]
    frame = pd.DataFrame(rows, columns=names)
    if frame.empty:
        hints = get_type_hints(item_type)
        frame = frame.astype({name: _empty_dtype(hints[name]) for name in names})
    return frame


def _write_json(path: Path, payload: object) -> None:
    """写 UTF-8、键排序的 JSON（对拍可复现）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def _write_snapshots(
    out_dir: Path,
    ctx: BenchContext,
    results: dict[tuple[str, str], PortfolioResult],
    extras: dict[str, object],
) -> None:
    """把每个 (run, scenario) 的 trades/skipped/equity 与各项结果落盘。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for (run_name, scen), result in sorted(results.items()):
        stem = f"{run_name}_{scen}"
        _frame_for(ExecutedTrade, [asdict(trade) for trade in result.trades]).to_parquet(
            out_dir / f"trades_{stem}.parquet", index=False
        )
        _frame_for(SkippedSignal, [asdict(item) for item in result.skipped]).to_parquet(
            out_dir / f"skipped_{stem}.parquet", index=False
        )
        curve = result.equity_curve
        if curve.empty:
            curve = pd.DataFrame({"ts_ms": pd.Series(dtype="int64"), "equity": [], "drawdown": []})
        curve.to_parquet(out_dir / f"equity_{stem}.parquet", index=False)
        initial = (
            ctx.backtest_cfg.reference.initial_equity
            if run_name == "reference"
            else ctx.sizing_cfg.initial_equity
        )
        _write_json(out_dir / f"metrics_{stem}.json", compute_metrics(result, initial))
    for name, payload in extras.items():
        _write_json(out_dir / f"{name}.json", payload)


def _peak_memory() -> str:
    """进程峰值内存：优先 psutil，Windows 下用 ctypes 读 PeakWorkingSetSize，都没有记“未测”。"""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        psutil = None
    if psutil is not None:
        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", info.rss)
        return f"{peak / 1024**2:.1f} MB"
    if sys.platform == "win32":
        peak = _windows_peak_working_set()
        if peak is not None:
            return f"{peak / 1024**2:.1f} MB"
    return "未测（无 psutil，且当前平台不支持 ctypes 读取峰值工作集）"


def _windows_peak_working_set() -> int | None:
    """用 ctypes 读本进程峰值工作集（字节）；失败返回 None。"""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    return int(counters.PeakWorkingSetSize) if ok else None


def _print_timing(timing: Timing) -> None:
    """把分阶段耗时打印成表格（报告数字取自这里与 timing.json）。"""
    header = f"{'阶段':<22}{'wall_s':>10}{'precompute':>12}{'close_arrays':>14}{'mark_to_mkt':>13}"
    print(header)
    for name, bucket in timing.phases.items():
        print(
            f"{name:<22}{bucket.get('wall_s', 0.0):>10.2f}"
            f"{bucket.get('precompute_trades_all_s', 0.0):>12.2f}"
            f"{bucket.get('close_arrays_s', 0.0):>14.2f}"
            f"{bucket.get('mark_to_market_curve_s', 0.0):>13.2f}"
        )
    print(f"{'合计':<22}{timing.total_s():>10.2f}")


def main(argv: list[str] | None = None) -> int:
    """脚本入口：加载数据 -> 计时运行 -> 写快照与 timing.json。"""
    args = build_parser().parse_args(argv)
    n_months = args.months.count(":") + 1
    out_dir = args.out or Path(f"artifacts/bench/{args.coins}x{n_months}_{args.suite}")
    if out_dir.exists() and any(out_dir.iterdir()) and not args.overwrite:
        raise SystemExit(f"输出目录非空：{out_dir}（覆盖会丢结果，确认后加 --overwrite）")

    timing = Timing()
    profiler = cProfile.Profile()
    if args.profile:
        profiler.enable()

    with _instrument(engine_module, timing.add_stage):
        ctx = _load_data(args, timing)
        results: dict[tuple[str, str], PortfolioResult] = {}
        extras: dict[str, object] = {}
        _run_normal_scenarios(ctx, timing, results)
        if args.suite == "full":
            extras["breakeven"] = _run_breakeven(ctx, timing, results)
            extras["sweep"] = _run_sweep(ctx, timing).to_dict(orient="records")
            s1, n_candidates = _run_s1(ctx, timing, results[("reference", "base")])
            extras["s1"] = {**s1, "n_candidates": float(n_candidates)}
            extras["s4"] = _run_s4(ctx, timing)

    if args.profile:
        profiler.disable()

    with timing.phase("write_snapshots"):
        _write_snapshots(out_dir, ctx, results, extras)
        if args.profile:
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(30)
            (out_dir / "profile.txt").write_text(stream.getvalue(), encoding="utf-8")
    _write_json(
        out_dir / "timing.json",
        {
            "suite": args.suite,
            "coins": [coin.alias for coin in ctx.coins],
            "months": ctx.months,
            "start_ms": ctx.start_ms,
            "end_ms": ctx.end_ms,
            "phases": timing.phases,
            "total_s": timing.total_s(),
            "peak_memory": _peak_memory(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "command": " ".join(sys.argv),
        },
    )
    _write_json(
        out_dir / "meta.json",
        {
            "suite": args.suite,
            "coins": [
                {"alias": coin.alias, "symbol": coin.symbol, "role": coin.role}
                for coin in ctx.coins
            ],
            "months": ctx.months,
            "start_ms": ctx.start_ms,
            "end_ms": ctx.end_ms,
            "scenarios": ctx.normal_scenarios + (["breakeven"] if args.suite == "full" else []),
            "threshold": ctx.backtest_cfg.threshold,
            "initial_equity": {
                "reference": ctx.backtest_cfg.reference.initial_equity,
                "user": ctx.sizing_cfg.initial_equity,
            },
        },
    )

    _print_timing(timing)
    print(f"输出目录：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
