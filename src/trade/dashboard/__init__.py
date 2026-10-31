"""网页看板：FastAPI + 静态前端（深色 iOS 风格）。

规格：docs/02-系统架构与部署.md §2；docs/10-可视化看板.md
"""

from trade.dashboard.app import create_app
from trade.dashboard.config import DashboardConfig, load_dashboard_config

__all__ = ["DashboardConfig", "create_app", "load_dashboard_config"]
