"""研究流水线：网格评估、试验登记与轮次报告（docs/13 §7、§9）。

- 每组参数：全期跑一次得到训练曲线（折内选参用），再从第 1 折起点连续跑一次得到
  “自身拼接曲线”（登记试验用）。
- 拼接曲线是一条连续路径：以初始权益、空仓、空风控状态从第 1 折起点开始，折边界换参数，
  路径内的风控事件照常延续（1 号第五部分 B 第 10 条 a~e）。
- 试验登记含 code_hash / data_hash / config_hash / equity_basis；逐组落盘并可断点续跑；
  脚本只写 EXPERIMENTS.log，不写 ledger（1 号第三部分第 5 条、B 第 13 条）。
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import yaml

from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.research.config import ResearchConfig
from trade.research.data import load_market_data
from trade.research.engine import Engine, RunResult, ScenarioOptions, Segment, Trade
from trade.research.interface import EXPOSURE_NEUTRAL
from trade.research.metrics import (
    annualized_sharpe,
    compound_monthly,
    longest_drawdown_days,
    summarize,
    worst_week,
)
from trade.research.rolling import (
    Fold,
    FoldChoice,
    ParamRun,
    fold_windows,
    select_for_fold,
    slice_returns,
    trades_in_fold,
)
from trade.strategies.registry import build_strategy

_CTX: dict[str, Any] = {}
# 可执行性检查的阈值（docs/13 §7 第 9 条）：丢弃仓位 ≤ 应开仓位的 25%、年化夏普 ≥ 信号口径的 60%
_EXEC_DROP_RATIO_MAX = 0.25
_EXEC_SHARPE_RATIO_MIN = 0.60
# 达标阈值（docs/13 §7 第 1、2、4、5、7、10、11 条）
_MIN_ANNUALIZED_SHARPE = 0.8
_MIN_POSITIVE_FOLDS = 3
_MIN_DSR = 0.50
_MAX_DELAY1_EXCESS = 0.30
_MAX_BETA_ABS = 0.30
_MIN_ALPHA_SHARPE = 0.6
_MIN_SHORT_EXCESS_SHARPE = 0.6
# X 轨与 R11、R12 的 Deflated Sharpe 主口径包含作废旧试验（docs/13 §7）
_VOID_IN_MAIN_ROUNDS = frozenset({"R11", "R12"})
# 需要额外数据源的方向（docs/13 §1、§10）：评估时自动加载，不依赖命令行开关
_FLOW_FAMILIES = frozenset({"short_taker_exhaustion"})
_PREMIUM_FAMILIES = frozenset({"short_premium_rich"})
# shuffle 不适用的方向（打分不决定选币与方向，docs/13 §7）
_SHUFFLE_NOT_APPLICABLE = frozenset(
    {"short_basket_baseline", "short_basket_btc_regime", "alt_short_btc_long"}
)
# 只做空的方向性策略：与 X0 的 N = 20 组比较超额夏普（docs/13 §7 第 11 条）
_SHORT_ONLY_FAMILIES = frozenset(
    {
        "short_seasoned_listing",
        "short_basket_btc_regime",
        "short_crowded_pump",
        "short_premium_rich",
        "short_taker_exhaustion",
    }
)


def params_key(params: Mapping[str, object]) -> str:
    """参数组的稳定键（JSON 排序序列化）。"""
    return json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_grid(path: Path) -> dict[str, Any]:
    """读取网格 YAML：`{round, family, params: [...]}`。"""
    values = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or "family" not in values or "params" not in values:
        raise ValueError(f"网格配置缺少 family / params：{path}")
    return values


def build_market(
    config: ResearchConfig,
    *,
    start_ms: int,
    end_ms: int,
    start_month: str,
    end_month: str,
    with_flow: bool = False,
    with_premium: bool = False,
) -> Any:
    """按研究配置加载数据（工作进程与主进程共用）。

    Args:
        config: 研究配置。
        start_ms: 起始毫秒（含）。
        end_ms: 结束毫秒（不含）。
        start_month: 起始月份。
        end_month: 结束月份。
        with_flow: 是否加载 `klines_1h_flow`。
        with_premium: 是否加载 `premium_1h`。

    Returns:
        MarketData（标记价格总是加载，止损触发需要）。
    """
    root = Path.cwd()
    return load_market_data(
        klines_1h=root / config.paths.klines_1h,
        klines_1d=root / config.paths.klines_1d,
        funding=root / config.paths.funding,
        universe_hist=root / config.paths.universe_hist,
        rules_path=root / config.paths.artifacts / "exchange_info" / "rules.json",
        start_ms=start_ms,
        end_ms=end_ms,
        start_month=start_month,
        end_month=end_month,
        mark_1h=root / config.paths.mark_1h,
        flow_1h=root / config.paths.klines_1h_flow if with_flow else None,
        premium_1h=root / config.paths.premium_1h if with_premium else None,
    )


def run_param(
    market,
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    family: str,
    params: Mapping[str, object],
    start_ms: int,
    end_ms: int,
    options: ScenarioOptions | None = None,
    components: Mapping[str, Mapping[str, object]] | None = None,
) -> ParamRun:
    """跑一组参数（单策略连续区间），返回日收益、交易与统计。"""
    strategy = build_strategy(
        family, dict(params), stop_max=config.execution.stop_max, components=components
    )
    sizing_mode = str(params.get("sizing_mode", "fixed_risk"))
    engine = Engine(
        market=market,
        strategy=strategy,
        sizing_cfg=sizing,
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        options=options or ScenarioOptions(),
        seed=config.bootstrap.seed,
        sizing_mode=sizing_mode,
    )
    return _to_param_run(params, engine.run(start_ms, end_ms))


def _to_param_run(params: Mapping[str, object], result: RunResult) -> ParamRun:
    return ParamRun(
        key=params_key(params),
        params=dict(params),
        daily=result.daily_returns(),
        trades=result.trades,
        turnover=result.turnover,
        dropped=result.dropped,
        forced=result.forced_liquidations,
        cost_total=result.cost_total,
        funding_total=result.funding_total,
        long_gross=result.long_gross,
        short_gross=result.short_gross,
        risk_events=len(result.risk_events),
        net_exposure_p95=float(
            np.quantile(np.abs(np.asarray(result.net_exposure, dtype="float64")), 0.95)
        )
        if result.net_exposure
        else 0.0,
        params_meta={
            "daily_sharpe": result.daily_sharpe(),
            "attempted": float(result.attempted),
            "dropped_min_notional": float(result.dropped_min_notional),
            "funding_estimated": float(result.funding_estimated),
            "funding_missing": float(result.funding_missing),
            "mark_fallback": float(result.mark_fallback),
        },
    )


def _worker_init(payload: dict[str, Any]) -> None:
    """工作进程初始化：各自加载数据，避免跨进程序列化几百 MB 数组。"""
    config = ResearchConfig.model_validate(payload["config"])
    _CTX["config"] = config
    _CTX["sizing"] = SizingConfig.model_validate(payload["sizing"])
    _CTX["fee_taker"] = float(payload["fee_taker"])
    _CTX["risk"] = RiskLimits(**payload["risk"])
    _CTX["start_ms"] = int(payload["start_ms"])
    _CTX["end_ms"] = int(payload["end_ms"])
    _CTX["market"] = build_market(
        config,
        start_ms=int(payload["start_ms"]),
        end_ms=int(payload["end_ms"]),
        start_month=str(payload["start_month"]),
        end_month=str(payload["end_month"]),
        # 工作进程必须与主进程加载同一批数据源（X3 用溢价指数、X4 用主动买入成交）
        with_flow=bool(payload.get("with_flow", False)),
        with_premium=bool(payload.get("with_premium", False)),
    )


def _worker_task(task: dict[str, Any]) -> ParamRun:
    return _execute_task(_CTX["market"], _CTX, task)


def _execute_task(market, ctx: Mapping[str, Any], task: Mapping[str, Any]) -> ParamRun:
    """执行一个任务：普通参数运行，或 R5 的按折成分参数拼接路径。"""
    config = ctx["config"]
    if isinstance(config, Mapping):
        config = ResearchConfig.model_validate(config)
    sizing = ctx["sizing"]
    if isinstance(sizing, Mapping):
        sizing = SizingConfig.model_validate(sizing)
    risk = ctx["risk"]
    if isinstance(risk, Mapping):
        risk = RiskLimits(**risk)
    if task.get("kind") == "path":
        return run_path_param(
            market,
            config=config,
            sizing=sizing,
            fee_taker=float(ctx["fee_taker"]),
            risk_limits=risk,
            family=str(task["family"]),
            params=task["params"],
            folds=task["folds"],
            components_by_fold=task.get("components_by_fold"),
            components_by_mode=task.get("components_by_mode"),
            end_ms=int(task["end_ms"]),
        )
    return run_param(
        market,
        config=config,
        sizing=sizing,
        fee_taker=float(ctx["fee_taker"]),
        risk_limits=risk,
        family=str(task["family"]),
        params=task["params"],
        start_ms=int(task.get("start_ms", ctx["start_ms"])),
        end_ms=int(task.get("end_ms", ctx["end_ms"])),
        options=ScenarioOptions(**task["options"]),
        components=task.get("components"),
    )


SCENARIO_OPTIONS: dict[str, ScenarioOptions] = {
    "base": ScenarioOptions(),
    "cost2x": ScenarioOptions(name="cost2x", cost_mult=2.0),
    "nohalt": ScenarioOptions(name="nohalt", no_halt=True),
    "delay1": ScenarioOptions(name="delay1", delay_rebalances=1),
    "shuffle": ScenarioOptions(name="shuffle", shuffle=True),
    "long_only": ScenarioOptions(name="long_only", long_only=True),
    "short_only": ScenarioOptions(name="short_only", short_only=True),
}


def _run_tasks(
    tasks: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    workers: int,
    market,
) -> list[ParamRun]:
    """并行（或串行）执行参数任务。"""
    if not tasks:
        return []
    if workers <= 1:
        ctx = {
            "config": ResearchConfig.model_validate(payload["config"]),
            "sizing": SizingConfig.model_validate(payload["sizing"]),
            "fee_taker": float(payload["fee_taker"]),
            "risk": RiskLimits(**payload["risk"]),
            "start_ms": payload["start_ms"],
            "end_ms": payload["end_ms"],
        }
        return [_execute_task(market, ctx, task) for task in tasks]
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_worker_init, initargs=(payload,)
    ) as pool:
        return list(pool.map(_worker_task, tasks))


@dataclass(frozen=True)
class RecordHashes:
    """试验登记用的三个哈希（1 号第三部分第 5 条）。"""

    code_hash: str
    data_hash: str
    config_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code_hash": self.code_hash,
            "data_hash": self.data_hash,
            "config_hash": self.config_hash,
        }


def _hash_files(paths: Sequence[Path]) -> str:
    """按路径排序后逐文件累加 SHA256（路径 + 内容）。"""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).replace("\\", "/").encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _module_file(root: Path, module: str) -> Path | None:
    """把 `trade.x.y` 映射到仓库内的模块文件（不存在返回 None）。"""
    parts = module.split(".")
    if not parts or parts[0] != "trade":
        return None
    base = root / "src" / "trade"
    for part in parts[1:]:
        base = base / part
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _code_closure(root: Path, entries: Sequence[Path]) -> list[Path]:
    """代码闭包：入口文件 + 经 ast 静态解析递归导入的本仓库模块（docs/13 §8）。

    `src/trade/strategies/registry.py` 与其他方向的策略模块不计入（registry 只按名字分派，
    新增方向只追加条目）；R5 的成分模块由调用方作为入口传入。
    """
    registry = root / "src" / "trade" / "strategies" / "registry.py"
    seen: set[Path] = set()
    queue: list[Path] = [path for path in entries if path.is_file()]
    while queue:
        path = queue.pop()
        if path in seen or path == registry:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                target = _module_file(root, module)
                if target is not None and target not in seen:
                    queue.append(target)
    return sorted(seen)


def compute_hashes(
    *,
    config: ResearchConfig,
    root: Path,
    grid_path: Path | None,
    extra_configs: Sequence[Path] = (),
    family: str | None = None,
    component_families: Sequence[str] = (),
) -> RecordHashes:
    """计算 code_hash / data_hash / config_hash（1 号第三部分第 5 条、docs/13 §8）。

    Args:
        config: 研究配置（取 manifest 与数据根）。
        root: 仓库根。
        grid_path: 本次网格文件。
        extra_configs: 其他参与计算的配置文件（例如仓位配置、费率配置）。
        family: 本轮方向（其策略模块是代码闭包的一部分）；None 表示只算框架。
        component_families: R5 的成分方向（成分模块计入 R5 的闭包）。

    Returns:
        RecordHashes。

    Raises:
        FileNotFoundError: 缺少策略模块、manifest、选币快照或配置文件（红线 12）。
    """
    entries: list[Path] = [
        *(root / "src" / "trade" / "research").rglob("*.py"),
        root / "scripts" / "portfolio_backtest.py",
    ]
    families = [name for name in (family, *component_families) if name]
    if family == "ensemble":
        # 组合方向通过 registry 分派成分方向的 build，本身不导入成分模块：
        # 按 component_families 显式加入闭包（docs/13 §8）。
        families = [name for name in component_families if name]
    for name in families:
        module = root / "src" / "trade" / "strategies" / f"{name}.py"
        if not module.is_file():
            raise FileNotFoundError(f"缺少策略模块：{module}（红线 12）")
        entries.append(module)
    code_files = _code_closure(root, entries)
    data_root = root / config.paths.data_root
    data_files: list[Path] = [
        data_root / "history" / name
        for name in (
            "manifest.json",
            "manifest_flow.json",
            "manifest_premium.json",
            "manifest_mark.json",
        )
    ]
    data_files.extend(sorted((data_root / "universe_hist").glob("*.json")))
    missing_data = [str(path) for path in data_files if not path.is_file()]
    if missing_data:
        raise FileNotFoundError(f"缺少数据清单或选币快照：{missing_data}（红线 12）")
    config_files = [root / "configs" / "research.yaml", *extra_configs]
    if grid_path is not None:
        config_files.append(grid_path)
    missing = [str(path) for path in config_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少配置文件：{missing}（红线 12）")
    return RecordHashes(
        code_hash=_hash_files(code_files),
        data_hash=_hash_files(data_files),
        config_hash=_hash_files(config_files),
    )


@dataclass
class GroupResult:
    """一组参数的两条曲线与统计（可落盘复用，1 号 B 第 13 条）。"""

    family: str
    params: dict[str, object]
    train_daily: pd.Series
    train_entries: list[int]
    train_meta: dict[str, float]
    own_daily: pd.Series
    own_meta: dict[str, float] = field(default_factory=dict)
    # R5 组合：训练曲线必须按折使用该折的成分参数（1 号第八部分 R5），
    # 因此为每一折各存一条训练日收益与训练期交易数；其他方向的这两个字段为空。
    train_by_fold: list[pd.Series] = field(default_factory=list)
    train_trades_by_fold: list[int] = field(default_factory=list)

    def as_param_run_for_fold(self, index: int) -> ParamRun:
        """转成第 `index` 折选参用的 ParamRun（该折训练曲线与交易数）。

        Args:
            index: 折序号（0 起）。

        Returns:
            ParamRun，`daily` 为该折成分参数下的训练曲线，`trades` 长度等于该折训练期交易数。

        Raises:
            IndexError: 该组没有按折成分参数（非组合方向）。
        """
        series = self.train_by_fold[index]
        # 交易数与训练窗口内的一致性：虚拟交易的 entry_ts 取该序列首日（必然落在
        # [研究起点, 该折起点) 内），使 select_for_fold 的交易数下限判断正确。
        entry_ms = int(series.index[0].timestamp() * 1000) if len(series) else 0
        trades = [
            Trade(
                symbol="",
                side=0,
                entry_ts=entry_ms,
                exit_ts=entry_ms,
                notional=0.0,
                fees=0.0,
                funding=0.0,
                gross_pnl=0.0,
                net_pnl=0.0,
                reason="cached",
                entry_equity=0.0,
            )
            for _ in range(self.train_trades_by_fold[index])
        ]
        return ParamRun(
            key=params_key(self.params),
            params=dict(self.params),
            daily=series,
            trades=trades,
            turnover=0.0,
            dropped=0,
            forced=0,
            cost_total=0.0,
            funding_total=0.0,
            long_gross=0.0,
            short_gross=0.0,
            risk_events=0,
            params_meta={},
        )

    def as_param_run(self) -> ParamRun:
        """转成折内选参用的 ParamRun（只用日收益与开仓时刻）。"""
        trades = [
            Trade(
                symbol="",
                side=0,
                entry_ts=int(ts),
                exit_ts=int(ts),
                notional=0.0,
                fees=0.0,
                funding=0.0,
                gross_pnl=0.0,
                net_pnl=0.0,
                reason="cached",
                entry_equity=0.0,
            )
            for ts in self.train_entries
        ]
        return ParamRun(
            key=params_key(self.params),
            params=dict(self.params),
            daily=self.train_daily,
            trades=trades,
            turnover=float(self.train_meta.get("turnover", 0.0)),
            dropped=int(self.train_meta.get("dropped", 0.0)),
            forced=int(self.train_meta.get("forced", 0.0)),
            cost_total=float(self.train_meta.get("cost_total", 0.0)),
            funding_total=float(self.train_meta.get("funding_total", 0.0)),
            long_gross=float(self.train_meta.get("long_gross", 0.0)),
            short_gross=float(self.train_meta.get("short_gross", 0.0)),
            risk_events=int(self.train_meta.get("risk_events", 0.0)),
            params_meta=dict(self.train_meta),
        )

    def own_daily_sharpe(self) -> float:
        """自身拼接曲线的日频非年化夏普（试验登记字段）。"""
        series = self.own_daily
        if series.size < 3:
            return 0.0
        std = float(series.std(ddof=1))
        return float(series.mean() / std) if std > 0.0 else 0.0

    def own_annualized_sharpe(self) -> float:
        """自身拼接曲线的年化夏普。"""
        return annualized_sharpe(self.own_daily)


def _series_to_json(series: pd.Series) -> list[list[Any]]:
    """日收益序列转 JSON（ISO 日期 + 浮点值）。"""
    return [[index.isoformat(), float(value)] for index, value in series.items()]


def _series_from_json(values: Sequence[Sequence[Any]]) -> pd.Series:
    """JSON 转日收益序列。"""
    if not values:
        return pd.Series(dtype="float64")
    index = pd.to_datetime([str(item[0]) for item in values], utc=True)
    return pd.Series([float(item[1]) for item in values], index=index, dtype="float64")


def group_cache_path(group_dir: Path, family: str, key: str) -> Path:
    """逐组落盘的文件名：`<family>_<参数键哈希>.json`（参数键含引号与冒号，不能直接做文件名）。"""
    digest = hashlib.sha1(f"{family}|{key}".encode()).hexdigest()[:16]
    return group_dir / f"{family}_{digest}.json"


def save_group(path: Path, group: GroupResult, hashes: RecordHashes) -> None:
    """原子写入一组参数的结果（先写临时文件再改名，1 号 B 第 13 条）。"""
    payload = {
        "hashes": hashes.as_dict(),
        "family": group.family,
        "params": group.params,
        "train": {
            "daily": _series_to_json(group.train_daily),
            "entries": [int(ts) for ts in group.train_entries],
            "meta": group.train_meta,
        },
        "train_by_fold": [
            {"daily": _series_to_json(series), "trades": int(count)}
            for series, count in zip(group.train_by_fold, group.train_trades_by_fold, strict=True)
        ],
        "own": {"daily": _series_to_json(group.own_daily), "meta": group.own_meta},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_group(path: Path, hashes: RecordHashes) -> GroupResult | None:
    """读取逐组结果；文件不存在或哈希不一致时返回 None（该轮需要重跑）。"""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("hashes") != hashes.as_dict():
        return None
    train = payload["train"]
    own = payload["own"]
    train_by_fold_raw = payload.get("train_by_fold") or []
    return GroupResult(
        family=str(payload["family"]),
        params=dict(payload["params"]),
        train_daily=_series_from_json(train["daily"]),
        train_entries=[int(ts) for ts in train["entries"]],
        train_meta={key: float(value) for key, value in train["meta"].items()},
        own_daily=_series_from_json(own["daily"]),
        own_meta={key: float(value) for key, value in own["meta"].items()},
        train_by_fold=[_series_from_json(item["daily"]) for item in train_by_fold_raw],
        train_trades_by_fold=[int(item["trades"]) for item in train_by_fold_raw],
    )


def read_trial_sharpes(log_path: Path) -> list[float]:
    """从 EXPERIMENTS.log 读取全部 kind=tuning 记录的日频夏普。"""
    if not log_path.is_file():
        return []
    values: list[float] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("kind") == "tuning" and "daily_sharpe" in entry:
            values.append(float(entry["daily_sharpe"]))
    return values


def read_void_trial_sharpes(reports_dir: Path) -> list[float]:
    """读取研究重置时移到 `_void_*/` 的旧 `kind=tuning` 记录的日频夏普（docs/13 §7）。"""
    values: list[float] = []
    for log_path in sorted(reports_dir.glob("_void_*/EXPERIMENTS.log")):
        values.extend(read_trial_sharpes(log_path))
    return values


def trial_sharpes_for(
    round_id: str, log_path: Path, reports_dir: Path
) -> tuple[list[float], list[float]]:
    """返回 (主口径 trial_sharpes, 含旧记录的敏感性口径)。

    R11、R12 与 X0~X4 的主口径另加入 00 号研究重置时移到 `_void_*/` 的旧记录；
    其余轮次主口径只读当前 `EXPERIMENTS.log`，另输出包含旧记录的敏感性值（docs/13 §7）。
    """
    current = read_trial_sharpes(log_path)
    void = read_void_trial_sharpes(reports_dir)
    if round_id in _VOID_IN_MAIN_ROUNDS or round_id.startswith("X"):
        return current + void, current + void
    return current, current + void


def append_experiments(log_path: Path, entries: list[dict[str, Any]]) -> None:
    """按行追加试验登记（docs/13 §9）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def _group_meta(run: ParamRun, *, prefix: str) -> dict[str, float]:
    """一组参数运行的统计字段（落盘与报告共用）。"""
    return {
        f"{prefix}daily_sharpe": float(run.params_meta.get("daily_sharpe", 0.0)),
        f"{prefix}trades": float(len(run.trades)),
        f"{prefix}turnover": float(run.turnover),
        f"{prefix}dropped": float(run.dropped),
        f"{prefix}forced": float(run.forced),
        f"{prefix}cost_total": float(run.cost_total),
        f"{prefix}funding_total": float(run.funding_total),
        f"{prefix}long_gross": float(run.long_gross),
        f"{prefix}short_gross": float(run.short_gross),
        f"{prefix}risk_events": float(run.risk_events),
        f"{prefix}attempted": float(run.params_meta.get("attempted", 0.0)),
        f"{prefix}dropped_min_notional": float(run.params_meta.get("dropped_min_notional", 0.0)),
        f"{prefix}funding_estimated": float(run.params_meta.get("funding_estimated", 0.0)),
        f"{prefix}funding_missing": float(run.params_meta.get("funding_missing", 0.0)),
        f"{prefix}mark_fallback": float(run.params_meta.get("mark_fallback", 0.0)),
    }


def ensure_groups(
    *,
    market,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    pairs: Sequence[tuple[str, Mapping[str, object]]],
    payload: dict[str, Any],
    workers: int,
    group_dir: Path,
    log_path: Path,
    hashes: RecordHashes,
    round_id: str,
    folds: list[Fold],
    start_ms: int,
    end_ms: int,
    register: bool = True,
    components_by_fold: Sequence[Mapping[str, Mapping[str, object]]] | None = None,
    components_by_mode: Mapping[str, Sequence[Mapping[str, Mapping[str, object]]]] | None = None,
) -> dict[tuple[str, str], GroupResult]:
    """取得每组参数的训练曲线与自身拼接曲线：命中缓存直接复用，否则重跑并落盘。

    Args:
        market: 已加载的市场数据（`workers > 1` 时为 None，工作进程各自加载）。
        config: 研究配置。
        sizing: 信号口径仓位配置（初始权益 = `equity.signal`）。
        fee_taker: 吃单费率。
        risk_limits: 风控参数。
        pairs: 需要的 (family, params) 列表。
        payload: 工作进程载荷。
        workers: 并行进程数。
        group_dir: 逐组落盘目录（`artifacts/reports/<轮次>/groups`）。
        log_path: `EXPERIMENTS.log`。
        hashes: 本次三个哈希；任一不同时该组重跑。
        round_id: 轮次 ID（写试验记录）。
        folds: 验证折（第 1 折起点是自身拼接曲线的起点）。
        start_ms: 研究起点。
        end_ms: 研究终点（不含）。
        register: 是否登记 `kind=tuning` 试验（R5 的成分复用其它轮次已登记的组时为 False）。
        components_by_fold: R5 的逐折成分参数（所有组同一套）；给出时训练曲线按折分别运行
            （该折之前的数据 + 该折成分参数），自身拼接曲线也在折边界切换成分参数
            （1 号第八部分 R5）。
        components_by_mode: 按仓位模式给出的逐折成分参数（1 号第五部分 B 第 15 条 k 款）：
            每组用自己 `sizing_mode` 对应的那一套；与 `components_by_fold` 二选一。

    Returns:
        {(family, params_key): GroupResult}。

    Raises:
        ValueError: 参数重复（同一 family 下同一组参数出现两次）；成分参数折数不一致；
            某组的仓位模式没有对应的成分参数。
    """
    if components_by_mode is not None and components_by_fold is not None:
        raise ValueError("components_by_mode 与 components_by_fold 只能给出一个（docs/13 §5）")
    if components_by_fold is not None and len(components_by_fold) != len(folds):
        raise ValueError(f"成分参数折数 {len(components_by_fold)} 与验证折数 {len(folds)} 不一致")
    if components_by_mode is not None:
        for mode, values in components_by_mode.items():
            if len(values) != len(folds):
                raise ValueError(
                    f"仓位模式 {mode} 的成分参数折数 {len(values)} 与验证折数 {len(folds)} 不一致"
                )
    results: dict[tuple[str, str], GroupResult] = {}
    fresh: list[tuple[str, dict[str, object]]] = []
    for family, params in pairs:
        key = params_key(params)
        if (family, key) in results or any(
            item_family == family and params_key(item_params) == key
            for item_family, item_params in fresh
        ):
            raise ValueError(f"参数键重复：{family} {key}")
        cached = load_group(group_cache_path(group_dir, family, key), hashes)
        if cached is not None:
            results[(family, key)] = cached
        else:
            fresh.append((family, dict(params)))
    if not fresh:
        return results
    tasks: list[dict[str, Any]] = []
    strides: list[int] = []
    for family, params in fresh:
        per_fold = components_by_fold
        if components_by_mode is not None:
            mode = str(params.get("sizing_mode", "fixed_risk"))
            if mode not in components_by_mode:
                raise ValueError(
                    f"{family} 的仓位模式 {mode} 没有对应成分参数（1 号第五部分 B 第 15 条 k 款）"
                )
            per_fold = components_by_mode[mode]
        if per_fold is None:
            tasks.append(
                {
                    "family": family,
                    "params": params,
                    "options": {"name": "base"},
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            )
            tasks.append(
                {
                    "family": family,
                    "params": params,
                    "options": {"name": "base"},
                    "start_ms": folds[0].start_ms,
                    "end_ms": end_ms,
                }
            )
            strides.append(2)
            continue
        for index, fold in enumerate(folds):
            tasks.append(
                {
                    "family": family,
                    "params": params,
                    "options": {"name": "base"},
                    "start_ms": start_ms,
                    "end_ms": fold.start_ms,
                    "components": per_fold[index],
                }
            )
        tasks.append(
            {
                "kind": "path",
                "family": family,
                "params": params,
                "folds": list(folds),
                "components_by_fold": list(per_fold),
                "end_ms": end_ms,
            }
        )
        strides.append(len(folds) + 1)
    runs = _run_tasks(tasks, payload=payload, workers=workers, market=market)
    entries: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    offset = 0
    for (family, params), stride in zip(fresh, strides, strict=True):
        chunk = runs[offset : offset + stride]
        offset += stride
        if stride == 2:
            train = chunk[0]
            own = chunk[1]
            group = GroupResult(
                family=family,
                params=params,
                train_daily=train.daily,
                train_entries=[int(trade.entry_ts) for trade in train.trades],
                train_meta=_group_meta(train, prefix="train_"),
                own_daily=own.daily,
                own_meta=_group_meta(own, prefix="own_"),
            )
        else:
            fold_runs = chunk[: len(folds)]
            own = chunk[-1]
            merged = (
                pd.concat([run.daily for run in fold_runs]).sort_index()
                if fold_runs
                else pd.Series(dtype="float64")
            )
            merged = merged[~merged.index.duplicated(keep="first")]
            group = GroupResult(
                family=family,
                params=params,
                train_daily=merged,
                train_entries=sorted(
                    {int(trade.entry_ts) for run in fold_runs for trade in run.trades}
                ),
                train_meta=_group_meta(fold_runs[0], prefix="train_"),
                own_daily=own.daily,
                own_meta=_group_meta(own, prefix="own_"),
                train_by_fold=[run.daily for run in fold_runs],
                train_trades_by_fold=[len(run.trades) for run in fold_runs],
            )
        key = params_key(params)
        results[(family, key)] = group
        save_group(group_cache_path(group_dir, family, key), group, hashes)
        if not register:
            continue
        entries.append(
            {
                "ts": now,
                "kind": "tuning",
                "round": round_id,
                "family": family,
                "params": params,
                "daily_sharpe": group.own_daily_sharpe(),
                "annualized_sharpe": group.own_annualized_sharpe(),
                "trades": int(group.own_meta.get("own_trades", 0.0)),
                "equity_basis": config.equity.signal,
                **hashes.as_dict(),
            }
        )
    if entries:
        append_experiments(log_path, entries)
    return results


def build_segments(
    *,
    family: str,
    folds: list[Fold],
    choices: list[FoldChoice],
    params_by_key: Mapping[str, Mapping[str, object]],
    config: ResearchConfig,
    components_by_fold: Sequence[Mapping[str, Mapping[str, object]]] | None = None,
    components_by_mode: Mapping[str, Sequence[Mapping[str, Mapping[str, object]]]] | None = None,
) -> list[Segment]:
    """按折构造连续路径的段（折边界换参数，不在边界额外强平，docs/13 §6）。

    Args:
        components_by_fold: 所有折同一套成分参数（旧路径）。
        components_by_mode: 按仓位模式的逐折成分参数；每折用该折选中参数的 `sizing_mode`
            对应的那一套（1 号第五部分 B 第 15 条 k 款）。

    Raises:
        ValueError: 某折选中参数的仓位模式没有对应的成分参数。
    """
    segments: list[Segment] = []
    for index, (fold, choice) in enumerate(zip(folds, choices, strict=True)):
        params = dict(params_by_key[choice.params_key])
        mode = str(params.get("sizing_mode", "fixed_risk"))
        components = None if components_by_fold is None else components_by_fold[index]
        if components_by_mode is not None:
            if mode not in components_by_mode:
                raise ValueError(
                    f"{family} 第 {index + 1} 折的仓位模式 {mode} 没有对应成分参数"
                    "（1 号第五部分 B 第 15 条 k 款）"
                )
            components = components_by_mode[mode][index]
        strategy = build_strategy(
            family,
            params,
            stop_max=config.execution.stop_max,
            components=components,
        )
        segments.append(
            Segment(
                start_ms=fold.start_ms,
                end_ms=fold.end_ms,
                strategy=strategy,
                label=choice.params_key,
                sizing_mode=mode,
            )
        )
    return segments


def run_selected_path(
    market,
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    segments: Sequence[Segment],
    end_ms: int,
    options: ScenarioOptions | None = None,
    seed: int | None = None,
) -> RunResult:
    """以给定仓位口径与初始权益连续运行整条拼接路径（docs/13 §6 第 10 条 b 款）。

    Args:
        seed: shuffle 情景的随机种子；None 表示用 `config.bootstrap.seed`。
    """
    engine = Engine(
        market=market,
        sizing_cfg=sizing,
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        options=options or ScenarioOptions(),
        seed=config.bootstrap.seed if seed is None else seed,
    )
    return engine.run_path(list(segments), int(end_ms))


def run_path_param(
    market,
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    family: str,
    params: Mapping[str, object],
    folds: Sequence[Fold],
    end_ms: int,
    components_by_fold: Sequence[Mapping[str, Mapping[str, object]]] | None = None,
    components_by_mode: Mapping[str, Sequence[Mapping[str, Mapping[str, object]]]] | None = None,
) -> ParamRun:
    """同一组参数按折切换成分参数的“自身拼接曲线”（R5 专用）。

    规格：1 号第八部分 R5（第 k 折的成分参数由该折之前的数据选出，折边界切换）；
    `components_by_mode` 时按该组 `sizing_mode` 取对应模式的成分（1 号第五部分 B 第 15 条 k 款）。

    Args:
        market: 市场数据。
        config: 研究配置。
        sizing: 信号口径仓位配置。
        fee_taker: 吃单费率。
        risk_limits: 风控参数。
        family: 方向（组合为 `ensemble`）。
        params: 该组参数（各折相同）。
        folds: 验证折。
        end_ms: 路径终点（不含）。
        components_by_fold: 每折的成分参数（所有组同一套），长度必须与 `folds` 相同。
        components_by_mode: 按仓位模式的逐折成分参数。

    Returns:
        ParamRun（该组的自身拼接曲线）。

    Raises:
        ValueError: 成分参数折数与验证折数不一致，或没有给出任何成分参数。
    """
    if components_by_fold is None and components_by_mode is None:
        raise ValueError("R5 的自身拼接曲线需要成分参数（1 号第八部分 R5）")
    if components_by_fold is not None and len(components_by_fold) != len(folds):
        raise ValueError(f"成分参数折数 {len(components_by_fold)} 与验证折数 {len(folds)} 不一致")
    if components_by_mode is not None:
        for mode, values in components_by_mode.items():
            if len(values) != len(folds):
                raise ValueError(
                    f"仓位模式 {mode} 的成分参数折数 {len(values)} 与验证折数 {len(folds)} 不一致"
                )
    key = params_key(params)
    choices = [
        FoldChoice(fold=fold, params_key=key, train_sharpe=0.0, train_trades=0) for fold in folds
    ]
    segments = build_segments(
        family=family,
        folds=list(folds),
        choices=choices,
        params_by_key={key: dict(params)},
        config=config,
        components_by_fold=components_by_fold,
        components_by_mode=components_by_mode,
    )
    result = run_selected_path(
        market,
        config=config,
        sizing=sizing,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        segments=segments,
        end_ms=end_ms,
    )
    return _to_param_run(params, result)


def _component_param_sets(
    root: Path, mapping: Mapping[str, object], *, sizing_mode: str | None = None
) -> dict[str, list[dict[str, object]]]:
    """读取 R5 成分网格的参数集合。

    1 号第五部分 B 第 15 条 k 款：R5 每组的 `sizing_mode` 为 A 时，三个成分取各自网格中模式 A 的组
    为该折选出的参数；为 B 时取模式 B 的组。给出 `sizing_mode` 时只保留该模式的参数（保留
    `sizing_mode` 字段供运行使用）；未给出时去掉该维度并去重（旧行为）。

    Args:
        root: 仓库根目录（解析 `component_grids` 的相对路径）。
        mapping: `{family: 网格文件相对路径}`。
        sizing_mode: 仓位模式（`fixed_risk` / `vol_target`）；None 表示不区分模式。

    Returns:
        `{family: [参数字典, ...]}`，顺序与网格文件一致（去重后）。

    Raises:
        ValueError: 网格缺少 `params`。
    """
    params_by_family: dict[str, list[dict[str, object]]] = {}
    for family, path in mapping.items():
        component_grid = load_grid(root / str(path))
        raw = component_grid.get("params")
        if not isinstance(raw, list):
            raise ValueError(f"成分网格缺少 params：{path}")
        cleaned: list[dict[str, object]] = []
        seen: set[tuple[tuple[str, object], ...]] = set()
        for item in raw:
            params = dict(item)
            mode = str(params.get("sizing_mode", "fixed_risk"))
            if sizing_mode is None:
                params = {key: value for key, value in params.items() if key != "sizing_mode"}
            elif mode != sizing_mode:
                continue
            signature = tuple(sorted(params.items()))
            if signature in seen:
                continue
            seen.add(signature)
            cleaned.append(params)
        params_by_family[str(family)] = cleaned
    return params_by_family


def component_params_by_fold(
    *,
    grid: Mapping[str, Any],
    config: ResearchConfig,
    market,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    payload: dict[str, Any],
    workers: int,
    group_dir: Path,
    log_path: Path,
    hashes: RecordHashes,
    round_id: str,
    folds: list[Fold],
    start_ms: int,
    end_ms: int,
    root: Path,
    sizing_mode: str,
) -> list[dict[str, dict[str, object]]] | None:
    """R5 的成分参数逐折选择（1 号第八部分 R5、docs/13 §5）。

    每个成分方向取其第一次网格中与 R5 本组同仓位模式的参数（1 号第五部分 B 第 15 条 k 款），
    只用该折开始之前的数据按年化夏普选参；成分参数的评估已在各自轮次登记，这里复用结果、
    不重复登记试验。

    Args:
        sizing_mode: R5 本组的仓位模式，决定成分只从该模式的组里选（k 款）。

    Returns:
        每折一个 `{family: params}` 映射；网格没有 `component_grids` 时返回 None。
    """
    mapping = grid.get("component_grids")
    if not isinstance(mapping, Mapping) or not mapping:
        return None
    params_by_family = _component_param_sets(root, mapping, sizing_mode=sizing_mode)
    empty = sorted(family for family, values in params_by_family.items() if not values)
    if empty:
        raise ValueError(
            f"R5 的成分网格在仓位模式 {sizing_mode} 下没有参数：{empty}"
            "（1 号第五部分 B 第 15 条 k 款）"
        )
    pairs: list[tuple[str, Mapping[str, object]]] = []
    for family, params_list in params_by_family.items():
        pairs.extend((family, params) for params in params_list)

    groups = ensure_groups(
        market=market,
        config=config,
        sizing=sizing,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        pairs=pairs,
        payload=payload,
        workers=workers,
        group_dir=group_dir,
        log_path=log_path,
        hashes=hashes,
        round_id=round_id,
        folds=folds,
        start_ms=start_ms,
        end_ms=end_ms,
        register=False,
    )
    choices: dict[str, list[dict[str, object]]] = {}
    for family, params_list in params_by_family.items():
        runs = {
            params_key(params): groups[(family, params_key(params))].as_param_run()
            for params in params_list
        }
        by_key = {params_key(params): params for params in params_list}
        choices[family] = [
            by_key[select_for_fold(runs, fold, start_ms).params_key] for fold in folds
        ]
    return [{family: choices[family][index] for family in choices} for index in range(len(folds))]


def select_choices(
    *,
    groups: Mapping[tuple[str, str], GroupResult],
    family: str,
    params_by_key: Mapping[str, Mapping[str, object]],
    folds: Sequence[Fold],
    start_ms: int,
    components_by_fold: Sequence[Mapping[str, Mapping[str, object]]] | None = None,
    components_by_mode: Mapping[str, Sequence[Mapping[str, Mapping[str, object]]]] | None = None,
) -> list[FoldChoice]:
    """按第七部分第 3 条为每一折选参（组合方向用该折成分参数下的训练曲线）。

    Args:
        groups: 逐组结果。
        family: 方向。
        params_by_key: `{参数键: 参数}`。
        folds: 验证折。
        start_ms: 研究起点（训练期起点）。
        components_by_fold: 组合方向的逐折成分参数；为 None 时用各折共用的训练曲线。
        components_by_mode: 组合方向按仓位模式的逐折成分参数（只需非空即按折选参，
            训练曲线本身已由 `ensure_groups` 按模式计算）。

    Returns:
        每折一个 FoldChoice。

    Raises:
        ValueError: 某折没有满足训练期交易数下限的参数。
    """
    if components_by_fold is None and components_by_mode is None:
        train_runs = {key: groups[(family, key)].as_param_run() for key in params_by_key}
        return [select_for_fold(train_runs, fold, start_ms) for fold in folds]
    choices: list[FoldChoice] = []
    for index, fold in enumerate(folds):
        fold_runs = {
            key: groups[(family, key)].as_param_run_for_fold(index) for key in params_by_key
        }
        choices.append(select_for_fold(fold_runs, fold, start_ms))
    return choices


def _block_metrics(result: RunResult) -> dict[str, float]:
    """一次运行的指标块（情景、可执行口径与分折诊断共用）。"""
    daily = result.daily_returns()
    std = float(daily.std(ddof=1)) if daily.size > 1 else 0.0
    equity = (1.0 + daily).cumprod() if daily.size else pd.Series(dtype="float64")
    drawdown = float(((equity.cummax() - equity) / equity.cummax()).max()) if equity.size else 0.0
    return {
        "annualized_sharpe": annualized_sharpe(daily),
        "daily_sharpe": float(daily.mean() / std) if std > 0.0 else 0.0,
        "net_return": float(equity.iloc[-1] - 1.0) if equity.size else 0.0,
        "max_drawdown": drawdown,
        "longest_drawdown_days": longest_drawdown_days(equity),
        "compound_monthly": compound_monthly(equity),
        "worst_week": worst_week(equity),
        "trades": float(len(result.trades)),
        "net_pnl": float(sum(trade.net_pnl for trade in result.trades)),
        "dropped": float(result.dropped),
        "dropped_min_notional": float(result.dropped_min_notional),
        "attempted": float(result.attempted),
        "funding_estimated": float(result.funding_estimated),
        "funding_missing": float(result.funding_missing),
        "mark_fallback": float(result.mark_fallback),
        "risk_events": float(len(result.risk_events)),
    }


def _fold_stats(
    result: RunResult, folds: list[Fold], choices: list[FoldChoice]
) -> list[dict[str, Any]]:
    """分折统计：净收益、交易数、多空毛收益（第七部分第 5 条 b、h 款）。"""
    daily = result.daily_returns()
    stats: list[dict[str, Any]] = []
    for fold, choice in zip(folds, choices, strict=True):
        piece = slice_returns(daily, fold.start_ms, fold.end_ms)
        fold_trades = trades_in_fold(result.trades, fold)
        net = float((1.0 + piece).prod() - 1.0) if piece.size else 0.0
        stats.append(
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
                "params": choice.params_key,
                "train_sharpe": choice.train_sharpe,
                "train_trades": choice.train_trades,
                "net_return": net,
                "trades": len(fold_trades),
                "long_gross": float(sum(t.gross_pnl for t in fold_trades if t.side > 0)),
                "short_gross": float(sum(t.gross_pnl for t in fold_trades if t.side < 0)),
            }
        )
    return stats


def _x0_reference_daily(root: Path, out_dir: Path) -> pd.Series | None:
    """X0 中 N = 20 那组自身拼接曲线的日收益（docs/13 §7 第 11 条）。

    Returns:
        日收益序列；X0 网格或结果不存在时返回 None（第 11 条无法判定）。
    """
    grid_path = root / "configs" / "strategies" / "round_X0.yaml"
    if not grid_path.is_file():
        return None
    grid = load_grid(grid_path)
    params = next(
        (dict(item) for item in grid["params"] if int(dict(item).get("basket_n", 0)) == 20), None
    )
    if params is None:
        return None
    family = str(grid["family"])
    path = group_cache_path(out_dir / "X0" / "groups", family, params_key(params))
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _series_from_json(payload["own"]["daily"])


def _short_excess_sharpe(daily: pd.Series, reference: pd.Series | None) -> float | None:
    """扣除 X0 后的超额年化夏普：日收益对 X0 日收益回归取残差（docs/13 §7 第 11 条）。"""
    if reference is None or daily.empty or reference.empty:
        return None
    frame = pd.concat(
        [daily.rename("strategy"), reference.rename("x0")], axis=1, join="inner"
    ).dropna()
    if frame.shape[0] < 3:
        return None
    variance = float(frame["x0"].var(ddof=1))
    if variance <= 0.0:
        return annualized_sharpe(frame["strategy"])
    beta = float(frame["strategy"].cov(frame["x0"]) / variance)
    excess = frame["strategy"] - beta * frame["x0"]
    return annualized_sharpe(excess)


def _tier_passes(stats: Mapping[str, float], signal_sharpe: float) -> bool:
    """单个档位的可执行性检查（docs/13 §7 第 9 条）。"""
    attempted = stats.get("attempted", 0.0)
    dropped = stats.get("dropped_min_notional", 0.0)
    ratio = dropped / attempted if attempted > 0.0 else 0.0
    return (
        ratio <= _EXEC_DROP_RATIO_MAX
        and stats.get("net_return", -1.0) > 0.0
        and float(stats.get("annualized_sharpe", 0.0)) >= _EXEC_SHARPE_RATIO_MIN * signal_sharpe
    )


def _checks(
    *,
    family: str,
    exposure: str,
    base_sharpe: float,
    metrics: Mapping[str, object],
    fold_stats: list[dict[str, Any]],
    scenario_stats: Mapping[str, Mapping[str, float]],
    shuffle_sharpes: Sequence[float] | None,
    exec_tiers: Mapping[str, Mapping[str, float]],
    short_excess_sharpe: float | None,
) -> dict[str, bool]:
    """达标项 a~k（docs/13 §7 第 1~11 条，信号口径），返回逐项布尔值。

    Args:
        family: 方向（判断 shuffle 是否适用、是否只做空）。
        exposure: `neutral` / `directional`。
        base_sharpe: 拼接曲线年化夏普。
        metrics: `summarize` 的输出。
        fold_stats: 分折统计。
        scenario_stats: 情景统计。
        shuffle_sharpes: 全部种子的 shuffle 年化夏普；不适用时为 None。
        exec_tiers: 四个档位的 500 USDT 口径统计。
        short_excess_sharpe: 扣除 X0 后的超额年化夏普（只做空方向）。
    """
    if family in _SHUFFLE_NOT_APPLICABLE:
        shuffle_ok = True
    elif not shuffle_sharpes:
        raise ValueError(f"{family} 的 shuffle 情景缺少种子结果（docs/13 §7）")
    else:
        shuffle_ok = all(value < base_sharpe for value in shuffle_sharpes)
    delay1_sharpe = scenario_stats.get("delay1", {}).get("annualized_sharpe", 1.0)
    bootstrap = metrics["bootstrap"]
    assert isinstance(bootstrap, Mapping)
    checks: dict[str, bool] = {
        "sharpe_ge_080": base_sharpe >= _MIN_ANNUALIZED_SHARPE,
        "folds_positive_ge_3": sum(1 for item in fold_stats if item["net_return"] > 0.0)
        >= _MIN_POSITIVE_FOLDS,
        "bootstrap_lower_gt_0": float(bootstrap["lower_90"]) > 0.0,
        "dsr_ge_050": float(metrics["deflated_sharpe"]) >= _MIN_DSR,
        "cost2x_positive": scenario_stats.get("cost2x", {}).get("net_return", -1.0) > 0.0,
        "shuffle_all_below_base": shuffle_ok,
        "delay1_le_base_plus_030": delay1_sharpe <= base_sharpe + _MAX_DELAY1_EXCESS,
        "exec_tier_ok": any(_tier_passes(stats, base_sharpe) for stats in exec_tiers.values()),
    }
    if exposure == EXPOSURE_NEUTRAL:
        checks["side_condition"] = (
            sum(1 for item in fold_stats if item["long_gross"] + item["short_gross"] > 0.0)
            >= _MIN_POSITIVE_FOLDS
        )
        checks["beta_abs_le_030"] = abs(float(metrics["realized_beta"])) <= _MAX_BETA_ABS
        checks["alpha_sharpe_ge_06"] = float(metrics["alpha_sharpe"]) >= _MIN_ALPHA_SHARPE
    else:
        checks["side_condition"] = (
            scenario_stats.get("long_only", {}).get("net_pnl", 0.0) > 0.0
            or scenario_stats.get("short_only", {}).get("net_pnl", 0.0) > 0.0
        )
    if family in _SHORT_ONLY_FAMILIES:
        checks["short_excess_sharpe_ge_06"] = (
            short_excess_sharpe is not None and short_excess_sharpe >= _MIN_SHORT_EXCESS_SHARPE
        )
    return checks


def _write_report(
    *,
    round_id: str,
    family: str,
    exposure: str,
    folds: list[Fold],
    report: dict[str, Any],
    concat_daily: pd.Series,
    out_dir: Path,
) -> dict[str, Any]:
    """写 `grid.json` 与 `selected_daily_returns.csv`。"""
    target_dir = out_dir / round_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "grid.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not concat_daily.empty:
        concat_daily.rename("return").to_frame().to_csv(target_dir / "selected_daily_returns.csv")
    return report


def _param_rows(
    *,
    family: str,
    params_list: Sequence[Mapping[str, object]],
    groups: Mapping[tuple[str, str], GroupResult],
    choices: Sequence[FoldChoice],
) -> list[dict[str, Any]]:
    """每组参数一行（训练曲线与自身拼接曲线的统计）。"""
    rows: list[dict[str, Any]] = []
    for params in params_list:
        key = params_key(params)
        group = groups[(family, key)]
        meta = group.train_meta
        rows.append(
            {
                "key": key,
                "params": dict(params),
                "own_concat_daily_sharpe": group.own_daily_sharpe(),
                "own_concat_annualized_sharpe": group.own_annualized_sharpe(),
                "full_period_daily_sharpe": float(meta.get("train_daily_sharpe", 0.0)),
                "full_trades": int(meta.get("train_trades", 0.0)),
                "selected_in_folds": sum(1 for choice in choices if choice.params_key == key),
                "turnover": float(meta.get("train_turnover", 0.0)),
                "dropped": int(meta.get("train_dropped", 0.0)),
                "forced": int(meta.get("train_forced", 0.0)),
                "cost_total": float(meta.get("train_cost_total", 0.0)),
                "funding_total": float(meta.get("train_funding_total", 0.0)),
                "risk_events": int(meta.get("train_risk_events", 0.0)),
                "funding_estimated": int(meta.get("train_funding_estimated", 0.0)),
                "funding_missing": int(meta.get("train_funding_missing", 0.0)),
                "mark_fallback": int(meta.get("train_mark_fallback", 0.0)),
            }
        )
    return rows


def _no_selection_report(
    *,
    round_id: str,
    family: str,
    exposure: str,
    folds: list[Fold],
    params_list: Sequence[Mapping[str, object]],
    groups: Mapping[tuple[str, str], GroupResult],
    hashes: RecordHashes,
    failure: str,
    out_dir: Path,
) -> dict[str, Any]:
    """没有参数满足折内选参条件时的报告（试验已由 ensure_groups 照常登记）。"""
    rows = _param_rows(family=family, params_list=params_list, groups=groups, choices=[])
    report: dict[str, Any] = {
        "round": round_id,
        "family": family,
        "exposure": exposure,
        "generated_at": datetime.now(UTC).isoformat(),
        "hashes": hashes.as_dict(),
        "folds": [
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
            }
            for fold in folds
        ],
        "params": rows,
        "fold_stats": [],
        "selected_params": {},
        "selected_metrics": {},
        "exec_tiers": {},
        "scenarios": {},
        "checks": {},
        "passed": False,
        "failure": failure,
    }
    return _write_report(
        round_id=round_id,
        family=family,
        exposure=exposure,
        folds=folds,
        report=report,
        concat_daily=pd.Series(dtype="float64"),
        out_dir=out_dir,
    )


def evaluate_grid(
    *,
    config: ResearchConfig,
    sizing: SizingConfig,
    fee_taker: float,
    risk_limits: RiskLimits,
    grid: Mapping[str, Any],
    round_id: str,
    start_ms: int,
    end_ms: int,
    start_month: str,
    end_month: str,
    workers: int,
    out_dir: Path,
    log_path: Path,
    grid_path: Path | None = None,
    scenarios: Sequence[str] = (
        "cost2x",
        "nohalt",
        "delay1",
        "shuffle",
        "long_only",
        "short_only",
    ),
    with_flow: bool = False,
    with_premium: bool = False,
    exec_tiers: Mapping[str, tuple[SizingConfig, RiskLimits]] | None = None,
) -> dict[str, Any]:
    """评估一轮的完整网格，写出报告并登记试验（docs/13 §6、§7、§9）。

    Args:
        config: 研究配置。
        sizing: 风险包络（已按档位取值；初始权益在这里改写为两种口径）。
        fee_taker: 吃单费率。
        risk_limits: 风控参数（研究用 reset）。
        grid: 网格配置（`family` / `params` / 可选 `component_grids`）。
        round_id: 轮次 ID。
        start_ms: 研究起点毫秒。
        end_ms: 研究终点毫秒（不含）。
        start_month: 起始月份。
        end_month: 结束月份。
        workers: 并行进程数（≤ 1 表示串行并复用同一份数据）。
        out_dir: `artifacts/reports`。
        log_path: `EXPERIMENTS.log`。
        grid_path: 网格文件（计入 config_hash）。
        scenarios: 情景列表。
        with_flow: 是否加载主动买入成交。
        with_premium: 是否加载溢价指数。

    Returns:
        报告字典（与 `grid.json` 内容一致）。
    """
    family = str(grid["family"])
    exposure = str(grid.get("exposure", "neutral"))
    # 方向需要的数据源自动加载（docs/13 §1、§10）：X3 用溢价指数、X4 用主动买入成交
    with_flow = with_flow or family in _FLOW_FAMILIES
    with_premium = with_premium or family in _PREMIUM_FAMILIES
    params_list = [dict(item) for item in grid["params"]]
    folds = fold_windows(
        start_ms, end_ms, train_months=config.rolling.train_months, folds=config.rolling.folds
    )
    root = Path.cwd()
    component_families: tuple[str, ...] = ()
    mapping = grid.get("component_grids")
    if isinstance(mapping, Mapping) and mapping:
        component_families = tuple(str(name) for name in mapping)
    hashes = compute_hashes(
        config=config,
        root=root,
        grid_path=grid_path,
        extra_configs=[root / "configs" / "sizing_research.yaml", root / "configs" / "base.yaml"],
        family=family,
        component_families=component_families,
    )
    signal_sizing = sizing.model_copy(update={"initial_equity": config.equity.signal})
    if exec_tiers is None:
        # 未给出四个档位时退回单一默认档（调试用；正式评估必须传四档，docs/13 §7 第 9 条）
        exec_tiers = {
            sizing.sizing_mode: (
                sizing.model_copy(update={"initial_equity": config.equity.exec}),
                risk_limits,
            )
        }
    payload = {
        "config": config.model_dump(mode="json"),
        "sizing": signal_sizing.model_dump(mode="json"),
        "fee_taker": fee_taker,
        "risk": {
            "daily_loss_stop": risk_limits.daily_loss_stop,
            "daily_loss_halt": risk_limits.daily_loss_halt,
            "max_drawdown_halt": risk_limits.max_drawdown_halt,
            "max_consecutive_losses": risk_limits.max_consecutive_losses,
            "consecutive_loss_pause_min": risk_limits.consecutive_loss_pause_min,
            "halt_mode": risk_limits.halt_mode,
            "halt_reset_days": risk_limits.halt_reset_days,
        },
        "start_ms": start_ms,
        "end_ms": end_ms,
        "start_month": start_month,
        "end_month": end_month,
        "with_flow": with_flow,
        "with_premium": with_premium,
    }
    market = None
    if workers <= 1:
        market = build_market(
            config,
            start_ms=start_ms,
            end_ms=end_ms,
            start_month=start_month,
            end_month=end_month,
            with_flow=with_flow,
            with_premium=with_premium,
        )
    group_dir = out_dir / round_id / "groups"
    components_by_fold = None
    components_by_mode: dict[str, list[dict[str, dict[str, object]]]] | None = None
    if family == "ensemble":
        # R5 的成分按 R5 本组的仓位模式分组（1 号第五部分 B 第 15 条 k 款）
        modes = sorted({str(params.get("sizing_mode", "fixed_risk")) for params in params_list})
        components_by_mode = {}
        for mode in modes:
            fold_components = component_params_by_fold(
                grid=grid,
                config=config,
                market=market,
                sizing=signal_sizing,
                fee_taker=fee_taker,
                risk_limits=risk_limits,
                payload=payload,
                workers=workers,
                group_dir=group_dir / "components",
                log_path=log_path,
                hashes=hashes,
                round_id=round_id,
                folds=folds,
                start_ms=start_ms,
                end_ms=end_ms,
                root=root,
                sizing_mode=mode,
            )
            if fold_components is None:
                raise ValueError("R5 缺少 component_grids（docs/13 §5）")
            components_by_mode[mode] = fold_components
    groups = ensure_groups(
        market=market,
        config=config,
        sizing=signal_sizing,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        pairs=[(family, params) for params in params_list],
        payload=payload,
        workers=workers,
        group_dir=group_dir,
        log_path=log_path,
        hashes=hashes,
        round_id=round_id,
        folds=folds,
        start_ms=start_ms,
        end_ms=end_ms,
        components_by_fold=components_by_fold,
        components_by_mode=components_by_mode,
    )
    params_by_key = {params_key(params): params for params in params_list}
    try:
        choices = select_choices(
            groups=groups,
            family=family,
            params_by_key=params_by_key,
            folds=folds,
            start_ms=start_ms,
            components_by_fold=components_by_fold,
            components_by_mode=components_by_mode,
        )
    except ValueError as error:
        return _no_selection_report(
            round_id=round_id,
            family=family,
            exposure=exposure,
            folds=folds,
            params_list=params_list,
            groups=groups,
            hashes=hashes,
            failure=str(error),
            out_dir=out_dir,
        )
    return _evaluate_selected(
        config=config,
        family=family,
        exposure=exposure,
        folds=folds,
        choices=choices,
        params_list=params_list,
        params_by_key=params_by_key,
        groups=groups,
        grid=grid,
        market=market,
        signal_sizing=signal_sizing,
        exec_tiers=exec_tiers,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        payload=payload,
        workers=workers,
        group_dir=group_dir,
        log_path=log_path,
        hashes=hashes,
        round_id=round_id,
        start_ms=start_ms,
        end_ms=end_ms,
        scenarios=scenarios,
        out_dir=out_dir,
        root=root,
        components_by_fold=components_by_fold,
        components_by_mode=components_by_mode,
    )


def _evaluate_selected(
    *,
    config: ResearchConfig,
    family: str,
    exposure: str,
    folds: list[Fold],
    choices: list[FoldChoice],
    params_list: Sequence[Mapping[str, object]],
    params_by_key: Mapping[str, Mapping[str, object]],
    groups: Mapping[tuple[str, str], GroupResult],
    grid: Mapping[str, Any],
    market,
    signal_sizing: SizingConfig,
    exec_tiers: Mapping[str, tuple[SizingConfig, RiskLimits]],
    fee_taker: float,
    risk_limits: RiskLimits,
    payload: dict[str, Any],
    workers: int,
    group_dir: Path,
    log_path: Path,
    hashes: RecordHashes,
    round_id: str,
    start_ms: int,
    end_ms: int,
    scenarios: Sequence[str],
    out_dir: Path,
    root: Path,
    components_by_fold: Sequence[Mapping[str, Mapping[str, object]]] | None = None,
    components_by_mode: Mapping[str, Sequence[Mapping[str, Mapping[str, object]]]] | None = None,
) -> dict[str, Any]:
    """按折选中参数跑连续路径、四个档位的 500 USDT 口径与全部情景，并汇总达标项。"""

    def _segments() -> list[Segment]:
        return build_segments(
            family=family,
            folds=folds,
            choices=choices,
            params_by_key=params_by_key,
            config=config,
            components_by_fold=components_by_fold,
            components_by_mode=components_by_mode,
        )

    def _run(
        sizing: SizingConfig,
        options: ScenarioOptions | None = None,
        *,
        tier_risk: RiskLimits | None = None,
        seed: int | None = None,
    ) -> RunResult:
        return run_selected_path(
            market,
            config=config,
            sizing=sizing,
            fee_taker=fee_taker,
            risk_limits=tier_risk or risk_limits,
            segments=_segments(),
            end_ms=end_ms,
            options=options,
            seed=seed,
        )

    signal_path = _run(signal_sizing)
    scenario_stats = {
        name: _block_metrics(_run(signal_sizing, SCENARIO_OPTIONS[name]))
        for name in scenarios
        if name != "shuffle"
    }
    shuffle_sharpes: list[float] | None = None
    if family not in _SHUFFLE_NOT_APPLICABLE and "shuffle" in scenarios:
        # 达标判定用全部种子（docs/13 §7）；第一个种子的结果记为参考
        shuffle_sharpes = []
        for offset in range(config.shuffle.n_seeds):
            seeded = _run(
                signal_sizing,
                SCENARIO_OPTIONS["shuffle"],
                seed=config.shuffle.seed + offset,
            )
            shuffle_sharpes.append(annualized_sharpe(seeded.daily_returns()))
        scenario_stats["shuffle"] = _block_metrics(
            _run(signal_sizing, SCENARIO_OPTIONS["shuffle"], seed=config.shuffle.seed)
        )
    tier_stats: dict[str, dict[str, float]] = {}
    exec_records: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for tier_name, (tier_sizing, tier_risk) in exec_tiers.items():
        tier_path = _run(tier_sizing, tier_risk=tier_risk)
        stats = _block_metrics(tier_path)
        tier_stats[tier_name] = stats
        exec_records.append(
            {
                "ts": now,
                "kind": "exec",
                "round": round_id,
                "family": family,
                "tier": tier_name,
                "params": {
                    str(fold.index + 1): choice.params_key
                    for fold, choice in zip(folds, choices, strict=True)
                },
                "daily_sharpe": float(stats["daily_sharpe"]),
                "annualized_sharpe": float(stats["annualized_sharpe"]),
                "trades": int(stats["trades"]),
                "equity_basis": config.equity.exec,
                **hashes.as_dict(),
            }
        )
    append_experiments(log_path, exec_records)
    trial_main, trial_sensitivity = trial_sharpes_for(round_id, log_path, out_dir)
    metrics = summarize(signal_path, bootstrap=config.bootstrap, trial_sharpes=trial_main)
    dsr_sensitivity = signal_path.deflated(trial_sensitivity)
    fold_stats = _fold_stats(signal_path, folds, choices)
    short_excess = None
    if family in _SHORT_ONLY_FAMILIES:
        short_excess = _short_excess_sharpe(
            signal_path.daily_returns(), _x0_reference_daily(root, out_dir)
        )
    checks = _checks(
        family=family,
        exposure=exposure,
        base_sharpe=float(metrics["annualized_sharpe"]),
        metrics=metrics,
        fold_stats=fold_stats,
        scenario_stats=scenario_stats,
        shuffle_sharpes=shuffle_sharpes,
        exec_tiers=tier_stats,
        short_excess_sharpe=short_excess,
    )
    qualified_tiers = [
        name
        for name, stats in tier_stats.items()
        if _tier_passes(stats, float(metrics["annualized_sharpe"]))
    ]
    report: dict[str, Any] = {
        "round": round_id,
        "family": family,
        "exposure": exposure,
        "generated_at": now,
        "hashes": hashes.as_dict(),
        "equity_basis": {"signal": config.equity.signal, "exec": config.equity.exec},
        "shuffle_sharpes": shuffle_sharpes,
        "short_excess_sharpe": short_excess,
        "folds": [
            {
                "index": fold.index + 1,
                "start": datetime.fromtimestamp(fold.start_ms // 1000, tz=UTC).date().isoformat(),
                "end": datetime.fromtimestamp(fold.end_ms // 1000, tz=UTC).date().isoformat(),
            }
            for fold in folds
        ],
        "params": _param_rows(
            family=family, params_list=params_list, groups=groups, choices=choices
        ),
        "fold_stats": fold_stats,
        "selected_params": {
            str(fold.index + 1): choice.params_key
            for fold, choice in zip(folds, choices, strict=True)
        },
        "component_params": components_by_mode
        if components_by_mode is not None
        else components_by_fold,
        "segments": signal_path.segments,
        "selected_metrics": metrics,
        "dsr_sensitivity": dsr_sensitivity,
        "exec_tiers": tier_stats,
        "qualified_exec_tiers": qualified_tiers,
        "scenarios": scenario_stats,
        "checks": checks,
        "passed": all(checks.values()),
    }
    return _write_report(
        round_id=round_id,
        family=family,
        exposure=exposure,
        folds=folds,
        report=report,
        concat_daily=signal_path.daily_returns(),
        out_dir=out_dir,
    )
