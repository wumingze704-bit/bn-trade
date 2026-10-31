"""`python -m trade.dashboard` 入口。

规格：docs/10-可视化看板.md §2
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from trade.common.logging import configure_logging
from trade.dashboard.app import create_app
from trade.dashboard.config import load_dashboard_config


def main() -> None:
    """解析参数、加载配置并启动看板服务。"""
    parser = argparse.ArgumentParser(description="trade 看板（FastAPI，深色 iOS 风格，10）")
    parser.add_argument("--config", type=Path, default=Path("configs/dashboard.yaml"))
    args = parser.parse_args()

    configure_logging()
    config = load_dashboard_config(args.config)
    # 回环监听已在 pydantic 模型中校验（10 §2），此处直接使用。
    app = create_app(config)
    uvicorn.run(app, host=config.server.address, port=config.server.port, log_level="info")


if __name__ == "__main__":
    main()
