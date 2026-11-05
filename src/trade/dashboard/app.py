"""FastAPI 应用与路由。

规格：docs/10-可视化看板.md §5、§6、§7、§9
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from trade.common.config import load_config
from trade.common.exits import ExitConfig
from trade.common.sizing import SizingConfig
from trade.dashboard.config import DashboardConfig
from trade.dashboard.reports import list_models, list_reports, load_model, load_report
from trade.dashboard.research import (
    list_datasets,
    list_universe_months,
    load_quality,
    load_universe_month,
    load_universe_rule,
    read_universe_report,
    universe_changes,
)
from trade.dashboard.security import validate_host_header, validate_origin_header
from trade.dashboard.state_db import (
    open_state_db_readonly,
    query_closed_fills,
    query_equity_curve,
    query_events,
    query_latest_cycle,
    query_latest_equity,
    query_orders,
    query_positions,
    query_risk_state,
    query_scan_states,
    query_status,
    state_db_exists,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(config: DashboardConfig) -> FastAPI:
    """构造看板 FastAPI 应用（10 §7）。

    只监听回环地址（由 `DashboardConfig` 校验）；所有 POST 与 WebSocket
    校验 `Host`/`Origin`；无状态库时只开放研究接口。

    Args:
        config: 通过 `load_dashboard_config` 加载的看板配置。

    Returns:
        配置完成的 FastAPI 应用实例。
    """
    app = FastAPI(title="trade dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config

    @app.middleware("http")
    async def enforce_loopback_source(request: Request, call_next: Any) -> Any:
        # 只对写方法与控制类请求做来源校验；GET/HEAD/OPTIONS 用于静态与研究读取（10 §9）
        if request.method not in ("GET", "HEAD", "OPTIONS") and (
            not validate_host_header(request.headers.get("host"))
            or not validate_origin_header(request.headers.get("origin"))
        ):
            return JSONResponse({"detail": "拒绝非本机来源的请求"}, status_code=403)
        return await call_next(request)

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return build_status(config)

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        return build_overview(config, now_ms=_now_ms())

    @app.get("/api/scan")
    async def scan() -> dict[str, Any]:
        return build_scan(config)

    @app.get("/api/equity")
    async def equity(range_key: str = Query(default="all", alias="range")) -> dict[str, Any]:
        if range_key not in {"1D", "1W", "1M", "all"}:
            raise HTTPException(status_code=422, detail="range 只能是 1D / 1W / 1M / all")
        return build_equity(config, range_key=range_key, now_ms=_now_ms())

    @app.get("/api/positions")
    async def positions() -> dict[str, Any]:
        return _with_db(config, lambda connection: {"positions": query_positions(connection)})

    @app.get("/api/orders")
    async def orders() -> dict[str, Any]:
        return _with_db(config, lambda connection: {"orders": query_orders(connection)})

    @app.get("/api/events")
    async def events(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
        return _with_db(
            config, lambda connection: {"events": query_events(connection, limit=limit)}
        )

    @app.get("/api/universe")
    async def universe(month: str | None = None) -> dict[str, Any]:
        months = list_universe_months(config.paths.data_dir)
        selected = month or (months[-1] if months else None)
        snapshot = load_universe_month(config.paths.data_dir, selected) if selected else None
        return {
            "months": months,
            "selected": selected,
            "snapshot": snapshot,
            "changes": universe_changes(config.paths.data_dir, months),
            "rule": load_universe_rule(config.paths.universe_config),
            "report_md": read_universe_report(config.paths.universe_report),
        }

    @app.get("/api/quality")
    async def quality() -> dict[str, Any]:
        return {"quality": load_quality(config.paths.quality_cache)}

    @app.get("/api/datasets")
    async def datasets() -> dict[str, Any]:
        return {"datasets": list_datasets(config.paths.data_dir)}

    @app.get("/api/reports")
    async def reports() -> dict[str, Any]:
        return {"reports": list_reports(config.paths.reports_dir)}

    @app.get("/api/reports/{run_id}")
    async def report(run_id: str) -> dict[str, Any]:
        data = load_report(config.paths.reports_dir, run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return data

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"models": list_models(config.paths.models_dir)}

    @app.get("/api/models/{model_id}")
    async def model(model_id: str) -> dict[str, Any]:
        data = load_model(config.paths.models_dir, model_id)
        if data is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        return data

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        # WebSocket 握手时同样校验 Host/Origin（10 §9）
        if not validate_host_header(websocket.headers.get("host")) or not validate_origin_header(
            websocket.headers.get("origin")
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_json(build_status(config))
        try:
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(websocket.receive_text(), timeout=config.push_sec.status)
                await websocket.send_json(build_status(config))
        except WebSocketDisconnect:
            return

    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
    return app


def build_status(config: DashboardConfig) -> dict[str, Any]:
    """构造 `/api/status` 与 `/ws` 推送的状态对象（10 §7）。

    无状态库时 `live_available=False`，实盘相关字段全部为 None，
    前端据此隐藏实盘页面，只保留研究页。运行模式与请求权重当前不写入
    状态库（T5-12 只覆盖扫描表），对应字段保持 None。
    """
    live_available = state_db_exists(config.paths.state_db)
    heartbeat_ms: int | None = None
    halted: bool | None = None
    safe_mode: str | None = None
    if live_available:
        connection = open_state_db_readonly(config.paths.state_db)
        try:
            db_status = query_status(connection)
        finally:
            connection.close()
        heartbeat_ms = db_status["heartbeat_ms"]
        halted = db_status["halted"]
        safe_mode = db_status["safe_mode_reason"]
    sizing = _load_sizing(config)
    return {
        "live_available": live_available,
        "mode": None,
        "heartbeat_ms": heartbeat_ms,
        "model_id": None,
        "sizing": _sizing_summary(sizing),
        "safe_mode": safe_mode,
        "halted": halted,
        "connections": {},
        "weight_usage": None,
        "tz": config.display.tz,
    }


def build_scan(config: DashboardConfig) -> dict[str, Any]:
    """构造 `/api/scan`：最新一轮统计与按 `seq` 排序的全部币状态（10 §6）。"""
    if not state_db_exists(config.paths.state_db):
        return {"live_available": False, "cycle": None, "states": []}
    connection = open_state_db_readonly(config.paths.state_db)
    try:
        return {
            "live_available": True,
            "cycle": query_latest_cycle(connection),
            "states": query_scan_states(connection),
        }
    finally:
        connection.close()


def build_equity(config: DashboardConfig, *, range_key: str, now_ms: int) -> dict[str, Any]:
    """构造 `/api/equity` 权益曲线（10 §5.1、§10）。"""
    if not state_db_exists(config.paths.state_db):
        return {"range": range_key, "points": []}
    connection = open_state_db_readonly(config.paths.state_db)
    try:
        points = query_equity_curve(
            connection,
            range_key=range_key,
            now_ms=now_ms,
            max_points=config.display.equity_max_points,
        )
    finally:
        connection.close()
    return {"range": range_key, "points": points}


def build_overview(config: DashboardConfig, *, now_ms: int) -> dict[str, Any]:
    """构造 `/api/overview`：4 张指标卡、持仓、挂单、事件（10 §5.1）。

    Args:
        config: 看板配置。
        now_ms: 当前 UTC 毫秒时间，用于“今日”切分与持仓进度。

    Returns:
        指标卡 `kpis`、`positions`、`orders`、`events`、`risk` 与 `live_available`。
    """
    if not state_db_exists(config.paths.state_db):
        return {
            "live_available": False,
            "kpis": None,
            "positions": [],
            "orders": [],
            "events": [],
            "risk": None,
        }
    sizing = _load_sizing(config)
    exit_cfg = _load_exits(config)
    day_start_ms = _day_start_ms(now_ms, config.display.tz)
    connection = open_state_db_readonly(config.paths.state_db)
    try:
        positions = query_positions(connection)
        orders = query_orders(connection)
        events = query_events(connection)
        risk = query_risk_state(connection)
        equity = query_latest_equity(connection)
        fills = query_closed_fills(connection, start_ms=day_start_ms, end_ms=now_ms)
        spark = query_equity_curve(connection, range_key="1D", now_ms=now_ms, max_points=60)
    finally:
        connection.close()
    kpis = _kpis(
        equity=equity,
        risk=risk,
        fills=fills,
        positions=positions,
        sizing=sizing,
        spark=spark,
    )
    return {
        "live_available": True,
        "kpis": kpis,
        "positions": [_position_card(item, now_ms=now_ms, exit_cfg=exit_cfg) for item in positions],
        "orders": orders,
        "events": [_event_card(item, tz=config.display.tz) for item in events],
        "risk": risk,
    }


def _with_db(config: DashboardConfig, reader: Any) -> dict[str, Any]:
    """在只读连接上执行一个查询函数；无状态库时返回空对象。"""
    if not state_db_exists(config.paths.state_db):
        return {"live_available": False}
    connection = open_state_db_readonly(config.paths.state_db)
    try:
        result = reader(connection)
    finally:
        connection.close()
    return {"live_available": True, **result}


def _load_sizing(config: DashboardConfig) -> SizingConfig | None:
    """读取仓位配置（显示用；唯一来源仍是 configs/sizing.yaml，06 §5）。"""
    path = config.paths.sizing_config
    if path is None or not path.is_file():
        return None
    return load_config(SizingConfig, path)


def _load_exits(config: DashboardConfig) -> ExitConfig | None:
    """读取出场配置（持仓进度条相对 H_max，10 §5.1）。"""
    path = config.paths.exits_config
    if path is None or not path.is_file():
        return None
    return load_config(ExitConfig, path)


def _sizing_summary(sizing: SizingConfig | None) -> dict[str, Any] | None:
    if sizing is None:
        return None
    return {
        "position_frac": sizing.position_frac,
        "leverage": sizing.leverage,
        "max_positions": sizing.max_positions,
        "initial_equity": sizing.initial_equity,
        "daily_loss_stop": sizing.daily_loss_stop,
    }


def _kpis(
    *,
    equity: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    fills: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    sizing: SizingConfig | None,
    spark: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算 4 张指标卡（10 §5.1）。"""
    current_equity = float(equity["equity"]) if equity else 0.0
    initial_equity = float(sizing.initial_equity) if sizing else None
    change = current_equity - initial_equity if initial_equity else None
    change_pct = change / initial_equity if change is not None and initial_equity else None

    realized = float(risk["realized_pnl"]) if risk else 0.0
    daily_stop = float(sizing.daily_loss_stop) if sizing else None
    stop_usdt = daily_stop * current_equity if daily_stop is not None else None
    remaining = (stop_usdt + realized) if stop_usdt is not None else None

    margin_used = sum(float(item.get("margin") or 0.0) for item in positions)
    wallet = float(equity["wallet_balance"]) if equity else 0.0
    margin_ratio = margin_used / wallet if wallet > 0 else 0.0

    pnls = [float(item["realized_pnl"]) for item in fills]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    notional = sum(
        abs(float(item["price"]) * float(item["qty"]))
        for item in fills
        if item.get("price") and item.get("qty")
    )
    avg_bp = sum(pnls) / notional * 10000.0 if notional > 0 else 0.0

    return {
        "equity": {
            "value": current_equity,
            "initial": initial_equity,
            "change": change,
            "change_pct": change_pct,
            "spark": spark,
            "ts_ms": equity["ts_ms"] if equity else None,
        },
        "today_pnl": {
            "value": realized,
            "pct": realized / current_equity if current_equity > 0 else 0.0,
            "daily_loss_stop": daily_stop,
            "stop_usdt": stop_usdt,
            "remaining_usdt": remaining,
        },
        "positions": {
            "count": len(positions),
            "max_positions": sizing.max_positions if sizing else None,
            "margin_used": margin_used,
            "margin_ratio": margin_ratio,
        },
        "today_trades": {
            "count": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
            "avg_bp": avg_bp,
        },
    }


def _position_card(
    item: dict[str, Any], *, now_ms: int, exit_cfg: ExitConfig | None
) -> dict[str, Any]:
    """把持仓行转换为展示字段：浮动 R、止损类型、持仓分钟进度（10 §5.1）。"""
    entry = float(item.get("entry_price") or 0.0)
    sl = float(item.get("sl") or 0.0)
    hh = float(item.get("hh") or 0.0)
    mfe_r = (hh - entry) / (entry * sl) if entry > 0 and sl > 0 and hh > 0 else 0.0
    stop_price = float(item.get("stop_price") or 0.0)
    if stop_price >= entry * (1.0 + 0.0015):
        stop_kind = "trail"
    elif stop_price >= entry * (1.0 - 0.0015):
        stop_kind = "breakeven"
    else:
        stop_kind = "initial"
    signal_t = item.get("signal_t")
    held = max(0, (now_ms - int(signal_t)) // 60_000) if signal_t else 0
    h_max = exit_cfg.H_max if exit_cfg is not None else None
    return {
        **item,
        "mfe_r": mfe_r,
        "stop_kind": stop_kind,
        "held_minutes": held,
        "h_max": h_max,
        "progress": min(1.0, held / h_max) if h_max else None,
    }


def _event_card(item: dict[str, Any], *, tz: str) -> dict[str, Any]:
    """事件展示字段：本地时间与是否已推送微信（10 §5.1）。"""
    ts_ms = int(item["ts_ms"])
    local = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone(ZoneInfo(tz))
    return {
        **item,
        "time_local": local.strftime("%m-%d %H:%M:%S"),
        "time_utc": datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%m-%d %H:%M:%S"),
        "notified": bool(item.get("notified")),
    }


def _now_ms() -> int:
    """当前 UTC 毫秒时间。"""
    return int(datetime.now(UTC).timestamp() * 1000)


def _day_start_ms(now_ms: int, tz: str) -> int:
    """按展示时区取当日 00:00 的 UTC 毫秒（10 §4：时间显示为北京时间）。"""
    local = datetime.fromtimestamp(now_ms / 1000, tz=UTC).astimezone(ZoneInfo(tz))
    start = datetime.combine(local.date(), datetime.min.time(), tzinfo=ZoneInfo(tz))
    return int(start.astimezone(UTC).timestamp() * 1000)
