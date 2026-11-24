# 一次性迁移进度（M1~M7）

执行文件：`指令/自动多轮/0-自动调度-每次发这个.txt` 第六部分。每完成一步即提交推送并更新本文件；
中断后再次执行从第一个未完成的步骤继续。

| 步骤 | 状态 | 提交 |
| :-- | :-- | :-- |
| M1 服务器改为 git 副本 | 完成 | `816c31b` |
| M2 配置（risk / equity / 风险档位 / 500 USDT） | 完成 | `35ee65a` |
| M3 数据补充与防幸存者偏差 | 完成 | `18cbdee` |
| M4 研究框架修复（D1~D7、D12、D13）与可信度测试 | 完成 | `9c82edc`、`a2bafb4` |
| M5 仓位模式 B 与 β 中性 | 完成 | `5381f16` |
| M6 实盘安全修复（D8~D11）与引擎对齐 | 完成 | `982df27` |
| M7 旧判定作废并准备重跑 | 完成 | `（本次提交）` |



## 当前状态

- M6 已完成（代码 `66b8c70`、`982df27`；部署 `982df27`）：
  - **D8 紧急停机顺序**：`killswitch.kill_switch` 改为 ① 写停机标志 → ② 调用 `stop_runner` 停止并确认实例（未确认不操作账户）→ ③ 只撤普通挂单（保留止损）→ ④ 市价平仓并复核到零 → ⑤ 撤剩余条件单 → ⑥ 删除心跳；仍有持仓时保留止损并报错。`scripts/kill_switch.py` 用 `systemctl stop` + `is-active` 确认，失败时 `sudo -n` 重试，仍失败即中止。
  - **D9 看门狗**：读 `halt_flags`、`systemctl is-enabled`、`is-active` 与 `Result`；有停机标志 / 单元未启用 / 主动停止（Result=success）只告警；第一次重启后 24 小时内第二次超时只告警（状态文件读回修复）；只重启 `trade-runner*`，经 `sudo -n /usr/local/sbin/trade-restart-unit`（白名单校验脚本，实例名从标准输入读，sudoers 规则无通配符）。实测：超时重启一次、随后 30 秒内再次超时只告警（`ActiveEnterTimestamp` 不变）、写入停机标志后只告警。
  - **D10 策略权益与专用账户**：`runner._equity` 改为 `min(启动余额, live_capital_cap) + 启动以来已实现 + 未实现`（基准写入 `equity_baseline`）；影子盘权益 = `initial_equity + 累计已实现（含开仓手续费）+ 未实现`；live 启动新增专用账户检查（外部持仓 / 普通挂单 / 余额 1.5 倍上限）与 `env_file` / 私钥 600 权限检查。
  - **D11 systemd**：交易与看板单元 `Restart=on-failure`、直接调用 venv python、`NoNewPrivileges/PrivateTmp/ProtectSystem=strict/ProtectHome=read-only/ReadWritePaths`；看门狗单元因 sudo 例外改用 `ProtectSystem=full`；看板控制 PIN 改从 `control.pin_file` 读取。
  - **引擎对齐**：引擎直接调用 `trade.research.portfolio.plan_positions`（含模式 B 与 β 中性）；候选文件可选 `sizing_mode` / `risk_tier`；行情新增标记价格（`markPriceKlines`）与主动买入（`taker_buy_*`），`with_premium` 时补溢价指数；止损触发价改用决策整点前一根已收盘 `mark_1h` 收盘价，影子盘止损按 `mark_1h` 且在已收盘 K 线上判断。
  - **顺带修复的新缺陷**：D16 影子盘权益重复计未实现；D17 `risk_state` 行不存在时 `update_risk_state` 静默丢弃已实现盈亏（改为 upsert）；D18 影子盘止损误用未收盘 K 线。
  - **测试**：新增 `test_killswitch.py`（顺序）、`test_watchdog.py`、`test_runner_equity.py`、`test_deploy_units.py`、`test_feed_parity.py`、`test_runner_alignment.py`；修复前在 `51e3af8` 工作树复现 17 项失败（killswitch 9、watchdog 7、deploy 5 中的 4 项）与 `check_secret_permissions` 导入失败。
  - **质量命令**：`ruff format --check .`（374 files）、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`（56 files）、`mypy src/trade/dashboard`（8 files）通过；`pytest -q` = `1 failed, 861 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有测试）。`scripts/check_docs_sections.py` 通过（基线已有失效引用 20 条）。
  - **服务器部署（`HEAD = 982df27`）**：看板 active、`curl 127.0.0.1:8501` = 200、ExecStart 为直接调用 python；看门狗 timer active；`visudo` 解析 OK；白名单脚本拒绝非 trade-runner 单元。engine.md 已更新文件清单与 SHA256。
- 下一步：M1~M7 全部完成，R1~R10 按“普通研究轮次”重跑（第四部分第 8 条 a），顺序 R3 → R1 → R2 → R4 → R5 → R6~R10（0 号调度第六部分 M7 第 3 条）。
- 推送：本地 `develop` 已到 `982df27`（代码/部署），M6 交接文档另行提交。

- M5 已完成（代码 `5381f16`）：
  - 仓位模式 B（`vol_target`）：按过去 `vol_lookback_days` 天日收益标准差的倒数分配权重，用过去
    `cov_lookback_days` 天日收益协方差的组合事前波动缩放到 `target_vol`；防灾止损固定 `execution.stop_max`，
    单仓名义 ≤ 权益 × `max_risk_per_trade` ÷ `stop_max`；中性策略按 60 天 β 调整两边名义，保证组合事前
    |β| ≤ `beta_abs_max` 且多头总名义 ÷ 空头总名义在 `ls_ratio_range` 内。
  - 配置项 `sizing_mode`、`target_vol`、`vol_lookback_days: 30`、`cov_lookback_days: 60`、`beta_lookback_days: 60`、
    `beta_abs_max: 0.10`、`ls_ratio_range: [0.5, 2.0]` 进入 `SizingConfig`（默认保持模式 A）与
    `configs/sizing_research.yaml`；`with_tier()` 用选中档位的 `target_vol` 覆盖。删除不再使用的 `portfolio.neutral_max_gap`。
  - 指标新增实现 β 与 alpha 夏普，达标项新增 j 款（中性策略 |β| ≤ 0.30 且 alpha 夏普 ≥ 0.8）。
  - 测试：`tests/research` 50 项全通过（新增 `test_metrics.py`、模式 B 手算 / 单仓上限 / β 中性 / β 样本不足、
    `test_h5_引擎按段使用模式B`、`test_h6_中性策略的β调整在引擎中生效`、配置项校验、取整状态一致性）；
    `pytest -q` = `1 failed, 826 passed, 3 skipped`，唯一失败为本机无 CUDA 的
    `tests/models/test_dataset.py::test_pinned_cpu_placement`（准备阶段既有失败，未修改测试）。
  - 服务器 `HEAD = 5381f16` 与本地一致；最小规模运行（R4 第 1 组参数 × 研究窗口最后 3 个月，模式 A、B 各一次）：
    - 模式 A（`fixed_risk`）：信号口径 448 笔、丢弃 0、年化夏普 -2.618、实现 β 0.086、alpha 夏普 -2.188；
      可执行口径 414 笔、应开仓 452、丢弃 38（8.4%）、年化夏普 -3.387、实现 β 0.082。
      结果文件 `artifacts/reports/smoke/20261124T144439Z.json`。
    - 模式 B（`vol_target`）：信号口径 442 笔、应开仓 445、丢弃 3（0.67%）、年化夏普 -1.178、实现 β 0.008、
      alpha 夏普 -1.118；可执行口径 371 笔、应开仓 464、丢弃 93（20.04%）、年化夏普 -0.632、实现 β 0.017。
      结果文件 `artifacts/reports/smoke/20261124T144647Z.json`。
    - 两次运行的 `funding_estimated` 分别为 28 / 4，`funding_missing 0`，`mark_fallback` 128 / 104；
      均未写 `EXPERIMENTS.log`（`kind=tuning` 仍为 65 条）。
- 下一步（M6）：实盘安全修复 D8~D11 与引擎对齐（紧急停机顺序、看门狗、专用账户与密钥文件权限、systemd 加固，
  引擎复用研究框架与模式 B / β 中性，实时数据视图含 `mark_1h` / flow / premium）。
- 推送：`origin/develop` 已到 `5381f16`，无未推送提交。


## M4 前置事实（2026-11-24 核查）




## M3 前置事实（2026-11-24 核查，只读）

- `data/universe_hist` 现有 72 个条目（含 `aliases.json`、`summary.json`），月份文件覆盖 2020-10 ~ 2026-07；
  M3 需为 2021-01 ~ 2026-07 重新生成快照，旧文件先复制到 `data/universe_hist_pre_m3/`（data/ 下的覆盖由
  M3 第 2 条授权，复制清单写入 `decisions.md`）。
- `artifacts/exchange_info/` 有 3 个历史快照，可作为交易规则来源②；其余下架币按来源③合成。
- 现有数据体量：`data/klines_1h` 551 MB、`data/klines_1d` 162 MB、`data/funding` 121 MB；磁盘剩余 37 GB。
- 需新增的三类数据目录尚不存在：`data/klines_1h_flow/`、`data/premium_1h/`、`data/mark_1h/`，
  对应 manifest 为 `data/history/manifest_flow.json`、`manifest_premium.json`、`manifest_mark.json`。
- `research_end` = 2026-07-31，下载月份上限为 2026-07（不得越过）。

## M1 记录

- 备份：服务器 `~/trade/artifacts/server_backup/20261124T104404Z.tar.zst`，552 个文件，1.79 MB
  （排除 `data/`、`artifacts/`、`.venv`、`__pycache__`）。
- 差异核对：服务器 552 个非数据文件 vs 本地 develop `b59c437`，25 处差异、无服务器独有较新代码
  （清单见 `decisions.md`）。
- 转换：服务器 `~/trade` 执行 `git init` 后从 bundle `git fetch`，`git checkout -f --detach b59c437`；
  `git rev-parse HEAD` = `b59c4375361e99709fa3c1541a29e1a1dd95c530` 与本地一致。
- `data/history/manifest.json` SHA256 转换前后均为 `bcb2dca37c5581416d5ceaaf17c3d40d98b5c9b6b75739dc4030a11cf27840ee`；
  服务器无 `.env.*` 与私钥文件（尚无实盘密钥），`~/.ssh/authorized_keys` 已是 600。
- 看板与看门狗：转换前 `systemctl is-active trade-dashboard trade-watchdog.timer` 均为 active，
  转换后 `curl 127.0.0.1:8501` 返回 200，无需重启。
- 服务器 `.git/info/exclude` 追加历史遗留目录与临时脚本（`.bak-code/`、`tmp_deploy/`、`_*.py` 等），
  保证服务器工作区 `git status` 干净；未执行任何 `git clean`。

## M7 记录（2026-11-24）

- `ledger.yaml`：R1~R10 的 `status` 改回 `pending`、`trials` 改为 0、`result: null`；原判定整体移入 `void`（含原 status / trials / result 与 reason）；`trials_used` 保持 65、`global_trial_budget` 400；`qualified_pool` / `candidates` 仍为空。
- `rounds/R1.md` ~ `R10.md`：顶部加“首次判定已作废（迁移 M1~M7）”，文末新增“重跑（迁移后）”一节（当前写“未运行”）；原内容保留。
- 网格：`configs/strategies/round_R1.yaml` ~ `round_R10.yaml` 的每个参数组乘 `sizing_mode ∈ {fixed_risk, vol_target}`（1 号第八部分开头），组数 24/12/16/12/8/18/12/12/8/8 = 130 组（原 65 组 × 2）；未增删其他参数、方向、规则或选币范围。
- R5 的成分网格：`component_params_by_fold` 读取成分参数时去掉 `sizing_mode` 并去重（R5 的仓位模式由 R5 自己的网格决定），新增 `tests/research/test_component_params.py`。
- 一致性检查口径（M7 后）：`trials_used = Σ rounds[].trials + Σ rounds[].void.trials + 冻结仓位选择的试验`（M7 规定 trials_used 不清零）；当前 0 + 65 + 0 = 65，与服务器 `EXPERIMENTS.log` 的 `kind=tuning` 65 条一致。
- 风险包络只按使用者决定（M2~M5）；杠杆 5x、逐仓、单向、position_frac、max_positions、R7 规则与 liq_cap 未改。
- 质量命令：`ruff format --check .`（375 files）、`ruff check .`、`mypy --strict`（56 files）与 `tests/research`（51 passed）通过；全量 `pytest` 结果见提交前记录。

