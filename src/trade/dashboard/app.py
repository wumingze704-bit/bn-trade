"""FastAPI 应用与路由。

规格：docs/10-可视化看板.md §7、§9
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from trade.dashboard.config import DashboardConfig
from trade.dashboard.reports import list_models, list_reports, load_model, load_report
from trade.dashboard.security import validate_host_header, validate_origin_header
from trade.dashboard.state_db import state_db_exists

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
    前端据此隐藏实盘页面，只保留研究页。
    """
    live_available = state_db_exists(config.paths.state_db)
    return {
        "live_available": live_available,
        "mode": None,
        "heartbeat_ms": None,
        "model_id": None,
        "sizing": None,
        "safe_mode": None,
        "halted": None,
        "connections": {},
        "weight_usage": None,
        "tz": config.display.tz,
    }
