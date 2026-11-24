"""通用低频实盘引擎入口：`--mode shadow|paper|live --candidate <候选ID>`。

规格：docs/07-实盘与风控.md §1、§5

- live：必须同时带 `--i-understand-the-risk`，且最终检验 PASS、影子盘 LIVE_READY、
  冻结文件 SHA256 一致、环境变量中有正式网密钥；任一不满足立即退出。
- paper：币安模拟盘（demo-fapi.binance.com），用于验证下单与止损流程。
- shadow：正式网公开行情，只记录不下单（交易调用由只读客户端拦截）。

用法：
    python scripts/live_runner.py --mode shadow --candidate <候选ID>
    python scripts/live_runner.py --mode live --candidate <候选ID> --i-understand-the-risk
    python scripts/live_runner.py --mode live --candidate <候选ID> --clear-halt
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from pathlib import Path
from typing import Any

from trade.common.config import BaseConfig, load_config
from trade.live import candidate as cand
from trade.live.candidate import CandidateSpec
from trade.live.config import LiveConfig, load_live_config
from trade.live.credentials import CredentialError, build_client
from trade.live.execution import ReadOnlyMarketClient
from trade.live.feed import (
    LiveFeed,
    load_universe_history,
    rules_from_exchange_info,
    statuses_from_exchange_info,
)
from trade.live.notifier.dispatcher import NotifierDispatcher
from trade.live.riskguard import RiskGuard
from trade.live.runner import Runner, load_risk_limits
from trade.live.store import Store
from trade.research.config import load_research_config, load_research_sizing

_ROOT = Path(__file__).resolve().parents[1]


def _resolve(value: Path | str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else _ROOT / path


def _acquire_lock(path: Path) -> None:
    """同一候选同一模式只允许一个实例（pid 存活时拒绝启动）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text.isdigit() and _pid_alive(int(text)):
            raise SystemExit(f"实例已在运行（pid {text}）：{path}")
    path.write_text(str(os.getpid()), encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """把 SIGTERM / SIGINT 转成正常退出（systemd stop 与 Ctrl+C）。

    正常退出会删除心跳文件，看门狗据此判断实例是主动停止而不是崩溃
    （3 号第六部分第 10 条）。Windows 与非主线程不支持时保持默认行为。
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError, AttributeError):
            continue


def _verify(
    *,
    mode: str,
    candidate_id: str,
    config_path: Path,
    i_understand: bool,
) -> tuple[LiveConfig, CandidateSpec, dict[str, Any], Store]:
    """加载配置与候选并执行全部启动前校验；不通过直接退出。"""
    config = load_live_config(config_path)
    engine = config.engine
    spec, entry = cand.load_candidate(
        root=_ROOT,
        candidates_dir=_resolve(engine.candidates_dir),
        ledger_path=_resolve(engine.ledger),
        candidate_id=candidate_id,
    )
    errors = cand.run_mode_errors(
        mode=mode,
        i_understand_the_risk=i_understand,
        spec=spec,
        entry=entry,
        final_dir=_resolve(engine.final_dir),
        shadow_dir=_resolve(engine.shadow_dir),
        root=_ROOT,
        env=os.environ,
    )
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        raise SystemExit(1)
    cand.verify_frozen(spec, _ROOT)
    store = Store(_resolve(config.storage.db_path))
    return config, spec, entry, store


async def _run(mode: str, candidate_id: str, config_path: Path, i_understand: bool) -> None:
    config, spec, entry, store = _verify(
        mode=mode,
        candidate_id=candidate_id,
        config_path=config_path,
        i_understand=i_understand,
    )
    engine = config.engine
    db_path = _resolve(config.storage.db_path)
    lock_path = db_path.parent / f"{candidate_id}-{mode}.lock"
    _acquire_lock(lock_path)
    client = None
    notifier = None
    runner: Runner | None = None
    normal_exit = False
    try:
        client = build_client(mode)
        source = ReadOnlyMarketClient(client) if mode == "shadow" else client
        info = await client.exchange_info()
        rules = rules_from_exchange_info(info)
        statuses = statuses_from_exchange_info(info)
        universe = load_universe_history(_resolve(engine.universe_hist))
        feed = LiveFeed(
            source=source,
            rules=rules,
            universe=universe,
            statuses=statuses,
            history_days_hourly=engine.history_days_hourly,
            history_days_daily=engine.history_days_daily,
            funding_limit=engine.funding_limit,
            with_premium=engine.with_premium,
        )
        research = load_research_config(_resolve(engine.research))
        base = load_config(BaseConfig, _resolve(engine.base))
        # 候选的档位与仓位模式来自候选文件；单笔风险取候选冻结值（docs/13 §2、§3）
        sizing = load_research_sizing(_resolve(engine.sizing_research)).with_tier(spec.risk_tier)
        updates: dict[str, Any] = {"risk_per_trade": spec.risk_per_trade}
        if spec.sizing_mode is not None:
            updates["sizing_mode"] = spec.sizing_mode
        sizing = sizing.model_copy(update=updates)
        print(
            f"[OK] 候选 {candidate_id} 模式 {mode} 档位 {spec.risk_tier or '（配置默认）'} "
            f"仓位模式 {sizing.sizing_mode} 资金上限 {engine.live_capital_cap} USDT"
        )

        risk = RiskGuard(
            limits=load_risk_limits(sizing, mode),
            net_exposure_cap=engine.net_exposure_cap,
            net_exposure_grace_min=engine.net_exposure_grace_min,
            clock_drift_limit_ms=engine.clock_drift_ms,
            order_error_limit=engine.max_order_errors_1h,
        )
        notifier = NotifierDispatcher(config.notify)
        runner = Runner(
            mode=mode,
            config=config,
            root=_ROOT,
            spec=spec,
            entry=entry,
            store=store,
            feed=feed,
            sizing=sizing,
            exec_cfg=research.execution,
            portfolio_cfg=research.portfolio,
            fee_taker=base.fees.taker,
            risk=risk,
            client=None if mode == "shadow" else client,
            notifier=notifier,
        )
        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        await runner.run(stop_event)
        normal_exit = True
    finally:
        if normal_exit and runner is not None:
            # 正常退出（systemd stop）删除心跳，看门狗不会重启主动停止的实例
            runner.remove_heartbeat()
        if notifier is not None:
            await notifier.aclose()
        if client is not None:
            await client.aclose()
        store.close()
        if lock_path.is_file():
            lock_path.unlink()


def _clear_halt(mode: str, candidate_id: str, config_path: Path) -> None:
    """人工清除停机标志（看板不提供该功能，docs/07 §7.3）。"""
    config = load_live_config(config_path)
    store = Store(_resolve(config.storage.db_path))
    try:
        store.clear_engine_halt(candidate=candidate_id, mode=mode)
        store.set_engine_halt(candidate=candidate_id, mode=mode, halted=False, reason=None)
        store.commit()
        print(f"[OK] 已清除 {candidate_id}-{mode} 的停机标志")
    finally:
        store.close()


def _parse_instance(instance: str) -> tuple[str, str]:
    """解析 systemd 模板单元的实例名 `<候选ID>-<shadow|paper>`。

    Raises:
        SystemExit: 实例名不合法（不猜默认值）。
    """
    candidate, _, mode = instance.rpartition("-")
    if not candidate or mode not in {"shadow", "paper"}:
        raise SystemExit(f"[FAIL] --instance 必须形如 <候选ID>-<shadow|paper>：{instance}")
    return mode, candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="通用低频实盘引擎")
    parser.add_argument("--mode", choices=["shadow", "paper", "live"])
    parser.add_argument("--candidate", help="候选 ID（总账 candidates[].id）")
    parser.add_argument(
        "--instance",
        help="systemd 模板单元的实例名 <候选ID>-<shadow|paper>（替代 --mode 与 --candidate）",
    )
    parser.add_argument("--config", default="configs/live.yaml")
    parser.add_argument("--i-understand-the-risk", action="store_true")
    parser.add_argument("--clear-halt", action="store_true")
    args = parser.parse_args()
    if args.instance:
        mode, candidate_id = _parse_instance(args.instance)
    elif args.mode and args.candidate:
        mode, candidate_id = args.mode, args.candidate
    else:
        parser.error("必须提供 --mode 与 --candidate（或 systemd 实例用 --instance）")
    config_path = _resolve(args.config)
    if args.clear_halt:
        _clear_halt(mode, candidate_id, config_path)
        return
    try:
        asyncio.run(_run(mode, candidate_id, config_path, args.i_understand_the_risk))
    except CredentialError as error:
        print(f"[FAIL] {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
