# 决策记录（共用，只追加）

## 2026-11-23 自动研究循环（首次运行）

### 1. 运行位置与研究数据

- 研究数据：2020-10 ~ 2026-07 的月度 1h/1d K 线与资金费，共 59,703 个文件、约 834 MB，
  全部来自 data.binance.vision 月度目录，CHECKSUM 校验通过，0 缺失、0 失败（`data/history/manifest.json`）。
- 选币快照：`data/universe_hist/` 重建 70 个月（2020-10 ~ 2026-07），规则与 `configs/universe.yaml` 一致；
  第 7 步质量门槛改为“R 前一个自然月 1h 缺失比例 ≤ 1%”。各月入选数：2020-10 ~ 2021-01 依次为
  0、0、8、9，此后 2021-02 起 ≥ 20（最多 60），最近 6 个月为 2026-02 48、2026-03 39、2026-04 38、
  2026-05 33、2026-06 35、2026-07 39。`research_start` 取第一个 ≥ 20 的月份 2021-02-01。
- 181 个币在 `artifacts/exchange_info/rules.json` 中没有交易规则（多为已下架且逐笔数据已清理的老合约，
  以及 TradFi 合约）。这些币无法取整与判定可交易性，统一标记 `excluded`，不进入 U(R)；名单写入
  `data/universe_hist/summary.json`。2020-10 的快照在补充该标记前生成，不用于研究（research_start 之后）。
- 运行位置：服务器上加载 310 个币、2021-01~2026-07 数据，峰值 RSS 630 MB、耗时 46 秒，空闲内存 2.5 GB，
  满足“峰值 ≤ 1.5 GB 且内存充足”，因此研究在东京服务器上运行（1 个进程，`nice -n 10`，tmux）。

### 2. 与旧框架的差异（有意偏差，已同步 docs/13）

- 旧回测的滑点用 1 分钟 `rv_15_raw`；本框架只有 1h 数据，改用过去 24 小时 1h 对数收益标准差
  作为波动代理：`slip = max(1bp, Y × rv_1h × sqrt(notional / mean_1h_quote_volume))`，
  `Y` 取 `configs/backtest.yaml: costs.Y`。已在 `docs/13-研究框架.md` §4 写明。
- 流动性上限按指令第二部分第 3 条：`单币名义 ≤ liq_cap × 该币 24 小时成交额 / 24`，
  `liq_cap` 取 `configs/sizing_research.yaml` 的 0.005；旧框架的 `mean_60` 口径不再使用。
- Deflated Sharpe 的 `trial_sharpes` 与 `sharpe` 统一用日频非年化夏普（指令第五部分 B 第 9 条），
  `n_obs` 为拼接曲线日数；与 `trade.backtest.sanity.deflated_sharpe` 的“同周期单位”要求一致。

### 3. 首轮 R1~R5 结果与关键约束

- R1~R5 首轮网格全部不达标：拼接年化夏普分别为 R1 −0.470、R2 −0.607、R3 无法选参（训练期无交易，
  全期最好 0.43）、R4 −0.939、R5 −1.078。逐组数字与诊断见 `docs/research/rounds/R1.md` ~ `R5.md`，
  报告在服务器 `artifacts/reports/R1~R5/grid.json`，试验登记 `artifacts/reports/EXPERIMENTS.log`。
- 关键约束（使用者确定的风险包络，Agent 不得修改）：100 USDT × `risk_per_trade` 0.0075 ÷ 15% 止损
  ≈ 4.9 USDT 名义，低于最小下单额 5 × 1.1 = 5.5，因此宽止损仓位全部被丢弃。R3 的 3 × ATR14(1d)
  几乎总是触及 15% 上限，2021-02~2022-02 没有交易，按第七部分第 3 条无法选参；R4 的多头一侧
  （近期下跌币，波动更大）也被大量丢弃。
- 所有轮次都未修订：网格内已覆盖可调参数，方向上做反向选择属于按结果挑参数，不符合第七部分第 6 条。
  各轮的决定与理由写在对应 `rounds/*.md` 的“判定”一节。

### 4. ledger 被同步覆盖后恢复

- 服务器 `~/trade` 不是 git 仓库，研究结果直接写在服务器上的 `docs/research/ledger.yaml`。在同步代码时
  用 tar 覆盖了该文件，导致 R1~R4 的 trials 与状态被重置；已按服务器 `EXPERIMENTS.log`（R1 12 条、
  R2 6 条、R3 8 条、R4 6 条、R5 4 条，合计 36 条 `kind=tuning`）恢复 `trials_used: 36` 与
  R1~R5 的 failed 状态与结果摘要。之后同步代码时排除 `docs/research/ledger.yaml`，以本地 git 版本为准，
  研究运行结束后再把服务器 ledger 复制回本地。


## 2026-11-23 一次性准备（00_一次性准备_最先发这个.txt）

### 1. 分支与 git

- 依据一次性准备任务指令授权自行决定：仓库实际开发分支名是 `devlop`（`origin/devlop`），远端不存在
  `develop`。指令中的“develop 分支”按实际分支 `devlop` 执行，不新建 `develop`，避免出现两条并行开发线。
- 准备前的未提交改动（`docs/rotation/decisions.md` 的修改与 `docs/rotation/progress_round2.md`、
  `docs/rotation/round2.md`、`scripts/rotation_grid_scan.py` 三个新文件）单独提交为 `chore: 准备前快照`，
  提交时排除 `指令/`（未跟踪）。`git pull --rebase origin devlop` 成功，没有需要合并的远端提交。
- 服务器 `~/trade` 不是 git 仓库（`git status` 报 `fatal: not a git repository`），因此无法 `git pull`；
  服务器代码同步按指令的备选方案用 `scripts/sync.ps1`（rsync/scp），并在完成报告中说明。

### 2. 指令文件

- 依据指令授权：`指令/` 下 7 个指令文件原为连字符文件名、放在 `指令/` 根下；`0-自动调度` 与本次任务都按
  `指令/自动多轮/` 加下划线文件名引用它们。已移动到 `指令/自动多轮/` 并改成指令中引用的名字
  （`00_一次性准备_最先发这个.txt`、`1_自动研究循环.txt`、`2_最终检验.txt`、`3_实盘工程与看板.txt`、
  `4_影子盘与上线评估.txt`、`5_实盘监控.txt`、`0_自动调度_每次发这个.txt`）。文件内容未改动。
- `指令/` 下没有其他旧指令文件夹（如第一轮、第二轮）或文件，无需删除。

### 3. 归档（全部用 `git mv`，保留历史）

移到 `docs/archive/`：

| 原路径 | 新路径 | 理由 |
| :--- | :--- | :--- |
| `docs/01-需求与范围.md` | `docs/archive/01-需求与范围.md` | 旧范围写的是只做多与分钟级模型 |
| `docs/04-特征与标签.md` | `docs/archive/04-特征与标签.md` | 分钟级特征与三重障碍标签，旧策略专用 |
| `docs/05-模型与训练.md` | `docs/archive/05-模型与训练.md` | PyTorch 分钟级模型，旧策略专用 |
| `docs/06-回测引擎.md` | `docs/archive/06-回测引擎.md` | 分钟级回测口径，与 1h 研究框架不同 |
| `docs/08-开发计划与验收.md` | `docs/archive/08-开发计划与验收.md` | 旧阶段 P0~P8 与闸门 G1~G6 |
| `docs/11-轮动策略.md` | `docs/archive/11-轮动策略.md` | 只做多轮动策略，已结束 |
| `docs/records.md` | `docs/archive/records.md` | 旧策略过程记录 |
| `docs/回测结果.md` | `docs/archive/回测结果.md` | 旧策略回测结果 |
| `docs/tasks.md` | `docs/archive/tasks.md` | 旧任务清单（先摘出 P5/P6 到 `docs/research/p5p6_backlog.md`） |
| `docs/rotation/`（9 个文件） | `docs/archive/rotation/` | 轮动策略的全部阶段与进度记录 |
| `universe_report.md`（仓库根） | `docs/archive/universe_report.md` | 旧选币快照报告 |

不存在的文件（指令中列为“若存在”）：`docs/12-*.md`、`docs/review.txt`、`docs/qa.txt`，无需处理。

### 4. 删除（git 操作与磁盘）

- `git rm`：`tmp_train_lgbm.yaml`、`_commit_msg.txt` 在工作区与 git 历史中都不存在（`Test-Path` 为 False），无需删除。
- `docs/archive/README.md` 新建，说明“已结束的旧策略文档，只供查阅”并列出归档清单。
- 新 `docs/tasks.md`（短文件）、`docs/00-当前目标.md`（≤ 60 行，只写规格）、`docs/research/p5p6_backlog.md`
  （从旧清单原样摘出 P5/P6 两节与两条相关待决定事项）新建；`docs/design.md` 重写为当前文档索引。

### 5. data/ 与 artifacts/ 删除（按指令第 4.10 条清单）

删除条件：服务器剩余空间 8.4 GB < 15 GB，必须删除；本地剩余 44.2 GB ≥ 30 GB，仍按清单删除
`data/features/`、`data/labels/`（这两项目的是减少噪音；本地没有 `data/raw/`）。

服务器（`~/trade`，删除时无进程使用：`ps -ef` 无 python 交易/下载进程，`systemctl list-units` 只有系统服务，
tmux 会话 `trade` 只有一个空闲 bash）：

| 目录 | 文件数 | 大小 | 删除时间（UTC） |
| :--- | ---: | ---: | :--- |
| `data/bars/` | 2149 | 8.7 G | 2026-11-23 11:30 UTC 前后 |
| `data/bars_r0_15/` | 1904 | 7.7 G | 2026-11-23 11:35 UTC 前后 |
| `data/features/` | 238 | 4.9 G | 2026-11-23 11:30 UTC 前后 |
| `data/labels/` | 2372 | 256 M | 2026-11-23 11:30 UTC 前后 |
| `data/raw/` | 61324 | 4.3 G | 2026-11-23 11:30 UTC 前后 |
| `data/raw_r0_15/` | 0 | 8.0 K | 2026-11-23 11:30 UTC 前后 |
| `artifacts/cache/` | 1641 | 3.1 G | 2026-11-23 11:35 UTC 前后 |
| `artifacts/bench/` | 38 | 2.2 M | 2026-11-23 11:30 UTC 前后 |
| `artifacts/signals/` | 716 | 164 M | 2026-11-23 11:30 UTC 前后 |

- `artifacts/models/`、`artifacts/quality/` 在服务器上不存在。
- `data/bars_r0_15/` 与 `artifacts/cache/` 首次 `rm -rf` 遇到 `Permission denied`：这些文件属主是 `root`
  （此前用 `sudo systemd-run --scope` 运行过），改用 `sudo rm -rf` 后删除。服务器剩余空间 8.4 G → 38 G。
- 保留：`data/universe/`（440 K）、`data/daily/`（55 M）、`data/raw_ioc/`（81 M，不在指令清单内，未删）、
  `artifacts/live/`（5.9 G，其中 `book/` 盘口录制用于校准点差）、`artifacts/reports/`、`artifacts/costs/`、
  `artifacts/exchange_info/`、`artifacts/symbols_*.txt`。

本地：

| 目录 | 文件数 | 大小 | 删除时间（本地） |
| :--- | ---: | ---: | :--- |
| `data/features/` | 2142 | 48.85 G | 2026-11-23 19:33 CST |
| `data/labels/` | 2133 | 2.42 G | 2026-11-23 19:33 CST |

- 本地 `data/bars/`（8.29 G）按指令“空间充足时可保留”保留；本地没有 `data/raw/`、`data/bars_r0_15/`、
  `data/raw_r0_15/`，`artifacts/` 为空目录。本地剩余空间 44.2 G → 94 G。

### 6. 仓库规范改写

- `AGENTS.md`：§1 改为“币安 USDⓈ-M 永续多策略研究与实盘工具，允许做多与做空”；§2 改为“读指令与
  `docs/research/` 交接文件 → 执行 → 质量检查 → 写交接与 decisions → 提交推送到 develop”；§5 改为按
  改动对象更新 `docs/13`、`docs/07`、`docs/09`、`docs/10`，只写规格，过程写入 `docs/research/decisions.md`；
  §6 红线由 15 条改为指令指定的 14 条；§10 改为按所执行指令的规定写报告、进度写入 `docs/research/`。
  §3、§4、§7、§8、§9 保留原有规范，只同步红线编号与文档章节引用。
- `.clinerules/00-core.md`、`10-docs.md`、`20-quant.md` 按新 AGENTS.md 重新生成，红线与 §5.1 对照表逐条照抄。
- `tests/test_agent_rules.py`：只改解析与期望常量（红线编号 1~14、量化子集 1/2/3/4/11/12/14、标题关键字
  “红线 1~14”、10-docs 改为校验 §5 同步规则与 §5.1 对照表），“逐条完全相等”的断言未放宽。
- `README.md` 开头改为新体系说明与“平时只发 `0_自动调度_每次发这个.txt`”，保留安装与质量检查命令。
- `scripts/check_docs_sections.py`：`docs/archive/` 下的归档文档也算“文档 §章节”引用的有效目标，
  新增的 `docs/00` 无基线时跳过前后比对并打印提示；过程记录改读 `docs/archive/records.md`。
- `.clineignore` 增加 `docs/archive/`；`.gitignore` 增加 `/指令/`（`指令/` 此前未被 git 跟踪，无需 `git rm --cached`）。

依据一次性准备任务指令授权自行决定：本任务要改写的规范文件（AGENTS.md、`.clinerules/`、
`tests/test_agent_rules.py` 的解析部分、`docs/tasks.md`、`docs/design.md`、README、`.clineignore`、`.gitignore`）
均属指令第三部分底线之外，按指令要求直接改写，不再另行征求确认。


依据使用者指示自行决定（2026-11-23，准备完成之后）：
分支名 `devlop` 是 `develop` 的拼写错误。已执行 `git branch -m devlop develop`，把本地分支改名为
`develop`、推送到 `origin/develop`，并删除远端拼写错误的分支 `origin/devlop`（`develop` 与 `devlop`
指向同一个提交，历史未改写）。AGENTS.md §2/§9、`.clinerules/00-core.md`、`docs/research/` 里的分支名
同步改为 `develop`；README 重写为当前体系的使用说明（调度用法、目录结构、文档入口、质量检查、数据与制品、
服务器与网络、分支与提交、安全与已知状态）。`docs/archive/tasks.md` 里的旧记录保持原样（归档文档不随代码改动更新）。

## 2026-11-23 自动研究循环（第二次运行，R6 起）

### 1. 新方向的设计取舍

依据自动研究循环指令授权自行决定：R6~R10 属于指令第八部分的 `ai_proposed` 轮次，允许新增策略实现与网格
（`src/trade/strategies/`、`configs/strategies/round_R6~R10.yaml`），规格同步写入 `docs/13-研究框架.md` §5。

新方向统一采用两个约束，来自 R1~R5 的实际数字而不是猜测：

- 低换手：R1 的 8h 调仓换手 229 倍、成本 / 毛收益 1.42；新方向先用 168h（周频）调仓，把成本压到 0.5~4.0 USDT。
- 紧止损：R3 的 3 × ATR14(1d) 几乎总触及 15% 上限，100 USDT × 0.75% 风险 ≈ 4.9 USDT 名义低于最小下单额
  5 × 1.1，训练期无交易；新方向沿用 3 × ATR14(4h)，单仓名义约 8~25 USDT。

### 2. R6（资金费调整的横截面动量）结果

- 9 组网格（λ ∈ {0,1,2} × L ∈ {14,28,56}，周频、K=5）全部不达标；选中组 L14 λ0 的训练夏普 1.631、
  训练期 61 笔，验证折 0 笔，拼接夏普 0.000。
- 单参数诊断：2021-10-07 当日亏损达 8%，触发 R6 `flatten_all`，`halt_mode: strict` 后全程不再交易；
  停机前权益从 100 升到 160.0，停机时约 140。
- `nohalt` 情景（关闭 R5~R8）在同参数下 4.5 年 +2.79%、年化夏普 0.124、最大回撤 41.6%、858 笔：
  即使不停机也不达标；`shuffle` 年化 −0.188。
- 结构性发现：`daily_loss_halt=8%` 与 `halt_mode: strict` 会让训练期的一次坏日子冻结全部验证折，
  这是使用者确定的 R5~R8 口径（红线 5），Agent 不修改；新方向只能通过更低波动或暴露缩放规避，
  因此暴露缩放作为 R8 的独立预登记方向。

### 3. 数据说明

2025-10 ~ 2026-07 曾被旧策略当作测试段看过一次（与 R1~R5 相同）；研究期 2021-02-01 ~ 2026-07-31 内所有
轮次都只读 `data/klines_1h`、`data/klines_1d`、`data/funding`、`data/universe_hist`。

### 4. R7~R10 结果（全部 FAILED）

| 轮次 | 方向 | 组数 | 拼接年化夏普 | 最大回撤 | 关键诊断 |
| :--- | :--- | ---: | ---: | ---: | :--- |
| R7 | 低波动异常多空 | 6 | −0.026 | 22.4% | 多头（低波动）−16.69、空头 +8.73，与假设相反 |
| R8 | 波动率择时动量 | 6 | 0.000 | 0.000 | 选中组训练期 2021-10-07 触发 R6 停机；σ0.5 组自身日频夏普最高 +0.031 |
| R9 | BTC 对冲单边动量 | 4 | −0.317 | 19.5% | 多头 −19.05、空头 +5.30；2022-08-19 触发 R8 停机 |
| R10 | 流动性分层动量 | 4 | −0.180 | 29.4% | 多头 −25.76、空头 +14.26；第 2 折后停机 |

- 与 R1~R6 相同的结构：`halt_mode: strict` 下一次 20% 回撤或单日 8% 亏损就会把后续验证折全部置零；
  R6、R8 在训练期被单日亏损停机，R7、R10 在前两折被回撤停机，R9 在第 2 折被回撤停机。
- `nohalt` 情景在 R7（+53.5%）、R9（+32.3%）、R10（+36.6%）上为正，但那是风控禁止的路径，
  不能作为达标依据；同一情景在 R6（+2.8%）、R8（−0.1%）上接近零，说明信号本身没有稳定的正期望。
- 所有轮次都未做“一次修订”：可解释的修订都落在降低风险 / 修改停机模式（使用者决定，红线 5）或
  在看到结果后挑方向（按结果拟合）两类，均不符合第七部分第 6 条。R6~R10 的预算为 9+6+6+4+4 = 29 次试验。

### 5. 结论

10 轮全部结束、`qualified_pool` 为空、`trials_used` 65 / 200。在当前市场与数据上未找到合格策略，
没有产生候选，因此没有冻结、最终检验、影子盘与实盘环节。后续建议见本轮完成报告。



## 2026-11-23 实盘工程与看板（P5 / P6）

- 依据实盘工程任务指令授权自行决定：旧版 `src/trade/live/` 的分钟级模块（`engine.py`、`market_data.py`、`orders.py`、`reconcile.py`、`risk.py`、`bar_builder.py`）与现有测试保留不删，新增低频路径模块（`candidate` / `feed` / `targets` / `execution` / `stops` / `riskguard` / `killswitch`）。理由：删除旧模块必须同时删除 `tests/live/` 下大量既有测试，与 AGENTS.md §6 红线 11 冲突；新模块名与职责清晰，旧模块不再被新入口引用。代价是仓库保留一份历史实现，已在 engine.md 记录。
- 依据实盘工程任务指令授权自行决定：状态库沿用旧表（`orders` / `fills` / `positions` / `equity_snapshots` / `risk_state` / `events` / `scan_cycles` / `scan_state`）并新增引擎表。理由：看板旧接口与测试继续可用，引擎写入同一份数据，避免两套状态库。
- 依据实盘工程任务指令授权自行决定：paper 模式端点改为 `https://demo-fapi.binance.com`（服务器实测 ping 返回 200），保留 `endpoints_for("testnet")` 兼容旧测试；`credentials.build_client` 对 shadow 使用公开端点与假签名材料，并由 `ReadOnlyMarketClient` 拦截一切交易调用。
- 依据实盘工程任务指令授权自行决定：systemd 用两个模板区分内存上限（shadow / paper 400M，live 500M），live 实例名不带模式后缀（`trade-runner-live@<候选ID>`）；`trade.live.units.unit_name` 统一命名，kill_switch 与看门狗共用。
- 依据实盘工程任务指令授权自行决定：`LiveConfig` 新增 `engine` 节，全部字段带默认值，旧 `configs/live.yaml` 与 `tests/live/test_config.py` 不受影响；`configs/live.yaml` 保留 `model_dir` / `sizing` / `exits` 兼容字段但低频引擎不读取。
- 依据实盘工程任务指令授权自行决定：`load_universe_history` 只读取 `YYYY-MM.json`，忽略同目录的 `aliases.json` / `summary.json`（服务器目录实测包含这两个文件，按快照解析会抛错）。
- 发现但未修改：服务器 `data/universe_hist` 只到 `2026-07.json`，当前月份没有选币快照；引擎 `View.universe_symbols()` 会抛带上下文的 `KeyError`（符合红线 12），启动 shadow 前需补齐当月快照。
- 部署：服务器 `sudo -n` 可用，已安装单元并启动看板与看门狗；未创建任何交易实例（总账无候选）。看板 `curl` 返回 200，`/api/status` 显示 `live_available=false`。


## 2026-11-24 迁移 M3：数据补充与防幸存者偏差

- 旧快照备份：`data/universe_hist` 共 72 个文件、1.3 MB（2026-11-24 11:16 UTC）完整复制到
  `data/universe_hist_pre_m3/`；`2021-02.json` 复制前后 SHA256 均为
  `1fa3a53e38dbf53028e4d29620f3ca379dca5c4758f986d982aa22a2ec5f0952`。覆盖只发生在
  `data/universe_hist/` 内，由 0 号调度第六部分 M3 第 2 条授权。
- 交易规则来源核实：`artifacts/exchange_info/exchangeInfo-20261031T105027Z.json` 共 942 个合约
  （TRADING 804、SETTLING 137、PENDING_TRADING 1；underlyingType 为 COIN 703、非加密 239）。
  旧 `rules.json` 缺 181 个币：150 个是 TradFi 合约（在当前快照中，contractType=
  `TRADIFI_PERPETUAL` 或 underlyingType 非 COIN），31 个是已下架加密合约（当前快照中完全没有）。
  处理方式：来源①保留 TradFi 的真实字段，由 `exclude_tradfi` 与 `contract_type != PERPETUAL`
  排除，不合成；只有 31 个已下架加密合约走合成规则（1h 价格的最小正价差向下取 10 的整数次幂 +
  0.1 USDT 的 step 名义上限 + 最小下单额 5 USDT）。
- 代码：`scripts/fetch_history.py` 增加 `flow` / `premium` / `mark` 三类补充数据（独立目录与
  manifest，`flow` 逐行核对 `open_time`）；新增 `src/trade/data/rules_research.py`；
  `scripts/build_universe_hist.py` 改为按 exchange → snapshot → synthetic 取得规则，输出
  `rules_source.json` 与 `summary.json`（含 TradFi 名单、合成币、来源计数、缺口）。合并后的规则写入
  `artifacts/exchange_info/rules.json`，原文件备份为 `artifacts/exchange_info/rules_pre_m3.json`。
- 下载结果（服务器 `systemd-run` 单元 `m3-fetch`，MemoryMax 1500M、nice 10，2020-10~2026-07）：
  `flow` 20207 个文件（全部 `open_time_verified`）、`premium` 19320、`mark` 20488，状态全部 `ok`，
  失败 0，62.1 文件/秒；体积 295 MB / 347 MB / 389 MB。覆盖差异：`mark` 覆盖全部 1h「币·月」，另有
  281 个「币·月」有 mark 无 1h 文件；`premium` 缺 1044 个「币·月」（涉及 133 个币，集中在
  2022-08 之后），这些缺口是 data.binance.vision 没有对应源文件，已由 manifest 逐月记录，使用
  premium 的方向在缺失期间不参与；`flow` 与 1h 完全一一对应。
- 选币快照重生成（`build_universe_hist.py --start 2020-10 --end 2026-07 --overwrite`）：70 个月，
  规则 928 个币（exchange 897、synthetic 31），TradFi 241 个；入选「币·月」3348 个，全部有规则与
  1h 数据。别名映射增至 330 个。
- 新旧每月入选币数（旧 → 新，新增数 / 其中合成规则）：2021-01 9→12（3/3）、2021-02 20→25（5/5）、
  2021-03 25→31（6/6）、2021-04 23→33（10/10）、2021-05 23→34（11/11）、2021-06 41→53（12/12）、
  2021-07 37→48（11/11）、2021-08 29→39（10/10）、2021-09 38→51（13/13）、2021-10 42→55（13/13）、
  2021-11 41→54（13/13）、2021-12 42→55（13/13）、2022-01 45→59（14/14）、2022-02 48→57（9/9）、
  2022-03 34→40（7/7，移除 1）、2022-04 45→53（8/8）、2022-05 32→39（7/7）、2022-06 58→58
  （新增 6、移除 6）、2022-07 49→55（6/6）、2022-08 36→41（5/5）、2022-09 42→48（6/6）、
  2022-10 37→42（5/5）、2022-11 30→35（5/5）、2022-12 34→39（5/5）、2023-01 28→30（2/2）、
  2023-02 44→48（4/4）、2023-03 56→59（5/5，移除 2）、2023-04 47→51（4/4）、2023-05 51→60（9/9）、
  2023-06 43→50（7/7）、2023-07 53→59（6/6）、2023-08 44→49（5/5）、2023-09 39→44（5/5）、
  2023-10 28→33（5/5）、2023-11 40→44（4/4）、2023-12 54→58（4/4）、2024-01 60→60（新增 4、移除 4）、
  2024-02 60→60（3、3）、2024-03 58→60（4、2）、2024-04 60→60（5、5）、2024-05 60→60（5、5）、
  2024-06 60→60（4、4）、2024-07 60→60（4、4）、2024-08 59→59（2、2）、2024-09 60→60（1、1）、
  2024-10 50→50、2024-11 57→57、2024-12 60→60（2、2）、2025-01~2025-05 每月新增 1、移除 1、
  2025-06 60→59（移除 1）、2025-07~2026-07 无变化。移除的币都是被新增的下架币挤出 top_n 的原有币。
  研究期内至少入选一次的合成规则币 22 个：AKRO、ANT、AUDIO、BTS、BTT、BZRX、COCOS、DODO、EOS、
  FOOTBALL、FRONT、GAL、HNT、KEEP、LUNA、MATIC、NU、RNDR、SRM、SXP、TOMO、YFII（均以 USDT 结尾）。
- `research_start` 重新确定：新快照中第一个入选数 ≥ 20 的月份仍是 2021-02（25 个币），2021-01 为
  12 个，总账保持 `research_start: 2021-02-01` 不变。
- 可执行性预检（500 USDT、`position_frac 0.08`、5x → 每仓名义上限 200 USDT）：BTCUSDT 最小下单额
  50 USDT（tick 0.1、step 0.001），ETHUSDT 20 USDT（tick 0.01、step 0.001）；2021-01~2026-07
  共 67 个月，每月「最小下单额 > 200 USDT」的入选币数均为 0，合计 0/3337「币·月」。说明按每仓名义
  上限判断没有币被必然丢弃；模式 A 下由止损距离决定的单仓名义仍可能低于 5 USDT，那部分丢弃由
  研究循环的可执行性检查（第七部分第 5 条 i 款）统计。



## 2026-11-24 迁移 M1：服务器改为 git 副本

- 使用者决定：研究与最终检验的 R6/R8 停机改为 reset 模式停 7 天、实盘与影子盘保持 strict；研究达标判定用 10,000 USDT 信号口径并另加 500 USDT 可执行性检查；新增仓位模式 B（按波动率分配权重 + 15% 防灾止损，target_vol 默认 0.20），与模式 A 并行检验；中性策略改为 β 中性；实盘策略权益按 `live_capital_cap` 封顶计算；实盘资金 500 USDT 起步、可执行口径 500 USDT；新增标准 / 进取 / 激进三个风险档位（研究与达标用标准档，冻结时按 1 号第九部分第 2 条选档）；总名义上限改为权益 × 3、净敞口上限改为权益 × 1.5；方向侧重主流币、截面类新方向限定成交额前 30、不交易 TradFi 合约；实盘停机回撤线与 90 天评估改为与研究基准对照；`global_trial_budget` 改为 400；月化约 10% 只作期望、不是达标项（0 号调度第六部分）。
- 备份：服务器 `~/trade` 除 `data/`、`artifacts/` 以外全部文件打成
  `artifacts/server_backup/20261124T104404Z.tar.zst`，552 个文件、1.79 MB；
  服务器 `data/` 现有 klines_1h 551 MB、klines_1d 162 MB、funding 121 MB、daily 55 MB、
  universe_hist 1.3 MB，磁盘剩余 37 GB。
- 差异核对（服务器 552 个文件 vs 本地 develop `b59c437`，用 bundle + 临时 git 目录逐文件比对，
  未改动 `~/trade`）：共 25 处差异，**没有服务器独有的较新代码**，无需把服务器文件提交回本地。
  - 服务器缺少（本地较新）：`docs/archive/07-实盘与风控.md`、`docs/archive/10-可视化看板.md`、
    `docs/research/rounds/R6.md`、`R7.md`、`R8.md`、`R9.md`、`R10.md`、
    `tests/dashboard/test_engine_api.py`、`tests/live/test_candidate.py`、`test_execution.py`、
    `test_feed.py`、`test_killswitch.py`、`test_riskguard.py`、`test_stops.py`、`test_targets.py`。
  - 内容不同（服务器为旧版，差异仅 UTF-8 BOM 或格式/节缺失）：`scripts/__init__.py`、
    `src/trade/__init__.py`、`src/trade/common/logging.py`、`src/trade/live/__init__.py`、
    `src/trade/models/__init__.py`、`tests/common/test_latency_probe.py`、`tests/common/test_logging.py`
    （以上仅 BOM）、`tests/research/helpers.py`（旧排版）、
    `tests/strategies/test_round_strategies.py`（旧版只有 R1~R4 用例）、
    `docs/research/p5p6_backlog.md`（缺“新方案与旧清单的对照”一节）。
  - 服务器独有、不属于仓库的遗留文件（转换后保持不动，加入服务器 `.git/info/exclude`）：
    `.bak-code/`、`bak-code/`、`tmp_deploy/`、`.claude/`、`.github/`、`src/trade.egg-info/`、
    根目录 `_*.py` / `_*.sh` 临时脚本、`scripts/run_live.py`、`tmp_train_lgbm.yaml` 与各类 `.log`。
- `docs/research/` 交接文件以本地 git 为准；`ledger.trials_used = 65` 与服务器
  `artifacts/reports/EXPERIMENTS.log` 中 `kind: tuning` 记录数 65 一致。
- 转换：服务器 `~/trade` 执行 `git init`、从 `git bundle` fetch、`git checkout -f --detach b59c437`；
  `git rev-parse HEAD` 与本地一致；`data/history/manifest.json` SHA256 前后均为
  `bcb2dca37c5581416d5ceaaf17c3d40d98b5c9b6b75739dc4030a11cf27840ee`；服务器没有 `.env.*` 与私钥文件，
  `~/.ssh/authorized_keys` 已是 600（无权限需要修改）；全程未执行 `git clean`。
- 同步规则改为 1 号第十部分的 git 方式：`scripts/sync.ps1 push-code` 改为
  bundle → scp → `git fetch` → `git checkout --detach <本地提交>` 并核对服务器 HEAD。
  依据迁移指令自行决定：服务器 `.git/info/exclude` 追加上述遗留路径，使 `git status` 干净，
  不影响仓库内容与其他机器。
- 看板与看门狗：转换前均 active；转换后 `curl 127.0.0.1:8501` 返回 200，未重启。


## 2026-11-24 迁移 M2：配置（停机方式、两种权益口径、风险档位）

- 指令内部冲突处理：M2 第 1 条写“equity 节（signal: 10000、exec: 100）”且“sizing_research 的 initial_equity 保持 100”，
  第 4 条写“initial_equity 改为 500”。按第 4 条与使用者决定（可执行口径 500 USDT）执行：
  `equity.signal: 10000`、`equity.exec: 500`、`initial_equity: 500`。
- 模型：新增 `trade.research.config.ResearchSizingConfig`（继承 `SizingConfig`，加 `tier` 与 `risk_tiers` 三档），
  不改 `SizingConfig` 本身，避免影响 `configs/sizing.yaml`、旧回测与 rotation 路径；
  `with_tier()` 把选中档位的 `risk_per_trade`、`max_risk_per_trade = max(0.025, disaster_loss_cap)` 与 R5~R8 阈值应用为纯 `SizingConfig`。
  三个加载点（`scripts/portfolio_backtest.py`、`scripts/live_runner.py`、`scripts/check_live_env.py`）改为 `load_research_sizing(...).with_tier()`。
- 停机：研究与最终检验改读 `configs/research.yaml: risk`（`reset`、7 天），不再读 `configs/backtest.yaml`；
  实盘 / 测试网 / 影子盘固定 `strict`（`trade.live.runner.LIVE_HALT_MODE`，`load_risk_limits(sizing, mode)`），
  新增 `tests/live/test_runner_halt.py` 断言三种模式都是 strict 且未知模式抛错。
- 组合上限：`configs/research.yaml: portfolio` 改为 `total_notional_cap: 3.0`、`net_exposure_cap: 1.5`。
- 实盘资金：`configs/live.yaml` 的 `live_capital_cap: 500`，pydantic 范围改为 `(0, 1000]`。
- 总账：`global_trial_budget` 200 → 400（`trials_used` 仍为 65）。
- 测试夹具：`tests/research/helpers.py` 的 `make_research_config` 补上新增的 `risk` / `equity` 必填节与新的组合上限，
  断言未改动、未跳过任何测试（配置模型新增必填字段属于规格变更，不是放宽容差）。
- 文档同步：`docs/00-当前目标.md` §1/§2（起始资金、两种权益口径、三档风险、总名义 3 倍与净敞口 1.5 倍、reset / strict 停机、
  月化是期望不是达标项）；`docs/13-研究框架.md` §2（sizing_research 的档位与 with_tier 语义、research.yaml 的 risk / equity、停机来源）。
- 校验：`ruff format --check .`、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`、
  `mypy src/trade/dashboard`、`python scripts/check_docs_sections.py` 全部通过；`pytest -q` 为
  `1 failed, 785 passed, 3 skipped`，唯一失败是与 SETUP_DONE 基线一致的 `tests/models/test_dataset.py::test_pinned_cpu_placement`（本机无 CUDA，未修改测试）。

## 2026-11-24 M4 研究框架修复的取舍（0 号调度第六部分 M4）

- 成交时点：按 1 号第五部分 B 第 4 条改为“决策在 t、成交在 `open_time = t` 的 K 线开盘”，仓位规模用截至 `t − 1h` 收盘的盯市值；
  权益曲线按每根 K 线收盘记录，因此日收益仍以 23:00 K 线收盘为准。
- 止损：改为按 `mark_1h` 的 low/high 触发、按成交价 K 线成交，成本 = `stop_slip_mult` 倍滑点（max(1bp, 平方根冲击)） + 半价差；
  入场基准价取入场时刻已收盘的最后一根标记价格收盘价。缺标记价格时退回成交价并计数 `mark_fallback`。
- 资金费断档判定：先用“相邻结算间隔中位数 × 1.5”，实测发现部分币的结算周期在 4h 与 8h 之间切换，导致 3 个月窗口内
  `funding_estimated` 达 8754（明显误判）；改为“相邻结算间隔 99 分位 × 1.5（间隔夹在 1~24h）”，同一窗口降至 28，
  真正缺数据的币（例如 TONUSDT 序列在窗口前结束）仍会被估算。
- 拼接曲线：`Engine.run_path` 接受段列表，权益、持仓与风控状态跨段延续，段起点状态为空；训练期只用于选参与指标预热，
  不再出现“训练期停机把验证折清零”的问题。
- R5 成分参数：网格改为 `component_grids` 指向 R1/R2/R4 网格，`pipeline.component_params_by_fold` 逐折按折前数据选参；
  `Ensemble.build` 缺成分时直接报错（不允许回退到全研究期参数）。成分参数的评估已在各自轮次登记，这里只复用结果、不重复登记试验。
- 可执行性（i 款）：`plan_positions` 增加 `attempted` 与 `dropped_min_notional` 计数，可执行口径用同一选参结果重跑拼接路径；
  三项判定（丢弃比例 ≤ 20%、净收益 > 0、夏普 ≥ 信号口径 70%）并入 `checks`。
- `long_only` / `short_only` 情景：仍在决策层过滤方向。1 号 d 款要求“交易集合是 base 的子集”，但仓位大小与持仓决定
  依赖权益路径，逐笔时间戳无法严格一致；测试改为核对方向过滤与两侧笔数上界，规格写入 `docs/13` §7。
- 最小规模运行不写 `EXPERIMENTS.log`：`--smoke-months` 只输出结果文件与记录字段样例，避免把调试运行计入试验次数
  （1 号第三部分第 5 条的试验指研究期网格评估）。
- 服务器同步：本地 `git bundle` → scp → 服务器 `git fetch` + `git checkout --detach`，核对 `git rev-parse HEAD` 与本地一致；
  本地到 GitHub 的推送仍未恢复（`git pull` 报 Empty reply），提交暂存在本地 develop。
- 测试改动（`review_log.md` 已逐条说明）：`test_engine.py` 的成交时点断言与止损用例的标记价格数据、`test_view.py` 的资金费用例
  改用 `funding_missing`；没有放宽任何容差、没有跳过任何测试。


## 2026-11-24 迁移 M5：仓位模式 B 与 β 中性的取舍（0 号调度第六部分 M5）

- 参数位置：`sizing_mode`、`target_vol`、`vol_lookback_days`、`cov_lookback_days`、`beta_lookback_days`、
  `beta_abs_max`、`ls_ratio_range` 加进共享的 `trade.common.sizing.SizingConfig`（带默认值），而不是另建研究专用模型：
  组合层由研究、影子盘、实盘共用（红线 4），实盘路径读同一份模型；旧配置（`configs/sizing.yaml`）未写这些字段时
  按默认值保持模式 A，行为不变。`ResearchSizingConfig.with_tier()` 用选中档位的 `target_vol` 覆盖默认值。
- 模式选择：`sizing_mode` 是网格参数维度。`run_param` 从 `params["sizing_mode"]` 取模式，`build_segments` 把它写进
  `Segment`；同一组其余参数配 A、B 两种模式即两组参数、两次试验。`scripts/portfolio_backtest.py` 在网格参数未写明时
  用 `configs/sizing_research.yaml` 的 `sizing_mode` 补齐（结果记录里始终带明确的模式）。
- 模式 B 权重只用 `1/σ`（过去 30 天日收益标准差），策略给出的 `Target.weight` 不参与，避免与策略自身的波动加权重复；
  防灾止损固定 `execution.stop_max`，策略的 `sl` 只做合法性校验，不参与名义计算。
- 日线视图：新增 `View.daily_returns(symbol, days)`，只取 `open_time + 1d ≤ now_ms` 的日线（红线 1）；模式 B 的权重的
  30 天波动、60 天协方差与 β 的 60 天回归都用它。模式 B 缺 30/60 天日线的币剔除并计入 `vol_missing`；β 样本不足 30 天
  或 BTC 日收益方差为 0 时取 1.0 并计入 `beta_default`（1 号第二部分第 3 条的规则）。
- β 调整顺序：先按 β 名义对齐两边使事前 β 为 0；再按 `ls_ratio_range` 缩小名义较大的一边；最后若 |β| 仍超
  `beta_abs_max × 权益` 则两边等比缩小（保持多空比）。三步都用比例缩放，保证三个约束同时成立。
- 删除 `portfolio.neutral_max_gap`：中性平衡改为由 β 与多空名义比定义，旧字段不再读取，避免留下死配置
  （同步 `configs/research.yaml`、`PortfolioConfig` 与测试 fixture）。
- 指标与达标项：`RunResult` 保存 BTCUSDT 日线（`benchmark_ts` / `benchmark_close`），`metrics.summarize` 计算
  `realized_beta`（cov(r, b) ÷ var(b)，共同交易日对齐）与 `alpha_sharpe`（剔除 β × BTC 日收益后的年化夏普）；BTC 日收益
  方差为 0 时 β 记 0；结果缺 BTC 日线时抛异常（红线 12）。达标项 j 只在 `exposure=neutral` 时加入 `checks`
  （`beta_abs_le_030`、`alpha_sharpe_ge_08`）。
- 最小规模运行：新增 `--smoke-sizing-mode {fixed_risk,vol_target}`，模式 A、B 各跑一次；仍只写
  `artifacts/reports/smoke/`，不写 `EXPERIMENTS.log`。
- 测试：新增 `tests/research/test_metrics.py`（β、alpha 夏普与 j 款）、`test_portfolio.py` 的模式 B 手算 / 单仓上限 /
  β 中性 / β 样本不足、`test_credibility.py` 的 `test_h5_引擎按段使用模式B` 与 `test_h6_中性策略的β调整在引擎中生效`、
  `test_config.py` 的新配置项与范围校验、`tests/common/test_sizing.py` 的 `size_from_notional` 与 `calc_notional` 状态一致。
  没有修改或跳过任何既有断言。


## 2026-11-24 M6 实盘安全修复与引擎对齐（取舍记录）

- 使用者决定：研究与最终检验 R6/R8 停机改为 reset 模式停 7 天、实盘与影子盘保持 strict；研究达标用 10,000 USDT 信号口径并另加 500 USDT 可执行性检查；新增仓位模式 B（按波动率分配权重 + 15% 防灾止损，target_vol 默认 0.20）；中性策略改为 β 中性；实盘策略权益按 live_capital_cap 封顶；实盘资金 500 USDT 起步、可执行口径 500 USDT；三个风险档位；总名义上限权益 × 3、净敞口上限权益 × 1.5；方向侧重主流币、截面类新方向限定成交额前 30、不交易 TradFi 合约；实盘停机回撤线与 90 天评估改为与研究基准对照；global_trial_budget 改为 400；月化约 10% 只作期望、不是达标项（0 号调度第六部分）。
- 看门狗重启权限：3 号第六部分第 10 条要求用 sudoers 规则授予重启权限，但服务器 sudo 版本不允许命令参数内通配符（`visudo` 报 “wildcards are not allowed in command arguments”）。
  取舍：保留“最小命令”原则，改为 sudoers 只授予一个白名单校验脚本 `/usr/local/sbin/trade-restart-unit`（规则不含通配符），脚本从标准输入读实例名，只接受 `trade-runner@*.service` 与 `trade-runner-live@*.service`，其他输入返回 64。
  权限等价于“只能重启 trade-runner 实例”，不授予 systemctl 其他子命令。已同步 `docs/07` §12/§13、`docs/02` §5。
- 看门狗单元不设 `NoNewPrivileges=true`：sudo 是 setuid 程序，该选项会让 sudo 失效；改用 `ProtectSystem=full` + `PrivateTmp` + `ReadWritePaths`，在 `docs/07` §13 写明例外原因。
- 紧急停机需要停止 systemd 实例：sudoers 只授予看门狗的重启脚本，没有授予 stop；因此 `scripts/kill_switch.py` 建议以 `sudo` 运行（未确认停止时直接中止，不操作账户）。
- 新增状态库表 `strategy_positions` 与 `equity_baseline`：前者保存引擎实际持仓快照（重启后止损对账与专用账户检查的依据，替代原先一直为空的旧 `positions` 查询）；后者保存策略权益启动基准，重启不重置。
- 候选文件新增可选字段 `sizing_mode` 与 `risk_tier`：冻结时按 1 号第九部分第 2 条选档、按 1 号第二部分第 3 条选仓位模式；缺省用 `configs/sizing_research.yaml` 的取值。单笔风险仍以候选 `risk_per_trade` 为准。
- 止损基准改为 `mark_1h`：与实盘 `workingType=MARK_PRICE`、研究框架 1 号 B 第 5 条一致；缺标记价格时退回成交价并告警。
- 影子盘止损改用已收盘 K 线：原先 `last_index_before(now_ms)` 会把当前未收盘 K 线当触发依据（红线 1），改为按决策整点取上一根已收盘 K 线。
- 影子盘权益与 `risk_state` 写入：原实现重复计算未实现、且当日无 `risk_state` 行时已实现盈亏被静默丢弃；改为 `initial_equity + 累计已实现 + 未实现`，`update_risk_state` 改为 upsert（红线 12：不静默丢数据）。
- 看板控制 PIN 改从 `configs/dashboard.yaml: control.pin_file` 读取：systemd 直接调用 python 后不能再经 `bash -lc` 导出环境变量；文件不存在时控制接口返回 503，不提供默认 PIN。
- 部署文件改用 LF：Windows 工作区的 CRLF 会让 sudoers / systemd 解析失败（实测 “garbage at end of line”）。

