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

