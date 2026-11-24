"""研究流水线：网格评估、试验登记与轮次报告（docs/13 §7、§9）。

- 每组参数：全期跑一次得到训练曲线（折内选参用），再从第 1 折起点连续跑一次得到
  “自身拼接曲线”（登记试验用）。
- 拼接曲线是一条连续路径：以初始权益、空仓、空风控状态从第 1 折起点开始，折边界换参数，
  路径内的风控事件照常延续（1 号第五部分 B 第 10 条 a~e）。
- 试验登记含 code_hash / data_hash / config_hash / equity_basis；逐组落盘并可断点续跑；
  脚本只写 EXPERIMENTS.log，不写 ledger（1 号第三部分第 5 条、B 第 13 条）。
"""

from __future__ import annotations

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
from trade.research.metrics import annualized_sharpe, summarize
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
# 可执行性检查：丢弃比例上限与夏普下限比例（1 号第七部分第 5 条 i 款）
_EXEC_DROP_RATIO_MAX = 0.20
_EXEC_SHARPE_RATIO_MIN = 0.70


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
    )


def _worker_task(task: dict[str, Any]) -> ParamRun:
    return run_param(
        _CTX["market"],
        config=_CTX["config"],
        sizing=_CTX["sizing"],
        fee_taker=_CTX["fee_taker"],
        risk_limits=_CTX["risk"],
        family=str(task["family"]),
        params=task["params"],
        start_ms=int(task.get("start_ms", _CTX["start_ms"])),
        end_ms=int(task.get("end_ms", _CTX["end_ms"])),
        options=ScenarioOptions(**task["options"]),
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
        return [
            run_param(
                market,
                config=ResearchConfig.model_validate(payload["config"]),
                sizing=SizingConfig.model_validate(payload["sizing"]),
                fee_taker=float(payload["fee_taker"]),
                risk_limits=RiskLimits(**payload["risk"]),
                family=str(task["family"]),
                params=task["params"],
                start_ms=int(task.get("start_ms", payload["start_ms"])),
                end_ms=int(task.get("end_ms", payload["end_ms"])),
                options=ScenarioOptions(**task["options"]),
            )
            for task in tasks
        ]
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


def compute_hashes(
    *,
    config: ResearchConfig,
    root: Path,
    grid_path: Path | None,
    extra_configs: Sequence[Path] = (),
) -> RecordHashes:
    """计算 code_hash / data_hash / config_hash（1 号第三部分第 5 条）。

    Args:
        config: 研究配置（取 manifest 与数据根）。
        root: 仓库根。
        grid_path: 本次网格文件。
        extra_configs: 其他参与计算的配置文件（例如仓位配置、费率配置）。

    Returns:
        RecordHashes。

    Raises:
        FileNotFoundError: 缺少 manifest 或配置文件（红线 12）。
    """
    code_files = [
        *(root / "src" / "trade" / "research").rglob("*.py"),
        *(root / "src" / "trade" / "strategies").rglob("*.py"),
        # 风控（R5~R8）与仓位（取整、最小下单额）是研究、最终检验、影子盘与实盘共用的执行口径
        # （AGENTS.md 红线 4）：改动它们同样改变结果，因此必须进入 code_hash。
        *(root / "src" / "trade" / "common").rglob("*.py"),
        root / "scripts" / "portfolio_backtest.py",
    ]
    manifest = root / config.paths.data_root / "history" / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"缺少数据 manifest：{manifest}（红线 12）")
    config_files = [root / "configs" / "research.yaml", *extra_configs]
    if grid_path is not None:
        config_files.append(grid_path)
    missing = [str(path) for path in config_files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少配置文件：{missing}（红线 12）")
    return RecordHashes(
        code_hash=_hash_files([path for path in code_files if path.is_file()]),
        data_hash=hashlib.sha256(manifest.read_bytes()).hexdigest(),
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
    return GroupResult(
        family=str(payload["family"]),
        params=dict(payload["params"]),
        train_daily=_series_from_json(train["daily"]),
        train_entries=[int(ts) for ts in train["entries"]],
        train_meta={key: float(value) for key, value in train["meta"].items()},
        own_daily=_series_from_json(own["daily"]),
        own_meta={key: float(value) for key, value in own["meta"].items()},
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

    Returns:
        {(family, params_key): GroupResult}。

    Raises:
        ValueError: 参数重复（同一 family 下同一组参数出现两次）。
    """
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
    for family, params in fresh:
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
    runs = _run_tasks(tasks, payload=payload, workers=workers, market=market)
    entries: list[dict[str, Any]] = []
    now = datetime.now(UTC).isoformat()
    for index, (family, params) in enumerate(fresh):
        train = runs[2 * index]
        own = runs[2 * index + 1]
        group = GroupResult(
            family=family,
            params=params,
            train_daily=train.daily,
            train_entries=[int(trade.entry_ts) for trade in train.trades],
            train_meta=_group_meta(train, prefix="train_"),
            own_daily=own.daily,
            own_meta=_group_meta(own, prefix="own_"),
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
) -> list[Segment]:
    """按折构造连续路径的段（折边界换参数，不在边界额外强平，docs/13 §6）。"""
    segments: list[Segment] = []
    for index, (fold, choice) in enumerate(zip(folds, choices, strict=True)):
        components = None if components_by_fold is None else components_by_fold[index]
        params = dict(params_by_key[choice.params_key])
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
                sizing_mode=str(params.get("sizing_mode", "fixed_risk")),
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
) -> RunResult:
    """以给定仓位口径与初始权益连续运行整条拼接路径（docs/13 §6 第 10 条 b 款）。"""
    engine = Engine(
        market=market,
        sizing_cfg=sizing,
        exec_cfg=config.execution,
        portfolio_cfg=config.portfolio,
        fee_taker=fee_taker,
        risk_limits=risk_limits,
        options=options or ScenarioOptions(),
        seed=config.bootstrap.seed,
    )
    return engine.run_path(list(segments), int(end_ms))


def _component_param_sets(
    root: Path, mapping: Mapping[str, object]
) -> dict[str, list[dict[str, object]]]:
    """读取 R5 成分网格的参数集合（去掉 `sizing_mode` 维度并去重）。

    1 号第八部分开头要求 R1~R10 的网格乘以仓位模式 {A, B}；R5 的仓位模式由 R5 自己的网格决定，
    成分方向只提供信号参数，因此这里的 `sizing_mode` 不参与逐折选参。

    Args:
        root: 仓库根目录（解析 `component_grids` 的相对路径）。
        mapping: `{family: 网格文件相对路径}`。

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
            params = {key: value for key, value in dict(item).items() if key != "sizing_mode"}
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
) -> list[dict[str, dict[str, object]]] | None:
    """R5 的成分参数逐折选择（1 号第八部分 R5、docs/13 §5）。

    每个成分方向取其第一次网格、只用该折开始之前的数据按年化夏普选参；成分参数的评估已在各自
    轮次登记，这里复用结果、不重复登记试验。

    Returns:
        每折一个 `{family: params}` 映射；网格没有 `component_grids` 时返回 None。
    """
    mapping = grid.get("component_grids")
    if not isinstance(mapping, Mapping) or not mapping:
        return None
    params_by_family = _component_param_sets(root, mapping)
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


def _delay1_max_excess(grid: Mapping[str, Any]) -> float:
    """读取网格的 delay1 容忍度（docs/13 §7；1 号第八部分 R11/R12）。

    默认 0 表示 g 款的严格要求 `delay1 年化夏普 < base`；正数表示按登记的例外口径
    `delay1 年化夏普 ≤ base + 容忍度`（R11、R12 登记为 0.10）。

    Args:
        grid: 网格配置。

    Returns:
        非负容忍度（年化夏普）。

    Raises:
        ValueError: 字段不是非负数。
    """
    value = grid.get("delay1_max_excess", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0.0:
        raise ValueError(f"网格 delay1_max_excess 必须是非负数：{grid.get('round')} {value!r}")
    return float(value)


def _checks(
    *,
    exposure: str,
    base_sharpe: float,
    metrics: Mapping[str, object],
    fold_stats: list[dict[str, Any]],
    scenario_stats: Mapping[str, Mapping[str, float]],
    exec_stats: Mapping[str, float],
    delay1_max_excess: float = 0.0,
) -> dict[str, bool]:
    """达标项 a~h 与 j 判定（第七部分第 5 条，信号口径），返回逐项布尔值。

    Args:
        delay1_max_excess: delay1 容忍度（年化夏普），0 表示要求低于 base。
    """
    delay1_sharpe = scenario_stats.get("delay1", {}).get("annualized_sharpe", 1.0)
    checks: dict[str, bool] = {
        "sharpe_ge_1": base_sharpe >= 1.0,
        "folds_positive_ge_4": sum(1 for item in fold_stats if item["net_return"] > 0.0) >= 4,
        "bootstrap_lower_gt_0": float(metrics["bootstrap"]["lower_bp"]) > 0.0,  # type: ignore[index]
        "dsr_ge_080": float(metrics["deflated_sharpe"]) >= 0.80,
        "cost2x_positive": scenario_stats.get("cost2x", {}).get("net_return", -1.0) > 0.0,
        "mdd_le_020": float(metrics["max_drawdown"]) <= 0.20,
        "shuffle_le_03": scenario_stats.get("shuffle", {}).get("annualized_sharpe", 1.0) <= 0.3,
        "delay1_lt_base": (
            delay1_sharpe <= base_sharpe + delay1_max_excess
            if delay1_max_excess > 0.0
            else delay1_sharpe < base_sharpe
        ),
    }
    if exposure == "neutral":
        checks["side_condition"] = (
            sum(1 for item in fold_stats if item["long_gross"] - item["short_gross"] > 0.0) >= 4
        )
        # j 款：中性策略的实现 β 与 alpha 夏普（不能主要靠大盘涨跌）
        checks["beta_abs_le_030"] = abs(float(metrics["realized_beta"])) <= 0.30
        checks["alpha_sharpe_ge_08"] = float(metrics["alpha_sharpe"]) >= 0.8
    else:
        checks["side_condition"] = (
            scenario_stats.get("long_only", {}).get("net_pnl", 0.0) > 0.0
            or scenario_stats.get("short_only", {}).get("net_pnl", 0.0) > 0.0
        )
    return checks


def _executable_checks(
    *, signal_metrics: Mapping[str, object], exec_stats: Mapping[str, float]
) -> dict[str, bool]:
    """可执行性检查（i 款，500 USDT 口径，用同一选参结果重跑拼接路径）。"""
    attempted = exec_stats.get("attempted", 0.0)
    dropped = exec_stats.get("dropped_min_notional", 0.0)
    ratio = dropped / attempted if attempted > 0.0 else 0.0
    signal_sharpe = float(signal_metrics["annualized_sharpe"])
    return {
        "exec_drop_ratio_le_020": ratio <= _EXEC_DROP_RATIO_MAX,
        "exec_net_positive": exec_stats.get("net_return", -1.0) > 0.0,
        "exec_sharpe_ge_70pct": float(exec_stats.get("annualized_sharpe", 0.0))
        >= _EXEC_SHARPE_RATIO_MIN * signal_sharpe,
    }


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
        "exec_metrics": {},
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
    params_list = [dict(item) for item in grid["params"]]
    folds = fold_windows(
        start_ms, end_ms, train_months=config.rolling.train_months, folds=config.rolling.folds
    )
    root = Path.cwd()
    hashes = compute_hashes(
        config=config,
        root=root,
        grid_path=grid_path,
        extra_configs=[root / "configs" / "sizing_research.yaml", root / "configs" / "base.yaml"],
    )
    signal_sizing = sizing.model_copy(update={"initial_equity": config.equity.signal})
    exec_sizing = sizing.model_copy(update={"initial_equity": config.equity.exec})
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
    )
    params_by_key = {params_key(params): params for params in params_list}
    train_runs = {key: groups[(family, key)].as_param_run() for key in params_by_key}
    try:
        choices = [select_for_fold(train_runs, fold, start_ms) for fold in folds]
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
        exec_sizing=exec_sizing,
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
    exec_sizing: SizingConfig,
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
) -> dict[str, Any]:
    """按折选中参数跑连续路径、两种权益口径与全部情景，并汇总达标项。"""
    components_by_fold = None
    if family == "ensemble":
        components_by_fold = component_params_by_fold(
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
        )

    def _segments() -> list[Segment]:
        return build_segments(
            family=family,
            folds=folds,
            choices=choices,
            params_by_key=params_by_key,
            config=config,
            components_by_fold=components_by_fold,
        )

    def _run(sizing: SizingConfig, options: ScenarioOptions | None = None) -> RunResult:
        return run_selected_path(
            market,
            config=config,
            sizing=sizing,
            fee_taker=fee_taker,
            risk_limits=risk_limits,
            segments=_segments(),
            end_ms=end_ms,
            options=options,
        )

    signal_path = _run(signal_sizing)
    exec_path = _run(exec_sizing)
    scenario_stats = {
        name: _block_metrics(_run(signal_sizing, SCENARIO_OPTIONS[name])) for name in scenarios
    }
    metrics = summarize(
        signal_path, bootstrap=config.bootstrap, trial_sharpes=read_trial_sharpes(log_path)
    )
    fold_stats = _fold_stats(signal_path, folds, choices)
    exec_stats = _block_metrics(exec_path)
    delay1_max_excess = _delay1_max_excess(grid)
    checks = _checks(
        exposure=exposure,
        base_sharpe=float(metrics["annualized_sharpe"]),
        metrics=metrics,
        fold_stats=fold_stats,
        scenario_stats=scenario_stats,
        exec_stats=exec_stats,
        delay1_max_excess=delay1_max_excess,
    )
    checks.update(_executable_checks(signal_metrics=metrics, exec_stats=exec_stats))
    append_experiments(
        log_path,
        [
            {
                "ts": datetime.now(UTC).isoformat(),
                "kind": "exec",
                "round": round_id,
                "family": family,
                "params": {
                    str(fold.index + 1): choice.params_key
                    for fold, choice in zip(folds, choices, strict=True)
                },
                "daily_sharpe": float(exec_stats["daily_sharpe"]),
                "annualized_sharpe": float(exec_stats["annualized_sharpe"]),
                "trades": int(exec_stats["trades"]),
                "equity_basis": config.equity.exec,
                **hashes.as_dict(),
            }
        ],
    )
    report: dict[str, Any] = {
        "round": round_id,
        "family": family,
        "exposure": exposure,
        "generated_at": datetime.now(UTC).isoformat(),
        "hashes": hashes.as_dict(),
        "equity_basis": {"signal": config.equity.signal, "exec": config.equity.exec},
        "delay1_max_excess": delay1_max_excess,
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
        "component_params": components_by_fold,
        "segments": signal_path.segments,
        "selected_metrics": metrics,
        "exec_metrics": exec_stats,
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
