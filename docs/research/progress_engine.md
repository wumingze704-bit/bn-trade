# 实盘工程与看板进度（P5 / P6）

状态：完成（结论 ENGINE_READY，见 `docs/research/engine.md`）。执行文件：`指令/自动多轮/3-实盘工程与看板.txt`；M6 对齐与安全修复见 0 号调度第六部分。时间：2026-11-23 首次完成，2026-11-24 M6 更新。


## 一、模式

- 正常模式：本地无其他研究大进程；服务器无下载 / 聚合进程。
- 质量命令按 AGENTS.md §8 全量执行（见 engine.md §六）。

## 二、已完成

1. 引擎：`trade.live.runner`（调度、对账、巡检、心跳、日报）、`candidate`（候选冻结与 live 前置条件）、`feed`（REST 行情组装）、`targets`（目标 − 实际差额）、`execution`（GTX / IOC / 市价兜底与影子成交）、`stops`（STOP_MARKET 先挂后撤）、`riskguard`（R5~R8 + 健康规则）、`killswitch`、`credentials`、`units`。
2. 状态库：在旧表基础上新增 `engine_status`、`rebalance_log`、`consistency_log`、`halt_flags`、`control_flags`、`shadow_positions`；旧表与旧接口保持不变。
3. 控制：`trade.live.control` 增加暂停开仓 / 恢复 / 请求平仓 / `risk_per_trade`（0.005~0.01）。
4. 脚本：新增 `scripts/live_runner.py`、`scripts/live_watchdog.py`、`scripts/check_live_env.py`；重写 `scripts/kill_switch.py`、`scripts/set_sizing.py`；`git rm scripts/run_live.py`。
5. 部署：`deploy/trade-runner@.service`、`trade-runner-live@.service`、`trade-dashboard.service`、`trade-watchdog.service` / `.timer`。
6. 看板：6 个页面（总览、持仓、调仓记录、一致性、研究、控制）；新增 `/api/engine`、`/api/rebalances`、`/api/consistency`、`/api/control`；控制 PIN 与锁定；盈亏遮蔽。
7. 文档：重写 `docs/07`、`docs/10`（旧版 `git mv` 到 `docs/archive/`）；同步 `docs/09`（模拟盘端点、algoOrder 参数、API 权限接口、环境变量）与 `docs/02`（systemd 实例、脚本清单、密钥路径）。
8. 测试：新增 43 个用例，全部通过；全量 `pytest -q` 为 `1 failed, 776 passed, 3 skipped`（失败项为准备阶段既有的 GPU 测试）。

## 三、服务器部署

- 代码用 tar 同步到 `~/trade`（服务器不是 git 仓库）。
- venv 补装 `fastapi==0.165.0`、`uvicorn==0.54.0`。
- 单元文件安装到 `/etc/systemd/system/`，`daemon-reload` 后启用 `trade-dashboard.service` 与 `trade-watchdog.timer`。
- 验证：`systemctl is-active` 两个单元均为 active；`curl -I http://127.0.0.1:8501/` 为 200；`/api/status` 返回 `live_available=false`（尚无状态库）。
- 未启动交易 / 影子盘实例（总账无候选）。

## 四、剩余项

- 总账无候选：shadow / live 无法启动；当月选币快照缺 `2026-11`（目录只到 `2026-07`），启动前需补齐。
- `.env.live`、`.dashboard_pin`、WxPusher 环境变量未配置（不写入仓库）。
- 真实账户启动检查、SSH 隧道浏览器实测、手机隧道、看门狗重启分支均未实跑。

## 五、断点续跑说明

新会话收到本指令时：先读本文件与 `engine.md`。已完成的代码与部署无需重做；需要继续时按 engine.md §八 的剩余项处理，或按使用者新指令修改。

## 六、M6 更新（2026-11-24）

- 停机顺序（D8）、看门狗规则与白名单 sudo（D9）、策略权益与专用账户 / 密钥权限（D10）、systemd 加固与 PIN 文件（D11）已修复，详见 `review_log.md`。
- 引擎对齐研究框架：组合层直接调用 `trade.research.portfolio.plan_positions`（模式 B / β 中性）、行情含 `mark_1h` / 主动买入 / `premium_1h`、止损基准与影子盘止损按标记价格。
- 部署：服务器 `HEAD = 982df27`，看板 active 且 `curl 127.0.0.1:8501` = 200，看门狗 timer active，`/etc/sudoers.d/trade-watchdog` 解析 OK；看门狗实测重启一次、冷却内与停机标志下均不重启。
- 测试与文件清单见 `engine.md` §六、§七。

