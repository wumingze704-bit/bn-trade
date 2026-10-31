"""看板 FastAPI 应用与安全测试。

规格：docs/10-可视化看板.md §7、§9、§12
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from trade.dashboard.app import create_app
from trade.dashboard.config import (
    ControlConfig,
    DashboardConfig,
    DashboardPathsConfig,
    DisplayConfig,
)


def _config(tmp_path: Path, reports_dir: Path) -> DashboardConfig:
    return DashboardConfig(
        paths=DashboardPathsConfig(
            state_db=tmp_path / "live" / "state.db",  # 故意不存在，模拟研究模式
            live_dir=tmp_path / "live",
            reports_dir=reports_dir,
            models_dir=tmp_path / "models",
        ),
        display=DisplayConfig(tz="Asia/Shanghai"),
        control=ControlConfig(enabled=True, pin_env="DASHBOARD_CONTROL_PIN"),
    )


def test_status_research_only(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["live_available"] is False


def test_reports_endpoint(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.get("/api/reports")
    assert response.status_code == 200
    assert len(response.json()["reports"]) == 1


def test_report_detail(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.get("/api/reports/run1")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run1"
    assert body["gate"]["source"] == "fixture"
    assert len(body["trades"]) == 1


def test_report_detail_404(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    assert client.get("/api/reports/missing").status_code == 404


def test_models_endpoint_empty(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.get("/api/models")
    assert response.status_code == 200
    assert response.json()["models"] == []


def test_index_served(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.get("/")
    assert response.status_code == 200
    assert "trade" in response.text


def test_post_bad_host_rejected(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.post("/api/anything", headers={"Host": "evil.com"})
    assert response.status_code == 403


def test_post_bad_origin_rejected(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.post(
        "/api/anything",
        headers={"Host": "localhost:8501", "Origin": "http://evil.com"},
    )
    assert response.status_code == 403


def test_post_valid_source_passes_middleware(tmp_path: Path, report_dir: Path) -> None:
    # 合法来源应通过中间件（不被 403 拒绝），无对应路由落到静态挂载的 405/404。
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    response = client.post("/api/anything", headers={"Host": "localhost:8501"})
    assert response.status_code != 403


def test_ws_bad_origin_rejected(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/ws", headers={"Host": "localhost:8501", "Origin": "http://evil.com"}
        ),
    ):
        pass


def test_ws_good_source_connects(tmp_path: Path, report_dir: Path) -> None:
    client = TestClient(create_app(_config(tmp_path, report_dir)))
    with client.websocket_connect(
        "/ws", headers={"Host": "localhost:8501", "Origin": "http://localhost:8501"}
    ) as websocket:
        status = websocket.receive_json()
        assert status["live_available"] is False


def test_index_no_raw_html_injection() -> None:
    # 前端对动态内容统一使用 x-text（文本转义），不使用 x-html 注入外部 HTML。
    index = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "trade"
        / "dashboard"
        / "static"
        / "index.html"
    )
    text = index.read_text(encoding="utf-8")
    assert "x-html" not in text
    assert "x-text" in text
