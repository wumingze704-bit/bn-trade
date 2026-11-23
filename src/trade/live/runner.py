"""通用低频实盘引擎主循环（shadow / paper / live）。

规格：docs/07-实盘与风控.md §4、§6

调度：每个整点后 `rebalance_delay_sec` 秒拉取刚收盘的 1h K 线；到策略声明的
调仓时刻做决策并执行差额，其余整点只巡检（止损是否齐全、净敞口、风控状态、
下架检查）；每 `heartbeat_minutes` 分钟写一次心跳。

判断持仓一律以交易所（paper / live）或状态库虚拟持仓（shadow）为准，不依赖本地记忆。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import zlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from trade.common.risk_rules import RiskLimits
from trade.common.sizing import SizingConfig
from trade.live.candidate import CandidateSpec
from trade.live.control import effective_sizing_from_store
from trade.live.execution import (
    ExchangeExecutor,
    ExecutionError,
    ExecutionReport,
    ShadowExecutor,
    SymbolRules,
    estimate_slip,
)
from trade.live.feed import FeedError, LiveFeed
from trade.live.notifier.dispatcher import NotifierDispatcher
from trade.live.orders import round_step_qty
from trade.live.riskguard import RiskGuard
from trade.live.stops import StopError, StopManager, stop_trigger_price
from trade.live.store import Store
from trade.live.targets import TradeIntent, held_directions, net_exposure, plan_delta
from trade.research.config import ExecutionConfig, PortfolioConfig
from trade.research.data import HOUR_MS
from trade.research.interface import Strategy
from trade.research.portfolio import plan_positions
from trade.research.view import View
from trade.strategies.registry import build_strategy

_MS_PER_MINUTE = 60_000
_BP = 1e-4
_VERSION = "p5-1"


class RunnerError(RuntimeError):
    """引擎启动条件不满足或运行中出现不可恢复错误。"""


def load_risk_limits(sizing: SizingConfig, backtest_path: Path) -> RiskLimits:
    """从 sizing 与 backtest.yaml 的 risk 段构造风控参数（docs/13 §2）。"""
    try:
        values = yaml.safe_load(backtest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RunnerError(f"无法读取 {backtest_path}: {error}") from error
    risk = values.get("risk") if isinstance(values, dict) else None
    if not isinstance(risk, dict):
        raise RunnerError(f"backtest 配置缺少 risk 段：{backtest_path}")
    return RiskLimits(
        daily_loss_stop=sizing.daily_loss_stop,
        daily_loss_halt=sizing.daily_loss_halt,
        max_drawdown_halt=sizing.max_drawdown_halt,
        max_consecutive_losses=sizing.max_consecutive_losses,
        consecutive_loss_pause_min=sizing.consecutive_loss_pause_min,
        halt_mode=str(risk["halt_mode"]),
        halt_reset_days=int(risk["halt_reset_days"]),
    )


def load_strategy(spec: CandidateSpec, *, stop_max: float) -> Strategy:
    """按候选的 family 与冻结参数构造策略（红线 4：与研究报告同一份代码）。"""
    expected = "src/trade/research"
    if spec.framework_dir.strip("/") != expected:
        raise RunnerError(
            f"候选 {spec.candidate_id} 的 framework_dir={spec.framework_dir}，"
            f"当前仓库只有 {expected}"
        )
    # 显式导入框架模块，确保引擎与研究使用同一份接口与组合层
    importlib.import_module("trade.research.interface")
    importlib.import_module("trade.research.portfolio")
    return build_strategy(spec.family, spec.params, stop_max=stop_max)


def rebalance_hour(hour_id: int, rebalance_hours: int) -> bool:
    """判断某个 UTC 小时是否为调仓时刻（对齐 8h → 00/08/16）。"""
    return (hour_id * HOUR_MS) % (rebalance_hours * HOUR_MS) == 0


def next_rebalance_ms(now_ms: int, rebalance_hours: int) -> int:
    """返回下一个调仓整点（UTC 毫秒）。"""
    period = rebalance_hours * HOUR_MS
    return (now_ms // period + 1) * period


def alias_for(symbol: str) -> str:
    """把交易对转为订单 ID 中可用的 ASCII 别名（≤ 10 字符）。"""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    cleaned = "".join(ch for ch in base.upper() if ch.isalnum())
    return cleaned[:10] or "SYM"


class Runner:
    """低频引擎主循环（docs/07 §4、§6）。

    Args:
        mode: `shadow` / `paper` / `live`。
        config: `configs/live.yaml`。
        root: 仓库根目录。
        spec: 候选冻结参数。
        entry: 总账中的候选条目。
        store: 状态库。
        feed: 实盘行情缓存。
        sizing: 研究期风险包络（`configs/sizing_research.yaml`）。
        exec_cfg: 执行成本参数（`configs/research.yaml: execution`）。
        portfolio_cfg: 组合层上限（`configs/research.yaml: portfolio`）。
        fee_taker: 吃单费率（`configs/base.yaml: fees.taker`）。
        risk: 风控状态机。
        client: 交易所客户端（paper / live）。
        notifier: 通知分发器（可选）。
        now_ms: 当前 UTC 毫秒时间函数。
        sleep: 异步等待函数。
    """

    def __init__(
        self,
        *,
        mode: str,
        config: Any,
        root: Path,
        spec: CandidateSpec,
        entry: Mapping[str, Any],
        store: Store,
        feed: LiveFeed,
        sizing: SizingConfig,
        exec_cfg: ExecutionConfig,
        portfolio_cfg: PortfolioConfig,
        fee_taker: float,
        risk: RiskGuard,
        client: Any = None,
        notifier: NotifierDispatcher | None = None,
        now_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if mode not in {"shadow", "paper", "live"}:
            raise RunnerError(f"未知运行模式：{mode}")
        self.mode = mode
        self._config = config
        self._root = root
        self.candidate = spec
        self._entry = dict(entry)
        self._store = store
        self._feed = feed
        self._sizing = sizing
        self._exec_cfg = exec_cfg
        self._portfolio_cfg = portfolio_cfg
        self._fee_taker = fee_taker
        self._risk = risk
        self._client = client
        self._notifier = notifier
        self._now_ms = now_ms or (lambda: int(datetime.now(tz=UTC).timestamp() * 1000))
        self._sleep = sleep
        self._strategy = load_strategy(spec, stop_max=exec_cfg.stop_max)
        if self._strategy.rebalance_hours != spec.rebalance_hours:
            raise RunnerError(
                f"候选 rebalance_hours={spec.rebalance_hours} 与策略声明 "
                f"{self._strategy.rebalance_hours} 不一致"
            )
        self._engine_cfg = config.engine
        self._executor: ExchangeExecutor | None = None
        self._stops: StopManager | None = None
        if mode != "shadow":
            if client is None:
                raise RunnerError("paper / live 模式需要交易所客户端")
            self._executor = ExchangeExecutor(
                client,
                now_ms=self._now_ms,
                sleep=sleep,
                wait_sec=self._engine_cfg.post_only_wait_sec,
                tick_buffer=self._engine_cfg.ioc_tick_buffer,
                fee_rate=fee_taker,
            )
            self._stops = StopManager(client)
        self._blocked_until: dict[str, int] = {}
        self._pending_shadow: list[tuple[TradeIntent, str, float]] = []
        self._pending_shadow_hour: int | None = None
        self._last_hour_id: int | None = None
        self._last_heartbeat_ms = 0
        self._last_rebalance_ms: int | None = None
        self._started_ms = 0
        self._shadow_fills = 0
        self._last_report_day: str | None = None

    async def run(self) -> None:
        """启动并进入主循环（正常退出只发生在外部停机信号）。"""
        await self.startup()
        while True:
            await self.tick_once(self._now_ms())
            await self._sleep(float(self._engine_cfg.loop_sleep_sec))

    async def startup(self) -> None:
        """启动检查、回补行情、恢复风控状态并核对交易所侧止损。"""
        self._started_ms = self._now_ms()
        await self._startup_exchange_checks()
        symbols = self._symbols(self._now_ms())
        await self._feed.warmup(symbols, self._now_ms())
        self._restore_risk()
        await self._reconcile_stops(self._now_ms())
        self._write_status(self._now_ms(), note="started")
        await self._notify("start", {"候选": self.candidate.candidate_id, "模式": self.mode})

    async def tick_once(self, now_ms: int) -> None:
        """执行一次循环节拍（测试可直接调用）。

        Args:
            now_ms: 当前 UTC 毫秒时间。
        """
        await self._maybe_heartbeat(now_ms)
        hour_id = now_ms // HOUR_MS
        if self._last_hour_id is None:
            self._last_hour_id = hour_id - 1
        if hour_id <= self._last_hour_id:
            return
        delay_ms = self._engine_cfg.rebalance_delay_sec * 1000
        if now_ms < hour_id * HOUR_MS + delay_ms:
            return
        self._last_hour_id = hour_id
        symbols = self._symbols(now_ms)
        try:
            await self._feed.refresh(symbols, now_ms)
            self._risk.set_data_stale(False)
        except FeedError as error:
            self._risk.set_data_stale(True)
            await self._alert(f"行情拉取失败：{error}")
        await self._settle_shadow(hour_id)
        await self._patrol(now_ms)
        if rebalance_hour(hour_id, self._strategy.rebalance_hours):
            await self._rebalance(now_ms)
        self._write_status(now_ms)
        await self._maybe_daily_report(now_ms)

    async def _startup_exchange_checks(self) -> None:
        """启动时核对时钟、费率、逐仓 / 单向 / 杠杆与 API Key 权限（docs/07 §5）。"""
        if self.mode == "shadow":
            return
        if self._client is None:
            raise RunnerError("paper / live 模式缺少交易所客户端")
        drift = await self._client.sync_time_offset()
        self._risk.set_clock_drift(drift)
        if abs(drift) > self._engine_cfg.clock_drift_ms:
            raise RunnerError(f"启动时钟漂移 {drift}ms 超过上限（07 §2）")
        try:
            fee = await self._client.commission_rate("BTCUSDT")
        except Exception as error:  # noqa: BLE001 - 启动阶段直接失败并带上下文
            raise RunnerError(f"读取手续费率失败：{error}") from error
        if fee.taker_commission_rate > self._fee_taker * self._engine_cfg.fee_reject_ratio:
            raise RunnerError(
                f"实际吃单费率 {fee.taker_commission_rate} > 配置 {self._fee_taker} × "
                f"{self._engine_cfg.fee_reject_ratio}，拒绝启动（docs/07 §5）"
            )
        symbols = set(self._feed.rules()) | set(self._positions_now()[0]) | {"BTCUSDT"}
        for symbol in sorted(symbols):
            rows = await self._client.position_risk(symbol)
            for item in rows:
                margin_type = str(item.get("marginType", "")).upper()
                if margin_type and margin_type != "ISOLATED":
                    raise RunnerError(f"{symbol} 不是逐仓（marginType={margin_type}），拒绝启动")
                leverage = int(float(item.get("leverage", 0) or 0))
                if leverage not in (0, self._sizing.leverage):
                    raise RunnerError(
                        f"{symbol} 杠杆为 {leverage}x，与风险包络 {self._sizing.leverage}x 不一致"
                        "（杠杆只能由使用者决定，红线 5）"
                    )
        if await self._client.dual_side_position():
            raise RunnerError("账户为双向持仓模式，引擎只支持单向持仓")
        if self.mode == "live":
            restrictions = await self._client.api_restrictions()
            if bool(restrictions.get("enableWithdrawals", False)):
                raise RunnerError("API Key 开启了提现权限，拒绝启动（红线 6）")
            if not bool(restrictions.get("ipRestrict", False)):
                raise RunnerError("API Key 没有设置 IP 白名单，拒绝启动（红线 6）")
            if not bool(restrictions.get("enableFutures", True)):
                raise RunnerError("API Key 未开启合约权限，拒绝启动")

    def _symbols(self, now_ms: int) -> set[str]:
        """当前需要行情的币：当月选币 ∪ BTC/ETH ∪ 已有持仓 ∪ 已加载过的币。"""
        symbols = set(self._feed.hourly)
        try:
            symbols |= set(self._feed.market().universe.symbols_at(now_ms))
        except KeyError as error:
            raise RunnerError(f"缺少当月选币快照：{error}") from error
        symbols |= {"BTCUSDT", "ETHUSDT"}
        symbols |= set(self._positions_now()[0])
        return symbols

    def _prices(self) -> dict[str, float]:
        """已收盘 1h 的最近收盘价（用于名义与敞口统计）。"""
        prices: dict[str, float] = {}
        for symbol, bars in self._feed.hourly.items():
            if bars.close.size:
                prices[symbol] = float(bars.close[-1])
        return prices

    def _positions_now(self) -> tuple[dict[str, float], dict[str, float]]:
        """同步读取当前持仓（shadow 从状态库，paper / live 从交易所）。

        本方法只给同步调用方（启动检查 / 心跳）使用；paper / live 的市场循环使用
        `_positions_async`，因为它必须等待 REST。
        """
        if self.mode != "shadow":
            return {}, {}
        return self._shadow_positions()

    def _shadow_positions(self) -> tuple[dict[str, float], dict[str, float]]:
        qtys: dict[str, float] = {}
        entries: dict[str, float] = {}
        for row in self._store.list_shadow_positions(self.candidate.candidate_id):
            qty = float(row["qty"]) * int(row["side"])
            qtys[str(row["symbol"])] = qty
            entries[str(row["symbol"])] = float(row["entry_price"])
        return qtys, entries

    async def _positions_async(self) -> tuple[dict[str, float], dict[str, float]]:
        """读取当前持仓（带方向的交易所快照或影子盘虚拟持仓）。"""
        if self.mode == "shadow":
            return self._shadow_positions()
        if self._client is None:
            raise RunnerError("paper / live 模式缺少交易所客户端")
        qtys: dict[str, float] = {}
        entries: dict[str, float] = {}
        for item in await self._client.position_risk():
            qty = float(item.get("positionAmt", 0.0))
            if abs(qty) <= 1e-9:
                continue
            symbol = str(item.get("symbol", ""))
            qtys[symbol] = qty
            entries[symbol] = float(item.get("entryPrice", 0.0))
        return qtys, entries

    async def _equity(self, positions: Mapping[str, float]) -> float:
        """盯市权益：钱包余额 + 未实现盈亏（shadow 用虚拟账户）。"""
        prices = self._prices()
        if self.mode == "shadow":
            snapshot = self._store.latest_equity_snapshot()
            wallet = (
                float(snapshot["wallet_balance"])
                if snapshot is not None
                else float(self._sizing.initial_equity)
            )
            unrealized = sum(
                (prices.get(symbol, 0.0) - self._shadow_positions()[1].get(symbol, 0.0)) * qty
                for symbol, qty in positions.items()
                if symbol in prices
            )
            return wallet + unrealized
        if self._client is None:
            raise RunnerError("paper / live 模式缺少交易所客户端")
        account = await self._client.account()
        return float(account.total_wallet_balance) + float(account.total_unrealized_profit)

    def _restore_risk(self) -> None:
        """从状态库恢复当日风控状态与停机标志（重启不丢失峰值与连续止损计数）。"""
        day = datetime.fromtimestamp(self._now_ms() / 1000, tz=UTC).strftime("%Y-%m-%d")
        row = self._store.get_risk_state(day)
        halt = self._store.get_engine_halt(candidate=self.candidate.candidate_id, mode=self.mode)
        halted = bool(halt["halted"]) if halt is not None else False
        if row is None:
            return
        self._risk.restore(
            peak_equity=float(row.get("peak_equity") or 0.0),
            day=day,
            day_start_equity=float(row.get("day_start_equity") or 0.0),
            consecutive_losses=int(row.get("consecutive_losses") or 0),
            halted=halted,
            halt_reason=halt["reason"] if halt is not None else None,
        )

    async def _reconcile_stops(self, now_ms: int) -> None:
        """启动 / 巡检：每个持仓必须有交易所侧止损；缺失立即平掉该仓位（红线 7）。"""
        if self.mode == "shadow":
            return
        if self._client is None or self._stops is None:
            raise RunnerError("paper / live 模式缺少客户端或止损管理器")
        qtys, entries = await self._positions_async()
        if not qtys:
            return
        stored = {row["symbol"]: row for row in self._store.open_positions()}
        for symbol, qty in qtys.items():
            if symbol not in stored:
                # 没有本引擎记录的仓位（人工或残留）：无止损依据，直接平掉
                await self._close_symbol(symbol, qty, now_ms, reason="无止损依据的持仓")
                continue
            orders = await self._client.algo_open_orders(symbol)
            if not orders:
                await self._alert(f"{symbol} 持仓缺少交易所止损，立即平仓（红线 7）")
                await self._close_symbol(symbol, qty, now_ms, reason="止损缺失")
        self._store.commit()

    async def _maybe_heartbeat(self, now_ms: int) -> None:
        """每 `heartbeat_minutes` 分钟写一次心跳（看门狗依据）。"""
        interval = self._engine_cfg.heartbeat_minutes * _MS_PER_MINUTE
        if now_ms - self._last_heartbeat_ms >= interval:
            self._last_heartbeat_ms = now_ms
            self._write_status(now_ms)

    def _write_status(self, now_ms: int, *, note: str | None = None) -> None:
        """写入引擎心跳与下一次调仓时间；同时写文件供看门狗读取。"""
        next_ms: int | None = None
        if self._last_rebalance_ms is not None:
            next_ms = self._last_rebalance_ms + self._strategy.rebalance_hours * HOUR_MS
        self._store.upsert_engine_status(
            candidate=self.candidate.candidate_id,
            mode=self.mode,
            pid=os.getpid(),
            started_ms=self._started_ms or now_ms,
            heartbeat_ms=now_ms,
            last_rebalance_ms=self._last_rebalance_ms,
            next_rebalance_ms=next_ms,
            version=_VERSION,
            note=note,
        )
        self._store.commit()
        path = self._heartbeat_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(now_ms), encoding="utf-8")

    def _heartbeat_path(self) -> Path:
        """心跳文件路径：`<状态库目录>/<候选ID>-<模式>.heartbeat`。"""
        db_path = Path(self._config.storage.db_path)
        if not db_path.is_absolute():
            db_path = self._root / db_path
        return db_path.parent / f"{self.candidate.candidate_id}-{self.mode}.heartbeat"

    def _minute_label(self, now_ms: int) -> str:
        return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y%m%d%H%M")

    def _rules_for(self, symbol: str) -> SymbolRules:
        meta = self._feed.rules().get(symbol)
        if meta is None or meta.tick_size is None or meta.step_size is None:
            raise RunnerError(f"缺少 {symbol} 的 tickSize / stepSize（红线 12）")
        return SymbolRules(tick_size=float(meta.tick_size), step_size=float(meta.step_size))

    async def _rebalance(self, now_ms: int) -> None:
        """调仓时刻：取目标与实际对账，执行差额并记录调仓明细。"""
        hour_start = (now_ms // HOUR_MS) * HOUR_MS
        qtys, entries = await self._positions_async()
        equity = await self._equity(qtys)
        flatten = self._risk.on_equity(ts_ms=now_ms, equity=equity)
        self._save_risk_state(now_ms, equity)
        control = self._store.get_control_flags(self.candidate.candidate_id)
        if flatten:
            await self._alert(f"{self._risk.state.halt_reason} 触发，平掉全部持仓")
            await self._execute_intents(self._close_intents(qtys), hour_start, now_ms, entries, {})
            self._store.set_engine_halt(
                candidate=self.candidate.candidate_id,
                mode=self.mode,
                halted=True,
                reason=self._risk.state.halt_reason,
            )
            self._store.commit()
            return
        if control["flatten_requested"]:
            await self._execute_intents(self._close_intents(qtys), hour_start, now_ms, entries, {})
            self._store.clear_flatten_request(self.candidate.candidate_id)
            self._store.commit()
            return
        blocked_reason = self._risk.open_blocked_reason(now_ms)
        if control["pause_open"]:
            blocked_reason = "控制页暂停开仓"
        if blocked_reason is not None:
            self._store.insert_event(
                level="warn",
                event=f"本次调仓只平不开：{blocked_reason}",
                symbol=None,
                detail={"candidate": self.candidate.candidate_id},
            )
            self._store.commit()
            self._write_scan(hour_start, [], qtys, reason=blocked_reason)
            return
        view = View(self._feed.market(), hour_start)
        sizing = effective_sizing_from_store(self._store, self._sizing)
        available_margin = await self._available_margin(equity)
        base_equity = equity
        if self.mode == "live":
            base_equity = min(equity, self._engine_cfg.live_capital_cap)
        blocked = {
            symbol: until for symbol, until in self._blocked_until.items() if until > hour_start
        }
        planned, _stats = plan_positions(
            self._strategy.decide(view, held_directions(qtys)),
            view=view,
            equity=base_equity,
            available_margin=available_margin,
            sizing_cfg=sizing,
            exec_cfg=self._exec_cfg,
            portfolio_cfg=self._portfolio_cfg,
            fee_taker=self._fee_taker,
            blocked=blocked,
        )
        step_sizes = {symbol: self._rules_for(symbol).step_size for symbol in self._feed.rules()}
        targets: dict[str, float] = {}
        for position in planned:
            qty = round_step_qty(
                position.notional / position.price_estimate, step_sizes[position.symbol]
            )
            if qty > 0.0:
                targets[position.symbol] = position.side * qty
        intents = plan_delta(qtys, targets, step_sizes=step_sizes)
        sl_map = {position.symbol: position.sl for position in planned}
        reports = await self._execute_intents(intents, hour_start, now_ms, entries, sl_map)
        self._last_rebalance_ms = now_ms
        self._write_scan(hour_start, planned, qtys)
        self._store.insert_rebalance_log(
            candidate=self.candidate.candidate_id,
            mode=self.mode,
            open_time=hour_start,
            finished_ms=self._now_ms(),
            equity=equity,
            long_notional=sum(
                qty * self._prices().get(symbol, 0.0) for symbol, qty in targets.items() if qty > 0
            ),
            short_notional=sum(
                -qty * self._prices().get(symbol, 0.0) for symbol, qty in targets.items() if qty < 0
            ),
            cost_bp=self._cost_bp(reports),
            est_cost_bp=self._estimated_cost_bp(reports, hour_start),
            n_targets=len(targets),
            n_orders=sum(len(report.fills) for report in reports),
            detail={
                "targets": targets,
                "intents": [
                    {"symbol": item.symbol, "qty": item.qty, "reduce_only": item.reduce_only}
                    for item in intents
                ],
                "blocked": blocked_reason,
            },
        )
        self._store.insert_equity_snapshot(
            ts_ms=now_ms,
            wallet_balance=equity,
            unrealized_pnl=0.0,
            available_margin=available_margin,
        )
        self._store.commit()
        await self._notify(
            "open" if intents else "heartbeat",
            {
                "候选": self.candidate.candidate_id,
                "模式": self.mode,
                "目标数": len(targets),
                "订单数": sum(len(report.fills) for report in reports),
                "非金额摘要": "见看板调仓记录",
            },
        )

    async def _available_margin(self, equity: float) -> float:
        """可用保证金：paper / live 取账户，shadow 按权益 × 杠杆。"""
        if self.mode == "shadow":
            return equity * float(self._sizing.leverage) * 0.95
        if self._client is None:
            raise RunnerError("缺少交易所客户端")
        account = await self._client.account()
        return float(account.available_balance)

    def _close_intents(self, qtys: Mapping[str, float]) -> list[TradeIntent]:
        return [
            TradeIntent(symbol, -qty, True)
            for symbol, qty in sorted(qtys.items())
            if abs(qty) > 1e-9
        ]

    async def _execute_intents(
        self,
        intents: list[TradeIntent],
        hour_start: int,
        now_ms: int,
        entries: Mapping[str, float],
        sl_map: Mapping[str, float],
    ) -> list[ExecutionReport]:
        """执行差额：shadow 记入待成交，paper / live 立即下单并挂止损。"""
        if not intents:
            return []
        if self.mode == "shadow":
            self._pending_shadow = [
                (intent, self._minute_label(hour_start), float(sl_map.get(intent.symbol, 0.0)))
                for intent in intents
            ]
            self._pending_shadow_hour = hour_start // HOUR_MS
            self._store.insert_event(
                level="info",
                event=f"影子盘待成交 {len(intents)} 笔（下一根 1h 开盘价）",
                symbol=None,
                detail={"candidate": self.candidate.candidate_id},
            )
            self._store.commit()
            return []
        if self._executor is None or self._client is None or self._stops is None:
            raise RunnerError("paper / live 模式缺少执行器")
        reports: list[ExecutionReport] = []
        minute = self._minute_label(hour_start)
        for seq, intent in enumerate(intents):
            rules = self._rules_for(intent.symbol)
            try:
                report = await self._executor.execute(
                    intent,
                    rules=rules,
                    alias=alias_for(intent.symbol),
                    minute=minute,
                    seq=seq,
                )
            except ExecutionError as error:
                self._risk.record_order_error(now_ms)
                await self._alert(f"{intent.symbol} 执行失败：{error}")
                continue
            reports.append(report)
            self._persist_report(intent, report, entries.get(intent.symbol, 0.0))
            await self._sync_symbol_stop(
                intent.symbol,
                sl=sl_map.get(intent.symbol),
                minute=minute,
                seq=seq,
            )
        self._store.commit()
        return reports

    def _persist_report(
        self, intent: TradeIntent, report: ExecutionReport, entry_price: float
    ) -> None:
        """把成交写入 orders / fills（同一 clientOrderId 只记一次）。"""
        if report.filled_qty <= 0.0:
            return
        self._store.upsert_order(
            client_id=report.fills[0].client_id,
            symbol=intent.symbol,
            side=report.fills[0].side,
            order_type="MIXED",
            price=report.avg_price,
            qty=report.filled_qty,
            status="FILLED",
        )
        side = 1 if intent.qty > 0 else -1
        realized = 0.0
        if intent.reduce_only and entry_price > 0.0:
            realized = (report.avg_price - entry_price) * report.filled_qty * side
            self._add_realized(report.fills[0].ts_ms, realized)
        for fill in report.fills:
            self._store.insert_fill(
                exchange_trade_id=self._trade_id(fill.client_id),
                client_id=fill.client_id,
                symbol=fill.symbol,
                price=fill.price,
                qty=fill.qty,
                fee=fill.fee,
                fee_asset="USDT",
                realized_pnl=realized if len(report.fills) == 1 else 0.0,
                ts_ms=fill.ts_ms,
            )

    @staticmethod
    def _trade_id(client_id: str) -> int:
        """由 clientOrderId 得到稳定的伪成交 ID（状态库唯一约束用）。"""
        return zlib.crc32(client_id.encode("utf-8")) & 0x7FFFFFFF

    async def _sync_symbol_stop(
        self, symbol: str, *, sl: float | None, minute: str, seq: int
    ) -> None:
        """按实际持仓与止损距离挂新止损、替换旧止损；无持仓则撤掉止损。"""
        if self._client is None or self._stops is None:
            return
        qty = 0.0
        entry = 0.0
        for item in await self._client.position_risk(symbol):
            value = float(item.get("positionAmt", 0.0))
            if abs(value) > 1e-9:
                qty = value
                entry = float(item.get("entryPrice", 0.0))
        if abs(qty) <= 1e-9:
            await self._stops.cancel_symbol(symbol)
            return
        if sl is None or entry <= 0.0:
            await self._close_symbol(symbol, qty, self._now_ms(), reason="缺少止损参数")
            return
        rules = self._rules_for(symbol)
        trigger = stop_trigger_price(
            side=1 if qty > 0 else -1, sl=sl, reference_price=entry, tick_size=rules.tick_size
        )
        existing = await self._client.algo_open_orders(symbol)
        old_id = str(existing[0].get("clientAlgoId")) if existing else None
        try:
            await self._stops.ensure(
                symbol=symbol,
                side=1 if qty > 0 else -1,
                qty=abs(qty),
                trigger_price=trigger,
                alias=alias_for(symbol),
                minute=minute,
                seq=seq,
                old_client_algo_id=old_id,
            )
        except StopError as error:
            await self._alert(f"{symbol} 止损挂出失败，立即平仓：{error}")
            await self._close_symbol(symbol, qty, self._now_ms(), reason="止损挂出失败")

    async def _close_symbol(self, symbol: str, qty: float, now_ms: int, *, reason: str) -> None:
        """市价平掉某币全部持仓（reduceOnly）并撤掉止损。"""
        if self._client is None:
            return
        rules = self._rules_for(symbol)
        quantity = round_step_qty(abs(qty), rules.step_size)
        if quantity > 0.0:
            await self._client.place_order(
                symbol=symbol,
                side="SELL" if qty > 0 else "BUY",
                type="MARKET",
                quantity=f"{quantity:.10f}".rstrip("0").rstrip("."),
                reduceOnly="true",
            )
        if self._stops is not None:
            await self._stops.cancel_symbol(symbol)
        self._store.insert_event(
            level="warn",
            event=f"{symbol} 平仓：{reason}",
            symbol=symbol,
            detail={"candidate": self.candidate.candidate_id},
        )
        self._store.commit()

    async def _settle_shadow(self, hour_id: int) -> None:
        """影子盘在下一根 1h 收盘后按该根开盘价成交（与研究框架口径一致）。"""
        if self.mode != "shadow" or not self._pending_shadow:
            return
        if self._pending_shadow_hour is None or hour_id <= self._pending_shadow_hour:
            return
        open_time = self._pending_shadow_hour * HOUR_MS
        view = View(self._feed.market(), open_time + HOUR_MS)
        executor = ShadowExecutor(fee_rate=self._fee_taker)
        top = self._top_symbols(view)
        for intent, _, sl in self._pending_shadow:
            bars = self._feed.hourly.get(intent.symbol)
            if bars is None:
                await self._alert(f"影子盘成交缺少 {intent.symbol} 的 K 线")
                continue
            index = bars.bar_index(open_time)
            if index is None:
                await self._alert(f"影子盘成交缺少 {intent.symbol} 在 {open_time} 的 1h K 线")
                continue
            open_price = float(bars.open[index])
            half_spread = (
                self._exec_cfg.half_spread_top_bp
                if intent.symbol in top
                else self._exec_cfg.half_spread_other_bp
            ) * _BP
            vol = view.hourly_vol(intent.symbol, self._exec_cfg.impact_vol_hours)
            mean_qv = view.mean_quote_volume(intent.symbol, self._exec_cfg.impact_vol_hours)
            slip = estimate_slip(
                notional=abs(intent.qty) * open_price,
                mean_hourly_volume=mean_qv or 0.0,
                hourly_vol=vol or 0.0,
                slip_min_bp=self._exec_cfg.slip_min_bp,
                impact_y=self._exec_cfg.impact_y,
            )
            fill = executor.fill(
                intent,
                open_price=open_price,
                half_spread=half_spread,
                slip=slip,
                ts_ms=open_time,
                client_id=f"sdw-{alias_for(intent.symbol)}-{self._minute_label(open_time)}",
            )
            self._apply_shadow_fill(intent, fill, sl, open_time)
        self._pending_shadow = []
        self._pending_shadow_hour = None
        self._store.commit()

    def _top_symbols(self, view: View) -> frozenset[str]:
        """当月 30 天成交额前 N 的币（决定半价差档位，docs/13 §4）。"""
        from trade.research.portfolio import top_volume_symbols

        return top_volume_symbols(view, view.universe_symbols(), self._exec_cfg.half_spread_top_n)

    def _apply_shadow_fill(self, intent: TradeIntent, fill: Any, sl: float, ts_ms: int) -> None:
        """更新影子盘虚拟持仓与已实现盈亏。"""
        candidate = self.candidate.candidate_id
        rows = {str(row["symbol"]): row for row in self._store.list_shadow_positions(candidate)}
        row = rows.get(intent.symbol)
        realized = 0.0
        if intent.reduce_only:
            if row is None:
                return
            side = int(row["side"])
            entry = float(row["entry_price"])
            opened = float(row["qty"])
            closed = min(opened, fill.qty)
            realized = (fill.price - entry) * closed * side - fill.fee
            remaining = opened - closed
            if remaining <= 1e-9:
                self._store.delete_shadow_position(candidate=candidate, symbol=intent.symbol)
            else:
                self._store.upsert_shadow_position(
                    candidate=candidate,
                    symbol=intent.symbol,
                    side=side,
                    qty=remaining,
                    entry_price=entry,
                    sl=float(row["sl"]),
                    opened_ms=int(row["opened_ms"]),
                )
            self._risk.record_exit(ts_ms=ts_ms, net_pnl=realized, hit_stop_type=False)
            if realized:
                self._add_realized(ts_ms, realized)
        else:
            side = 1 if intent.qty > 0 else -1
            if row is not None and int(row["side"]) == side:
                opened = float(row["qty"])
                entry = float(row["entry_price"])
                qty = opened + fill.qty
                entry = (entry * opened + fill.price * fill.qty) / qty
                opened_ms = int(row["opened_ms"])
            else:
                qty = fill.qty
                entry = fill.price
                opened_ms = ts_ms
            self._store.upsert_shadow_position(
                candidate=candidate,
                symbol=intent.symbol,
                side=side,
                qty=qty,
                entry_price=entry,
                sl=sl or 0.0,
                opened_ms=opened_ms,
            )
        self._store.insert_fill(
            exchange_trade_id=self._trade_id(fill.client_id),
            client_id=fill.client_id,
            symbol=fill.symbol,
            price=fill.price,
            qty=fill.qty,
            fee=fill.fee,
            fee_asset="USDT",
            realized_pnl=realized,
            ts_ms=fill.ts_ms,
        )
        self._store.upsert_order(
            client_id=fill.client_id,
            symbol=fill.symbol,
            side=fill.side,
            order_type="SHADOW",
            price=fill.price,
            qty=fill.qty,
            status="FILLED",
        )
        self._store.insert_event(
            level="info",
            event=f"影子盘成交 {fill.side} {intent.symbol}",
            symbol=intent.symbol,
            detail={"qty": fill.qty, "price": fill.price, "fee": fill.fee},
        )

    def _write_scan(
        self,
        hour_start: int,
        planned: list[Any],
        qtys: Mapping[str, float],
        *,
        reason: str | None = None,
    ) -> None:
        """把本次调仓的目标与过滤结果写入旧看板的 scan 表（调仓记录页数据源）。"""
        now = self._now_ms()
        states = [
            {
                "symbol": position.symbol,
                "alias": alias_for(position.symbol),
                "open_time": hour_start,
                "seq": seq,
                "status": "tradable",
                "prefilter": 1,
                "p": None,
                "m": float(position.side),
                "decision": "open" if reason is None else "skip",
                "skip_reason": reason,
                "spread_bp": None,
                "processed_ms": None,
            }
            for seq, position in enumerate(planned)
        ]
        self._store.write_scan(
            cycle={
                "open_time": hour_start,
                "started_ms": hour_start,
                "finished_ms": now,
                "n_symbols": len({p.symbol for p in planned} | set(qtys)),
                "n_prefilter": len(planned),
                "n_signal": len(planned),
                "n_opened": 0 if reason is not None else len(planned),
            },
            states=states,
        )

    def _save_risk_state(self, now_ms: int, equity: float) -> None:
        """把当日风控状态写入状态库（看板读取；停机标志同步旧表）。"""
        day = datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        row = self._store.get_risk_state(day)
        realized = float(row["realized_pnl"]) if row is not None else 0.0
        halted = 1 if self._risk.halt_flag(now_ms) else 0
        self._store.set_halt_flag(halted, date=day)
        self._store.update_risk_state(
            day,
            realized_pnl=realized,
            peak_equity=max(self._risk.state.peak_equity, equity),
            consecutive_losses=self._risk.state.consecutive_losses,
            safe_mode_reason=self._risk.safe_reason,
        )

    def _add_realized(self, now_ms: int, pnl: float) -> None:
        """累加当日已实现盈亏（影子盘与交易所成交都走这里）。"""
        day = datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        row = self._store.get_risk_state(day)
        realized = (float(row["realized_pnl"]) if row is not None else 0.0) + pnl
        halted = 1 if self._risk.halt_flag(now_ms) else 0
        self._store.set_halt_flag(halted, date=day)
        self._store.update_risk_state(day, realized_pnl=realized)

    def _cost_bp(self, reports: list[ExecutionReport]) -> float | None:
        """实际成交相对最近收盘价的成本（bp，正数为成本）。"""
        prices = self._prices()
        total_notional = 0.0
        total_cost = 0.0
        for report in reports:
            ref = prices.get(report.symbol)
            if not ref or ref <= 0.0 or report.filled_qty <= 0.0:
                continue
            side = 1 if report.fills[0].side == "BUY" else -1
            cost = side * (report.avg_price - ref) / ref
            total_cost += cost * report.notional
            total_notional += report.notional
        if total_notional <= 0.0:
            return None
        return total_cost / total_notional / _BP

    def _estimated_cost_bp(self, reports: list[ExecutionReport], hour_start: int) -> float | None:
        """研究框架的保守成本口径（半价差 + 最小滑点，bp）。"""
        if not reports:
            return None
        return float(self._exec_cfg.half_spread_other_bp + self._exec_cfg.slip_min_bp)

    async def _patrol(self, now_ms: int) -> None:
        """非调仓整点巡检：敞口、下架、控制请求、止损齐全与影子盘止损触发。"""
        qtys, _entries = await self._positions_async()
        prices = self._prices()
        if qtys:
            if not all(symbol in prices for symbol in qtys):
                self._risk.set_data_stale(True)
            else:
                net = net_exposure(qtys, prices)
                equity = await self._equity(qtys)
                if self._risk.update_net_exposure(net=net, equity=equity, ts_ms=now_ms):
                    self._risk.enter_safe_mode("净敞口超限超过宽限期")
                    await self._alert(
                        f"净敞口 {net:.2f} 超过上限 {self._engine_cfg.net_exposure_cap}"
                    )
        for symbol, qty in list(qtys.items()):
            status = self._feed.status(symbol)
            if status and status != "TRADING":
                await self._alert(f"{symbol} 交易状态为 {status}，平仓")
                if self.mode == "shadow":
                    self._force_shadow_close(symbol, qty, now_ms, reason="交易状态非 TRADING")
                else:
                    await self._close_symbol(symbol, qty, now_ms, reason="交易状态非 TRADING")
        control = self._store.get_control_flags(self.candidate.candidate_id)
        if control["flatten_requested"]:
            if self.mode == "shadow":
                self._pending_shadow = [
                    (intent, self._minute_label((now_ms // HOUR_MS) * HOUR_MS), 0.0)
                    for intent in self._close_intents(qtys)
                ]
                self._pending_shadow_hour = now_ms // HOUR_MS
            else:
                await self._execute_intents(
                    self._close_intents(qtys),
                    (now_ms // HOUR_MS) * HOUR_MS,
                    now_ms,
                    {},
                    {},
                )
            self._store.clear_flatten_request(self.candidate.candidate_id)
            self._store.commit()
        if self.mode == "shadow":
            self._shadow_stop_checks(now_ms)
        else:
            await self._reconcile_stops(now_ms)
        equity = await self._equity(qtys)
        self._store.insert_equity_snapshot(
            ts_ms=now_ms,
            wallet_balance=equity,
            unrealized_pnl=0.0,
            available_margin=equity * float(self._sizing.leverage),
        )
        self._save_risk_state(now_ms, equity)
        self._store.commit()

    def _shadow_stop_checks(self, now_ms: int) -> None:
        """影子盘止损：用刚收盘的 1h K 线判断触发（docs/13 §4）。"""
        positions = self._store.list_shadow_positions(self.candidate.candidate_id)
        slip = self._exec_cfg.slip_min_bp * _BP * self._exec_cfg.stop_slip_mult
        for row in positions:
            symbol = str(row["symbol"])
            bars = self._feed.hourly.get(symbol)
            if bars is None or bars.open_time.size == 0:
                continue
            index = bars.last_index_before(now_ms)
            if index < 0:
                continue
            side = int(row["side"])
            stop = float(row["sl"])
            qty = float(row["qty"])
            if stop <= 0.0 or qty <= 0.0:
                continue
            low = float(bars.low[index])
            high = float(bars.high[index])
            open_price = float(bars.open[index])
            triggered = (side > 0 and low <= stop) or (side < 0 and high >= stop)
            if not triggered:
                continue
            base = min(open_price, stop) if side > 0 else max(open_price, stop)
            price = base * (1.0 - slip) if side > 0 else base * (1.0 + slip)
            entry = float(row["entry_price"])
            realized = (price - entry) * qty * side - qty * price * self._fee_taker
            self._store.delete_shadow_position(candidate=self.candidate.candidate_id, symbol=symbol)
            client_id = f"stp-{alias_for(symbol)}-{self._minute_label(now_ms)}"
            self._store.insert_fill(
                exchange_trade_id=self._trade_id(client_id),
                client_id=client_id,
                symbol=symbol,
                price=price,
                qty=qty,
                fee=qty * price * self._fee_taker,
                fee_asset="USDT",
                realized_pnl=realized,
                ts_ms=now_ms,
            )
            self._add_realized(now_ms, realized)
            self._risk.record_exit(ts_ms=now_ms, net_pnl=realized, hit_stop_type=True)
            self._blocked_until[symbol] = now_ms + self._exec_cfg.cooldown_hours * HOUR_MS
            self._store.insert_event(
                level="warn",
                event=f"影子盘止损触发 {symbol}",
                symbol=symbol,
                detail={"price": price, "pnl": realized},
            )
        self._store.commit()

    def _force_shadow_close(self, symbol: str, qty: float, now_ms: int, *, reason: str) -> None:
        """影子盘按最近收盘价立即平仓（下架 / 状态异常）。"""
        price = self._prices().get(symbol)
        if price is None:
            return
        rows = {
            str(item["symbol"]): item
            for item in self._store.list_shadow_positions(self.candidate.candidate_id)
        }
        row = rows.get(symbol)
        if row is None:
            return
        entry = float(row["entry_price"])
        side = int(row["side"])
        realized = (price - entry) * abs(qty) * side - abs(qty) * price * self._fee_taker
        self._store.delete_shadow_position(candidate=self.candidate.candidate_id, symbol=symbol)
        client_id = f"ext-{alias_for(symbol)}-{self._minute_label(now_ms)}"
        self._store.insert_fill(
            exchange_trade_id=self._trade_id(client_id),
            client_id=client_id,
            symbol=symbol,
            price=price,
            qty=abs(qty),
            fee=abs(qty) * price * self._fee_taker,
            fee_asset="USDT",
            realized_pnl=realized,
            ts_ms=now_ms,
        )
        self._add_realized(now_ms, realized)
        self._store.insert_event(
            level="warn",
            event=f"影子盘强制平仓 {symbol}：{reason}",
            symbol=symbol,
            detail={"price": price, "pnl": realized},
        )

    async def _maybe_daily_report(self, now_ms: int) -> None:
        """每日 UTC 日报（权益、持仓、风控状态；受盈亏遮蔽约束）。"""
        target = self._engine_cfg.daily_report_utc
        hour, minute = (int(part) for part in target.split(":"))
        now = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
        if (now.hour, now.minute) < (hour, minute):
            return
        day = now.strftime("%Y-%m-%d")
        if self._last_report_day == day:
            return
        self._last_report_day = day
        qtys, _ = await self._positions_async()
        equity = await self._equity(qtys)
        payload: dict[str, object] = {
            "候选": self.candidate.candidate_id,
            "模式": self.mode,
            "日期": day,
            "持仓数": len(qtys),
            "风控": self._risk.safe_reason or "正常",
        }
        if not self._pnl_hidden():
            payload["权益"] = round(equity, 4)
        await self._notify("daily", payload)

    async def _notify(self, event: str, payload: Mapping[str, object]) -> None:
        """发送通知；失败只记状态库，绝不影响交易（docs/07 §6.9）。"""
        if self._notifier is None:
            return
        try:
            await self._notifier.send(event, dict(payload))
        except Exception as error:  # noqa: BLE001 - 通知失败不能影响交易
            self._store.insert_event(
                level="error",
                event=f"通知失败：{event}：{error}",
                symbol=None,
                detail={"candidate": self.candidate.candidate_id},
            )
            self._store.commit()

    async def _alert(self, text: str) -> None:
        """记录并推送一条告警。"""
        self._store.insert_event(
            level="error",
            event=text,
            symbol=None,
            detail={"candidate": self.candidate.candidate_id, "mode": self.mode},
        )
        self._store.commit()
        await self._notify(
            "alert", {"候选": self.candidate.candidate_id, "模式": self.mode, "原因": text}
        )

    def _pnl_hidden(self) -> bool:
        """shadow / paper 且候选尚未通过最终检验时不展示虚拟收益（docs/07 §6.11）。"""
        if not self._engine_cfg.hide_pnl_until_final:
            return False
        return self.mode in {"shadow", "paper"} and self._entry.get("final") in {
            "pending",
            "running",
        }
