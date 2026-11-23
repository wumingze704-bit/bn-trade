结论：ENGINE_READY

# 通用低频实盘引擎与看板（P5 / P6）

执行文件：`指令/自动多轮/3_实盘工程与看板.txt`。时间：2026-11-23（UTC 14:30~15:00 左右完成部署验证）。

## 一、架构

- 模式：`shadow`（正式网公开行情，只记录）、`paper`（币安模拟盘下单）、`live`（正式网真钱）。
- 入口：`scripts/live_runner.py --mode {shadow|paper|live} --candidate <候选ID>`；一次一个候选。
- 主循环：整点后 `engine.rebalance_delay_sec` 秒拉取刚收盘的 1h K 线 → 影子盘结算 → 巡检 → 调仓（对齐策略声明的调仓周期）→ 心跳 → 日报。
- 目标仓位对账：`trade.live.targets.plan_delta` 只执行“目标 − 实际”的差额，平仓 / 减仓先于开仓；实际持仓一律以交易所或状态库虚拟持仓为准。
- 行情：`trade.live.feed.LiveFeed` 用 REST 回补（1h 45 天、1d 120 天、资金费 1000 条），拼成与研究框架同构的 `MarketData`，策略通过 `View` 读取已收盘数据。
- 执行：`trade.live.execution.ExchangeExecutor` 两档 post-only（GTX）→ IOC → 市价兜底；`ShadowExecutor` 按研究口径在下一根 1h 收盘后以该根开盘价加半价差与滑点成交。
- 止损：`trade.live.stops.StopManager` 挂 STOP_MARKET（`algoType=CONDITIONAL`、`workingType=MARK_PRICE`、`reduceOnly`），先挂新后撤旧，未确认即平仓。
- 风控：`trade.live.riskguard.RiskGuard` 复用 `trade.common.risk_rules` 的 R5~R8，另加行情过期、下单错误、时钟漂移、净敞口、非交易状态规则；安全模式为只平不开。
- 控制与停机：`trade.live.control`（暂停开仓 / 恢复 / 请求平仓 / risk_per_trade 0.005~0.01）与 `trade.live.killswitch`（写停机标志 → 撤单 → 撤条件单 → 平仓 → 确认）。
- 状态库：SQLite `artifacts/live/state.db`；新增 `engine_status`、`rebalance_log`、`consistency_log`、`halt_flags`、`control_flags`、`shadow_positions`，旧表继续使用。
- 通知：复用 `trade.live.notifier`；未配置 WxPusher / 企业微信时为 no-op。
- 看门狗：`scripts/live_watchdog.py` 读取 `<状态库目录>/<候选ID>-<模式>.heartbeat`，15 分钟无心跳重启一次，1 小时内第二次只告警。

## 二、启动命令

```text
# 影子盘（正式网公开行情，只记录）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode shadow --candidate <候选ID>

# 模拟盘（demo-fapi.binance.com）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode paper --candidate <候选ID>

# 真钱（需要全部上线前置条件）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode live --candidate <候选ID> --i-understand-the-risk

# 清除停机标志（仅人工）
/home/ubuntu/trade-venv/bin/python scripts/live_runner.py --mode live --candidate <候选ID> --clear-halt

# 紧急停机
/home/ubuntu/trade-venv/bin/python scripts/kill_switch.py --candidate <候选ID> --mode live

# 运行中控制
/home/ubuntu/trade-venv/bin/python scripts/set_sizing.py --candidate <候选ID> --pause-open
```

状态库路径：`configs/live.yaml: storage.db_path`（默认 `artifacts/live/state.db`）。

## 三、部署文件

| 文件 | 用途 |
| :-- | :-- |
| `deploy/trade-runner@.service` | shadow / paper 实例（实例名 `<候选ID>-<模式>`），`MemoryMax=400M`，`Restart=always` |
| `deploy/trade-runner-live@.service` | live 实例（实例名 `<候选ID>`），`MemoryMax=500M`，固定带 `--i-understand-the-risk` |
| `deploy/trade-dashboard.service` | 看板，只监听 `127.0.0.1`，`MemoryMax=300M` |
| `deploy/trade-watchdog.service` / `.timer` | 每 5 分钟检查心跳 |

服务器部署结果（2026-11-23）：

- `trade-dashboard.service`：active (running)，Main PID 361254，内存约 100.5M；`curl -I http://127.0.0.1:8501/` 返回 `HTTP/1.1 200 OK`，`/api/status` 返回 `live_available=false`（状态库尚未创建）。
- `trade-watchdog.timer`：active。
- 交易实例未启动：总账 `candidates: []`，没有可运行的候选（按指令只部署看板与看门狗，不启动交易或影子盘）。
- 服务器 venv 补装 `fastapi 0.165.0`、`uvicorn 0.54.0`（`uv pip install`，属于本任务交付依赖）。

## 四、看板访问

- 电脑：`ssh -N -L 8501:127.0.0.1:8501 tokyo`，浏览器打开 `http://127.0.0.1:8501`。
- 手机：SSH App 配置同样端口转发后访问 `http://127.0.0.1:8501`。
- 页面：总览、持仓、调仓记录、一致性、研究、控制（6 个）。控制操作需要 `DASHBOARD_CONTROL_PIN`；服务从 `/home/ubuntu/trade/.dashboard_pin` 读取（文件不存在时控制接口返回 503）。
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

本任务只修改 `src/trade/live/**`、`src/trade/dashboard/**`、`scripts/` 下实盘与看板脚本、`configs/live.yaml`、`configs/dashboard.yaml`、`deploy/`、`docs/07`、`docs/09`、`docs/10`、`docs/02`、`docs/research/`。

## 六、测试结果（实际运行）

- `ruff format --check .`：358 files already formatted。
- `ruff check .`：All checks passed。
- `mypy --strict src/trade/common src/trade/backtest src/trade/live`：Success（56 files）。
- `mypy src/trade/dashboard`：Success（8 files）。
- `pytest -q`：`1 failed, 776 passed, 3 skipped, 16 warnings in 124.87s`。唯一失败为 `tests/models/test_dataset.py::test_pinned_cpu_placement`（本机无 CUDA，准备阶段既有失败，未修改测试）。
- 新增测试（43 个，全部通过）：候选冻结与上线前置条件、目标仓位对账、挂单流程与 IOC / 市价兜底、订单状态未知查询、止损方向取整与先新后旧、R5~R8 与健康规则、紧急停机、行情组装与研究数据视图一致、看板引擎接口与控制 PIN。
- 服务器静态检查：`python scripts/check_live_env.py --config configs/live.yaml` → `[OK] 配置可加载；资金上限 100.0 USDT；历史回补 45/120 天`。

未运行项及原因：
- 没有候选，无法跑“冻结候选的历史回放与 `portfolio_backtest` 逐条一致”测试；已用影子盘成交价与研究口径的单测覆盖公式。
- 未向币安正式网或模拟盘发送任何下单、撤单、改杠杆请求；按指令只在服务器做只读检查（dashboard 为本地状态库读取）。
- 未做 SSH 隧道浏览器实测；已用服务器本机 `curl` 验证 200。
- 未测试手机 SSH 隧道。

## 七、本任务文件清单与 SHA256

| 文件 | SHA256 |
| :-- | :-- |
| src/trade/live/candidate.py | c09faee1b638b71025ec92cb9c1f91fd4e7522035a285b3e6d10060040736f07 |
| src/trade/live/feed.py | 041f10248aef2fd12df28999da63b34511b55a0741825ff68950e8b499565402 |
| src/trade/live/targets.py | 4283c71725bd16d4d2c572832e9b7b4a753b2831807603c8b67e3f0b77e036fa |
| src/trade/live/execution.py | 41ff929a814e16eacc87905556d349263a67db5af0ff442893bb29b12e46a572 |
| src/trade/live/stops.py | 0242ff6b66e835bfc2a51d15e1c17354f565fe69a73c8d39e9ef9f0474b60120 |
| src/trade/live/riskguard.py | 0edb9a5cefd8b9955aac77ecb1711d744a6926b995b5e6f311ef8cdaf130d30d |
| src/trade/live/runner.py | 7126f77dcd76f880e340e99795283297a944c10a22a0a33446c5069fdbdd3761 |
| src/trade/live/killswitch.py | 95af3f419b135725897e4e532a0662f30733b270265213045642d37e62cf6a59 |
| src/trade/live/credentials.py | 16123a5ba80b9e89b68fe43131e66c040cb4c78bacf89ff427f0eb07f8cd943b |
| src/trade/live/units.py | 69ee3de08e39132ccfce094167bca55d6c2c10bc3f6ce6d173655e910e37a59a |
| src/trade/live/config.py | 1ac188d22393bbe541ce4de967c96b26053b14969ed39835cbe43a1ad6ed5705 |
| src/trade/live/control.py | b985755996285b8b5e4e9def123a3dd6794c22f48e14b50d8e18e7e4c31dc32b |
| src/trade/live/store.py | 290da892bee7b06920888048ae07afac23549939233ad5cb39a4ba9abae3fa89 |
| src/trade/live/exchange/client.py | 43960bd1ffa1b344748d6943b919d6cbd42555c56f1c24e18f402b0ee7841516 |
| src/trade/live/exchange/endpoints.py | eae54c8eade4394b6847a1fcc7b0f9785ebdd937e303896c6ab6eefca2925439 |
| src/trade/dashboard/app.py | 8c8c3c64ba17ee3acee1dacd2e1a8391c3f38156b108d6e8ba63b697ae5d5b42 |
| src/trade/dashboard/config.py | 3bf3fee3be98186c8a17d36abcc20fdff557be0365d0ad1a0564a62349c8c83d |
| src/trade/dashboard/state_db.py | 5a653a757019de8cccba7de570eea3fc93a915876a477ac993cbe954540e6d83 |
| src/trade/dashboard/static/index.html | 058142624193c566b85b91b7680e05e98220a836c35c7530bbf2465a7ff984d1 |
| src/trade/dashboard/static/app.js | e2b8a6cd5dcbbec3927cb6546820629948c7cac3a951476e174c9e6299f4b405 |
| src/trade/dashboard/static/styles.css | a3861b2a0288a47b9a9148d34f66da2ad0d5aca085a9d5912c177bceb0dbbcb9 |
| scripts/live_runner.py | b7d8b2928fd163f121fc1d0de18d7a536ea4a3c17c8f037fb2485d8e6173d437 |
| scripts/live_watchdog.py | 01c43146d78c88f45e695efa80c30ce37d705307dc8d3943c5f027ea4a66d294 |
| scripts/kill_switch.py | 6a00a9f2d9716fcbc49a801f25ab4ceb584a012ed0de74ea94e7028598a6b833 |
| scripts/set_sizing.py | 126c444134964cb9293d8d98147ae30e462cfc50b363c8fb0435bd721bde9f98 |
| scripts/check_live_env.py | 3aa1c1f487d3dc31b11ab64239b5d4c784385fd258fd98937ff30565ed5b12e8 |
| configs/live.yaml | 9ec7e209f3efbe9b0bc7b29aec975314105acc5f228d54ff6d07acc1d8cfeca6 |
| configs/dashboard.yaml | c6e0fd0816e8390baecde8eb1af8a555695c6e289499138112abe4643cf7ed7d |

其他改动：`scripts/run_live.py` 用 `git rm` 删除（入口改为 `scripts/live_runner.py`）；`docs/07`、`docs/10` 旧版 `git mv` 到 `docs/archive/`；新增 `deploy/` 5 个文件与 8 个测试文件；`docs/09`、`docs/02` 局部同步。

## 八、未完成项与使用者需要处理的事

1. **没有候选**：总账 `candidates: []`，因此引擎未启动任何实例，`paper` / `live` 的下单与止损流程只在测试 fixture 上验证。研究产生候选后，按 §二命令启动 shadow。
2. **当月选币快照缺失**：服务器 `data/universe_hist` 只到 `2026-07.json`，`View.universe_symbols()` 在 2026-11 会抛 `KeyError`（引擎拒绝启动并带上下文）。启动 shadow 前需要按月生成当期选币快照（`scripts/build_universe_hist.py` / 研究循环）。
3. **密钥与 PIN**：`/home/ubuntu/trade/.env.live` 与 `.dashboard_pin` 未创建（不写入仓库）；live 密钥、模拟盘密钥、`DASHBOARD_CONTROL_PIN`、`WXPUSHER_*` 由使用者填写。
4. **未验证真实账户**：API 权限、费率、逐仓 / 单向 / 5x 的启动检查只在代码与测试层验证，未用真实 Key 运行。
5. **看门狗只验证 timer 存活**：没有实例心跳文件，重启分支未在服务器实跑。

