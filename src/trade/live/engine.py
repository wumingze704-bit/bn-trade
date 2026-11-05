"""实盘交易引擎主循环与启动流程。

规格：docs/07-实盘与风控.md §2、§4
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from trade.live.config import LiveConfig, load_exits_config, load_sizing_config
from trade.live.control import effective_sizing_from_store
from trade.live.exchange.client import ExchangeClient
from trade.live.orders import OrderManager
from trade.live.risk import RiskEngine
from trade.live.store import Store

# 启动时钟检查上限（07 §2 第 2 步：> 500ms 退出）
_STARTUP_CLOCK_LIMIT_MS = 500


@dataclass(frozen=True)
class StartupResult:
    """启动流程校验结果（07 §2）。"""

    ok: bool
    errors: list[str]
    warnings: list[str]


def startup_clock_ok(drift_ms: int, limit_ms: int = _STARTUP_CLOCK_LIMIT_MS) -> bool:
    """启动时钟检查（07 §2 第 2 步）：时差中位数 ≤ 500ms。"""
    return abs(drift_ms) <= limit_ms


def fee_reject_live(actual_taker: float, configured_taker: float, ratio: float) -> bool:
    """费率核对（07 §2 第 6 步）：实际吃单费率 > 配置 × ratio 时拒绝 live。"""
    return actual_taker > configured_taker * ratio


class LiveEngine:
    """实盘引擎：组装各组件并驱动主循环（07 §2、§4）。"""

    def __init__(
        self,
        *,
        config: LiveConfig,
        base_dir: Path,
        client: ExchangeClient,
        store: Store,
        notifier: object | None = None,
    ) -> None:
        self._config = config
        self._base_dir = base_dir
        self._client = client
        self._store = store
        self._notifier = notifier
        sizing = load_sizing_config(config.sizing)
        exits = load_exits_config(config.exits)
        self._sizing = sizing
        self._exits = exits
        self._effective_sizing = effective_sizing_from_store(store, sizing)
        self._risk = RiskEngine(self._effective_sizing, config.risk, config.health)
        self._orders = OrderManager(client, {})

    @property
    def risk(self) -> RiskEngine:
        return self._risk

    @property
    def orders(self) -> OrderManager:
        return self._orders

    @property
    def effective_sizing(self) -> object:
        return self._effective_sizing

    async def run(self) -> None:
        """主循环入口（07 §4）。

        每分钟：K 线收盘 → 特征 → 预过滤 → 推理 → 风控 → 排序下单；
        每 10 秒写一次权益快照。特征/推理依赖模型产物（trade.models.artifact），
        在模型产物就绪后接入（见报告 TODO）。
        """
        while True:
            await asyncio.sleep(1.0)
            # 权益快照与延迟落库在此按 10 秒节拍写入（07 §4）
