---
name: Trade P6
description: "在全新聊天窗口中执行 trade 仓库 P6 看板任务"
argument-hint: "可选：指定 P6 任务 ID；默认选择依赖满足的最早未完成任务"
agent: "agent"
---

你在 trade 仓库工作。本窗口没有前序聊天记录。先检查当前任务状态、看板实现、接口契约和依赖；以设计与工程规范为准。

## 必读资料

1. 仓库根目录 `AGENTS.md`、`docs/design.md`。
2. `docs/08-开发计划与验收.md` 的 P6/G4 看板验收和安全测试。
3. `docs/10-可视化看板.md` 全文、`docs/02-系统架构与部署.md` 的 SSH 隧道章节、`docs/07-实盘与风控.md` 的状态库章节。
4. `docs/tasks.md` 中 T6-1~T6-7、T5-12 依赖和报告。
5. 当前 `src/trade/dashboard/`、静态资源、状态库 schema、测试及设计草图。

## 范围与任务选择

只做 P6 看板。依据 tasks.md 选一个最早、依赖已满足的未完成任务；每窗口只实施一个任务，完成测试、文档、任务状态和报告后停止。后续任务在新窗口重复运行本 prompt。T6-4 依赖 T5-12；真实报告页面验收依赖 P4 产物；不可用虚构数据宣称真实报告验收。

**提前准备阶段（已完成，2026-10-31）**：当前 `trade.live` 尚未实现，状态库 `state.db`、`trade.live.control`、T5-12 的 `scan_state`/`scan_cycles` 表都不存在。不依赖 P5 的四项已实施完成，其余仍「依赖 P5，暂不实施」：

- ✅ 已完成：**T6-1 后端骨架与安全**（FastAPI app + `configs/dashboard.yaml` pydantic 模型；`127.0.0.1` 回环监听校验，非 loopback 拒绝启动；POST/WebSocket 校验 `Host`/`Origin`；`/api/status`、`/api/reports`、`/api/reports/{id}`、`/api/models`、`WS /ws` 研究类接口；状态库只读 URI `mode=ro`；无 `state.db` 时自动隐藏实盘页面）。测试：`tests/dashboard/`（TestClient 冒烟、安全拒绝、WebSocket、状态库缺失、报告读取）。
- ✅ 已完成：**T6-2 前端框架与视觉规范**（`static/index.html` + `styles.css` 手写深色令牌（10 §4）+ Alpine.js；vendor 已本地化 `static/vendor/`（Alpine.js 3.14.8 / Plotly.js 2.35.3 / lightweight-charts 4.2.3）+ `VERSIONS.txt` 记录版本/SHA256，不走 CDN；只用 CSS `transform/opacity` 动画；左侧栏 6 入口 + 顶栏骨架；`x-text` 转义、`prefers-reduced-motion`）。遗留：`docs/assets/dashboard-overview.svg` 草图缺失，布局对照待补。
- ✅ 已完成：**T6-3 回测与研究页**（读 `artifacts/reports` 与 `artifacts/models`，按 06 §8 报告结构展示 `summary.json`/`gate.json`/权益曲线（Plotly）/交易明细；空目录/缺报告占位）。遗留：真实报告数值一致性验收需 P4 产物；「数据与模型」标签页的交易范围快照与质量页未实现。
- ✅ 已完成：**T6-7 SSH 隧道**（`scripts/sync.ps1` 增加 `tunnel` 子命令：`ssh -N -L 8501:127.0.0.1:8501 tokyo` 并打开浏览器）。遗留：公网 `curl` 8501 不通与性能验收待服务器部署。
- ❌ 暂不实施（依赖 P5）：**T6-4 总览与全市场扫描网格**（依赖 T5-12 的 `scan_state`/`scan_cycles`）、**T6-5 持仓/挂单/交易/绩效页**（依赖 `state.db` 表）、**T6-6 控制页**（依赖 `trade.live.control`）。这些任务的页面骨架已占位，但数据接口与验收不实现、不伪造。

依赖说明：`fastapi`/`uvicorn` 已在 `pyproject.toml` 的 `live` extra（T0.10 已加），本机已安装 `fastapi==0.162.0`、`uvicorn==0.54.0` 用于开发测试；`streamlit` 已在 T0.10 从 base 依赖移除（当前 `requirements-*.lock` 无 streamlit），`dashboard/__init__.py` 已按 10 §3 重写为 FastAPI 形态，无需再改依赖。

## 质量与安全要求

- 以 docs/10 的实现和验收为准，不因旧 Streamlit 包或旧描述擅自回迁技术栈。后端必须按设计拒绝非 loopback 监听，POST 与 WebSocket 校验 Host/Origin，状态库只读，缺少 state DB 时只开放允许的研究功能。
- 前端按既有设计和任务验收做响应式与可访问性；所有外部内容转义；第三方资源应按任务要求本地化并校验版本/SHA256。不要引入不必要新依赖或重做无关视觉系统。
- 测试覆盖 TestClient、安全拒绝、WebSocket、状态库缺失/只读、HTML 转义及各页面真实数据一致性；按阶段测试不能替代真实报告一致性验收。
- 先跑窄测试，再跑 `AGENTS.md` §8 全套质量命令；同步 docs/10、07 状态库契约、配置/schema、`docs/tasks.md`，在对话中给出中文完成报告。
- SSH 隧道仅为访问本机绑定的看板服务；不得实现或配置任何币安请求代理、转发或绕过网络限制。公网只开放设计要求的 SSH 入口。

## 安全边界

控制页调用现有 `trade.live.control`、PIN 和 Kill Switch 单一接口，不复制控制逻辑、不添加清除停机标志或切换运行模式的能力。禁止把看板暴露到公网、记录 PIN/令牌或绕过身份校验。风险与控制需求不清时暂停询问。
