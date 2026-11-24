结论：ENGINE_READY

# 通用低频实盘引擎与看板（P5 / P6，M6 对齐后）

执行文件：`指令/自动多轮/3-实盘工程与看板.txt`；对齐与安全修复见 `0-自动调度-每次发这个.txt` 第六部分 M6。
最近一次部署验证：2026-11-24 15:49 UTC（服务器 `HEAD = 982df27`）。

## 一、架构

- 模式：`shadow`（正式网公开行情，只记录）、`paper`（币安模拟盘下单）、`live`（正式网真钱）。
- 入口：`scripts/live_runner.py --mode {shadow|paper|live} --candidate <候选ID>`；systemd 模板实例用 `--instance <候选ID>-<shadow|paper>`。一次一个候选。
- 主循环：整点后 `engine.rebalance_delay_sec` 秒拉取刚收盘的 1h K 线 → 影子盘结算 → 巡检 → 调仓（对齐策略声明的调仓周期）→ 心跳 → 日报。
- 目标仓位对账：`trade.live.targets.plan_delta` 只执行“目标 − 实际”的差额，平仓 / 减仓先于开仓；实际持仓一律以交易所或状态库虚拟持仓为准，调仓后写入 `strategy_positions`（重启对账与专用账户检查的依据）。
- 行情：`trade.live.feed.LiveFeed` 用 REST 回补 1h / 1d、资金费、标记价格（`markPriceKlines`）与（`engine.with_premium` 时）溢价指数（`premiumIndexKlines`），1h 的 `taker_buy_*` 组成 `flow`，拼成与研究框架同构的 `MarketData`；策略通过 `View` 读取已收盘数据。
- 执行：`trade.live.execution.ExchangeExecutor` 两档 post-only（GTX）→ IOC → 市价兜底；`ShadowExecutor` 在 `open_time = 决策整点` 的 1h K 线开盘价加半价差与滑点成交。
- 止损：`trade.live.stops.StopManager` 挂 STOP_MARKET（`algoType=CONDITIONAL`、`workingType=MARK_PRICE`、`reduceOnly`）；触发价以决策整点前一根已收盘 `mark_1h` 收盘价为基准；先挂新后撤旧，未确认即平仓。影子盘止损同样按 `mark_1h` 的 low/high 并在已收盘 K 线上判断。
- 风控：`trade.live.riskguard.RiskGuard` 复用 `trade.common.risk_rules` 的 R5~R8（实盘 / 影子盘固定 `strict`），另加行情过期、下单错误、时钟漂移、净敞口、非交易状态规则；安全模式为只平不开。
- 策略权益（R5~R8 与仓位共用）：`min(启动时钱包余额, live_capital_cap) + 启动以来已实现 + 未实现盈亏`，启动基准写入 `equity_baseline`，不使用账户总余额；影子盘为 `initial_equity + 累计已实现（含开仓手续费）+ 未实现`。
- 控制与停机：`trade.live.control`（暂停开仓 / 恢复 / 请求平仓 / risk_per_trade 0.005~0.01）；`trade.live.killswitch` 顺序固定为 ① 写停机标志 → ② 停实例并确认 → ③ 撤普通挂单（保留止损）→ ④ 市价平仓并复核到零 → ⑤ 撤剩余条件单 → ⑥ 删心跳；仍有持仓时保留止损并报错。
- 看门狗：`scripts/live_watchdog.py` 读取 `<状态库目录>/<候选ID>-<模式>.heartbeat`；超时 → 重启一次；从第一次重启算起 24 小时内第二次超时只告警；有停机标志、单元未启用、实例被主动停止（`Result=success`）时只告警；重启经 `sudo -n /usr/local/sbin/trade-restart-unit`（白名单脚本，只接受 trade-runner 实例）。
- 状态库：SQLite `artifacts/live/state.db`；新增 `engine_status`、`rebalance_log`、`consistency_log`、`halt_flags`、`control_flags`、`shadow_positions`、`strategy_positions`、`equity_baseline`，旧表继续使用。
- 通知：复用 `trade.live.notifier`；未配置 WxPusher / 企业微信时为 no-op。

## 二、启动命令

```text
# 影子盘（正式网公开行情，只记录）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode shadow --candidate <候选ID>

# 模拟盘（demo-fapi.binance.com）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode paper --candidate <候选ID>

# 真钱（需要全部上线前置条件）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode live --candidate <候选ID> --i-understand-the-risk

# systemd 模板实例（shadow / paper）
systemctl start trade-runner@<候选ID>-shadow.service

# 清除停机标志（仅人工）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode live --candidate <候选ID> --clear-halt

# 紧急停机（需要停止 systemd 实例，建议以 sudo 运行）
sudo /home/ubuntu/trade-venv/bin/python scripts/kill_switch.py --candidate <候选ID> --mode live
```

状态库路径：`configs/live.yaml: storage.db_path`（默认 `artifacts/live/state.db`）。


## 三、部署文件

| 文件 | 用途 |
| :-- | :-- |
| `deploy/trade-runner@.service` | shadow / paper 实例（`--instance %i`），`MemoryMax=400M`，`Restart=on-failure` |
| `deploy/trade-runner-live@.service` | live 实例（实例名 `<候选ID>`），固定带 `--i-understand-the-risk`，`MemoryMax=500M` |
| `deploy/trade-dashboard.service` | 看板，只监听 `127.0.0.1`，`MemoryMax=300M` |
| `deploy/trade-watchdog.service` / `.timer` | 每 5 分钟检查心跳 |
| `deploy/sudoers-trade-watchdog` | 只允许执行 `/usr/local/sbin/trade-restart-unit`（无通配符） |
| `deploy/trade-restart-unit` | 从标准输入读实例名，只重启 trade-runner / trade-runner-live 实例 |

交易与看板单元加固：`NoNewPrivileges=true`、`PrivateTmp=true`、`ProtectSystem=strict`、`ProtectHome=read-only`、`ReadWritePaths=/home/ubuntu/trade/artifacts/live`、`ExecStart` 直接调用 venv 的 python；看门狗单元需要 sudo，不设 `NoNewPrivileges`，用 `ProtectSystem=full`。

服务器部署结果（2026-11-24 实测）：

- `trade-dashboard.service`：active；`ExecStart` 为 `/home/ubuntu/trade-venv/bin/python -m trade.dashboard --config configs/dashboard.yaml`（不再经 `bash -lc`）；`curl http://127.0.0.1:8501/` 返回 200。
- `trade-watchdog.timer`：active。
- `visudo -c -f /etc/sudoers.d/trade-watchdog` → `parsed OK`；白名单脚本对 `trade-dashboard.service` 返回 64（拒绝），对 `trade-runner@SMOKE-shadow.service` 可执行。
- 看门狗实测（临时实例 `trade-runner@SMOKE-shadow`）：心跳超时后重启一次（`ActiveEnterTimestamp` 由 23:47:23 → 23:49:08）；随后立即再次超时只告警（时间戳不变，24 小时冷却生效）；写入停机标志后只告警（时间戳不变）。测试实例、override、心跳与停机标志均已清理。

## 四、看板访问

- 电脑：`ssh -N -L 8501:127.0.0.1:8501 tokyo`，浏览器打开 `http://127.0.0.1:8501`。
- 手机：SSH App 配置同样的端口转发后访问。
- 页面：总览、持仓、调仓记录、一致性、研究、控制（6 个）。控制 PIN 优先取环境变量 `DASHBOARD_CONTROL_PIN`，未设置时读 `configs/dashboard.yaml: control.pin_file`（默认 `./.dashboard_pin`，权限 600，不进入 git；文件不存在时控制接口返回 503）。
- 盈亏遮蔽：`engine.hide_pnl_until_final=true` 时，shadow / paper 且候选 `final` 为 pending / running 时不显示虚拟收益与权益。

## 五、受保护模块清单

用 ast 递归解析 `scripts/portfolio_backtest.py` 与 `trade.strategies.registry` 的导入，受保护模块（本任务未修改）：

```text
trade.backtest.costs, trade.backtest.engine, trade.backtest.sanity,
trade.common._numba, trade.common.anchors, trade.common.batch, trade.common.config,
trade.common.costs, trade.common.entry, trade.common.exits, trade.common.funding,
trade.common.risk_rules, trade.common.sizing, trade.data.universe, trade.features.kline,
trade.research.config, trade.research.data, trade.research.engine,
trade.research.interface, trade.research.metrics, trade.research.pipeline,
trade.research.portfolio, trade.research.rolling, trade.research.view,
trade.strategies.*（10 个方向 + common + registry）
```

本任务只修改 `src/trade/live/**`、`src/trade/dashboard/**`、`scripts/` 下实盘与看板脚本、`configs/live.yaml`、`configs/dashboard.yaml`、`deploy/`、`docs/02`、`docs/07`、`docs/09`、`docs/10`、`docs/research/`。

## 六、测试结果（实际运行，2026-11-24）

- `ruff format --check .`：374 files already formatted。
- `ruff check .`：All checks passed。
- `mypy --strict src/trade/common src/trade/backtest src/trade/live`：Success（56 files）。
- `mypy src/trade/dashboard`：Success（8 files）。
- `pytest -q`：`1 failed, 861 passed, 3 skipped, 16 warnings in 147.38s`。唯一失败为 `tests/models/test_dataset.py::test_pinned_cpu_placement`（本机无 CUDA，准备阶段既有失败，未修改测试）。
- M6 新增与扩展的测试（全部通过）：
  - `tests/live/test_killswitch.py`：停机顺序（停实例 → 撤普通挂单 → 平仓 → 撤止损）、平仓完成前止损仍在、实例未确认停止时不动账户、删除心跳、仍有持仓时保留止损。
  - `tests/live/test_watchdog.py`：停机标志 / 主动停止 / 单元未启用时不重启、24 小时冷却、白名单命令、sudo 执行、状态文件读回。
  - `tests/live/test_runner_equity.py`：策略权益按上限与启动以来盈亏、专用账户三类拒绝、密钥文件 600 与属主检查、影子盘权益不重复计未实现、状态库新表。
  - `tests/live/test_deploy_units.py`：`Restart=on-failure`、直接调用 venv python、加固项、看门狗单元 sudo 例外、sudoers 与白名单脚本。
  - `tests/live/test_feed_parity.py`：REST 拼出的 1h / 标记价格 / 主动买入 / 溢价指数 / 资金费与研究数据层逐值一致。
  - `tests/live/test_runner_alignment.py`：组合层直接复用 `trade.research.portfolio.plan_positions`、模式 B 走研究组合层、影子盘按决策整点开盘价成交、影子盘止损只用已收盘 K 线。

未运行项及原因：
- 没有候选，未启动任何交易或影子盘实例；`live` 的真实账户启动检查（专用账户、密钥权限、API 权限）只在测试 fixture 上验证。
- 未向币安正式网或模拟盘发送任何下单、撤单、改杠杆请求；只做只读连通性与部署验证。
- 未做 SSH 隧道浏览器实测（服务器本机 `curl` 返回 200）。

## 七、本任务文件清单与 SHA256（M6 后）

| 文件 | SHA256 |
| :-- | :-- |
| src/trade/live/killswitch.py | c40ab6e9478eea12177ae203cd4998153ae7325ebfea4c86b1b9d0220bf6cda2 |
| src/trade/live/runner.py | d1e81c61f00e835119caf8fd8c422ece73ad098fd99ae576456abf12a55d414a |
| src/trade/live/feed.py | 91c4c399c4f79ed63f879b6e5f612d28a25a1e0d073c2285014ad382507fc563 |
| src/trade/live/candidate.py | 39f6a54e16f34465762ab9aab1486a0681e91de63d2f6c02fa2aa2eaefecc73c |
| src/trade/live/config.py | d860abbd9ac09e1b60ff4940690153d0d6116a55a58a494b351f0acf09e93f62 |
| src/trade/live/credentials.py | ac9571450b943c5d739f07f3dffad1df2fb95703f6f9603e63f720af37b69734 |
| src/trade/live/store.py | 3d05f66add7c56a43bd7f549f11bfa568bb0837a987bc2a50f39db5c8f06cb5c |
| src/trade/live/execution.py | 2e45435f6038a01543ab5b8d4a622800e73c0239b61d36c23ce4ad6e66e2def0 |
| src/trade/live/exchange/client.py | 378e9eb75b8c60c0851221a75f9a2b02fd4454e8b33e2983d80694a67a35480e |
| src/trade/dashboard/app.py | 3298a04580a912e7a3bea8f8bcf6e3da6c7142dd38e9a00b9c8112b05129af94 |
| src/trade/dashboard/config.py | 44cb1eb995c060566342a81744e92b26d78cb669e746d2389ffada4c97d78e2a |
| scripts/live_runner.py | 525cf72f4eb09536525806581d32df0c7d8b53a45761cefefb76e7114b91b8b8 |
| scripts/live_watchdog.py | 27bc94e8eaaba3b1ded23a2da02c1865058a0953197bfdf3936b12f018d466ab |
| scripts/kill_switch.py | 9ff4ad43f80fce2b5fd1855dd7684f9fa291a7949b9de06c42361670e3e2f524 |
| configs/live.yaml | 161cdac6ac3fa55c50f7ab75312f6f73d869df483f15336e52489f5412df292c |
| configs/dashboard.yaml | a9ba1f88aa74294e3ea015b6286ec33f2000f8483b10715d5f0b88610677e1d5 |
| deploy/trade-runner@.service | d32c86da3f86b4b3329c4e6ae41c38c4fa2cd79e4a4a2068db72ae6da77b99f2 |
| deploy/trade-runner-live@.service | ecd5d1c25ef23b53a884ace516932e1762786f941a85c0c16c13686e69080916 |
| deploy/trade-dashboard.service | 7c62ae59b48512bafe459edaa4e76507ee9b8785bf814cf7e1369aae84bcf271 |
| deploy/trade-watchdog.service | 0f57a3f26a158303df050efeeacfca9c172e524b11cd3336232259a2ce6dc7c8 |
| deploy/trade-watchdog.timer | d9d2b8ad87cd4673b6f9390003d9d3818f38d7205002348829e4fe3a5efd26eb |
| deploy/sudoers-trade-watchdog | 310f6f3172f018b322066c1c5902f593d9514a932b1aa3de819b373eeca3fa93 |
| deploy/trade-restart-unit | 95066cc84720e12d1a298d77e9688987edfb9f925059c5b0bd80fd5196d50463 |

## 八、未完成项与使用者需要处理的事

1. **没有候选**：总账 `candidates: []`，引擎未启动任何实例；`paper` / `live` 的下单与止损流程只在测试 fixture 上验证。
2. **当月选币快照**：服务器 `data/universe_hist` 只到 `2026-07.json`，启动 shadow 前需要按月生成当期快照（研究循环 / `scripts/build_universe_hist.py`）。
3. **密钥与 PIN**：`/home/ubuntu/trade/.env.live` 与 `.dashboard_pin` 未创建（不写入仓库）；PIN 现在由 `configs/dashboard.yaml: control.pin_file` 读取，文件权限须 600。
4. **真实账户**：专用账户、余额 1.5 倍、密钥 600、API 权限与逐仓 / 单向 / 杠杆只在代码与测试层验证，未用真实 Key 运行。
5. **kill_switch 需要停止 systemd 实例**：以 `sudo` 运行（普通用户无法停止系统单元；sudoers 只授予看门狗的重启脚本，未授予 stop）。
6. **服务器账号权限**：ubuntu 账号本身有云镜像默认的管理员 sudo；看门狗的 sudoers 规则只声明最小命令，实际 OS 层权限仍由云镜像决定。

