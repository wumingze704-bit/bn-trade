# 决策记录（共用，只追加）

## 2026-11-27 研究重置

- 使用者决定：研究重置（00_研究重置_单独发送.txt）。
- 重置时间：2026-11-27 12:01:39 UTC（本地与服务器本地时间 20:01 CST）。
- 原因：已有的研究轮次（R1~R10）由一个有口径缺陷的研究框架跑出，缺陷影响每一轮的数字与判定，旧结论不能作为
  “方向无效”的证据。缺陷编号与内容：
  - D1 拼接曲线把训练期的风控状态（停机、回撤、连续亏损）带进验证折；
  - D2 R5 的成分参数用全研究期选参，早期各折用到了之后的数据；
  - D3 决策时刻 t 的订单晚一小时成交，仓位所用权益含 t 之后的价格；
  - D4 止损成交只算 2 × 1bp 滑点，没有半价差与冲击成本；
  - D5 持仓币没有资金费序列时静默按 0 计；
  - D6 止损按成交价而不是标记价格触发；
  - D7 平仓取不到价格时静默保留仓位；
  - D12 中性策略“多空毛收益差”判定写反，要求多头比空头赚得多；
  - D13 reset 停机恢复时峰值不重设，回撤停机恢复当刻就再次停机，等于 strict；
  - D14 方向相反的目标被当作继续持有，不会反手；
  - D15 中性策略一侧为空时变成单边敞口、持仓数截断先截空头、止损后周内不再配平、上线天数按加载窗口起点计算；
  - D16 R1 未执行打分符号条件、R3 波动被缩放两次、半价差缺 tick 下限、168 小时调仓落在周四而不是周一。
- 旧记录：`docs/research/decisions.md` 重置前最后一次提交为 `2efa95b`；被删除的 `docs/research/rounds/`（R1~R10
  共 10 个文件）与 `docs/research/progress_research.md` 全文见 git 历史。
- 本任务只清研究结果，不修代码，也不运行任何研究；代码修复与重跑由后续任务负责。

### 1. 预登记内容的摘出（删除之前完成）

- 新建 `docs/research/hypotheses_R1_R10.md`：逐轮原样复制 `docs/research/rounds/<轮次>.md` 的“假设（先写后跑）”
  一节，并附 `configs/strategies/round_<轮次>.yaml` 的参数列表；文件开头写明“R1~R10 的预登记假设与网格；
  不含任何运行结果”。
- 逐行检查后删除 3 处由结果得出的措辞（其余保留的都是事先写下的参数、阈值与选参规则）：
  - R5 成分说明的括号“（各自首轮网格在全研究期按年化夏普、交易数 ≥ 30 选出的参数）”；
  - R6 预期收益来源的“周频调仓把换手与成本压到 R1 的约 1/20”；
  - R9 经济机制的“（R1 的多头端 −39.9、空头端 +18.3 就是这种混合的结果）”。
- R1 数据行引用旧 `docs/research/decisions.md` 的数据缺口记录，补注“（重置前的记录，只在 git 历史中）”，
  避免指向已重写的文件。

### 2. 删除（`git rm`，历史可恢复）

| 路径 | 内容 |
| :--- | :--- |
| `docs/research/rounds/` | R1~R10 共 10 个轮次记录（`R1.md`~`R10.md`） |
| `docs/research/progress_research.md` | 研究进度记录 |

不存在的路径（按指令“不存在的跳过”处理，未做任何操作）：`docs/research/short_report.md`、
`docs/research/candidates/`、`docs/research/final/`、`docs/research/shadow/`、`docs/research/live/`；
本地与服务器 `~/trade/docs/research/` 下均无这五项。

### 3. 清空与重写

- `docs/research/ledger.yaml`：按研究重置指令第五部分第 2 条的模板重写。`trials_used` 归零；
  `research_start`/`research_end`（2021-02-01 / 2026-07-31）与 `final_windows`（W1~W3）沿用重置前的值，窗口未改动、
  未读取；`rounds` 改为 X0~X4 与 R1~R12 共 17 条 `pending`，顺序为 X 轨 → R3 → R1、R2、R4、R5 → R6~R10 →
  R11、R12；`qualified_pool`、`candidates`、`live_candidates` 置空。
- `void_history.trials = 153`：服务器旧 `artifacts/reports/EXPERIMENTS.log` 共 164 条记录，其中 `kind=tuning`
  153 条、`kind=exec` 6 条、无 `kind` 字段 5 条（按 Python 逐行解析统计，无坏行）。
- `void_history.log = artifacts/reports/_void_20261127T120139Z/EXPERIMENTS.log`：移动后的实际路径。
- 本文件（`docs/research/decisions.md`）重写为三部分：研究重置（本节）、一次性准备的事实（分支名、服务器代码
  同步方式、指令文件位置、归档清单、数据目录清理，原文照抄）、实盘工程与看板（P5 / P6）（原文照抄）。原文件中
  关于研究轮次结果、诊断与研究运行的段落不再保留。

### 4. artifacts 移动与删除（只移动不删除，清单内）

服务器 `~/trade/artifacts/reports/` → `~/trade/artifacts/reports/_void_20261127T120139Z/`：

| 目录/文件 | 文件数 | 大小 | 修改时间（服务器本地） |
| :--- | ---: | ---: | :--- |
| `EXPERIMENTS.log` | 1 | 74 786 B | 2026-11-26 15:49:26 |
| `R1/` | 28 | 6.9 M | 2026-11-25 11:37:25 |
| `R2/` | 14 | 3.3 M | 2026-11-25 20:58:34 |
| `R3/` | 19 | 3.5 M | 2026-11-25 01:16:48 |
| `R4/` | 14 | 4.2 M | 2026-11-26 00:19:10 |
| `R5/` | 34 | 11 M | 2026-11-26 12:21:19 |
| `R6/` | 2 | 72 K | 2026-11-23 21:40:32 |
| `R7/` | 2 | 80 K | 2026-11-23 21:46:09 |
| `R8/` | 2 | 68 K | 2026-11-23 21:48:40 |
| `R9/` | 2 | 72 K | 2026-11-23 21:54:31 |
| `R10/` | 2 | 72 K | 2026-11-23 21:56:52 |
| 合计 | 120 | 29 M（`du -sh`） | 移动完成 2026-11-27 12:01:39 UTC |

- 移动前确认无进程在用：`ps -ef` 无 `portfolio_backtest.py`/`fetch_history.py` 等研究进程，`lsof +D` 无打开文件。
- `R6`~`R10` 目录属主为 root，用 `sudo -n mv` 移动（移动，不删除）。

本地 `artifacts/reports/` → `artifacts/reports/_void_20261127T120139Z/`：`EXPERIMENTS.log` 83 370 B
（修改时间 2026-11-19 23:10:34 本地）。本地 `artifacts/reports/` 下无大写 `R`/`X` 后接数字的轮次目录。

- `artifacts/research_cache/`：服务器与本地都不存在，未删除任何内容。
- 未动：`artifacts/reports/FINAL_RUNS.log`（服务器上不存在）、`artifacts/reports/research_final/`（服务器上不存在）、
  `artifacts/live/`（含 `book/`）、`artifacts/exchange_info/`、`artifacts/costs/`、`artifacts/server_backup/`
  以及未列出的文件（含 `artifacts/reports/R*.log` 各轮日志与 `EXPERIMENTS.log.before_*` 备份）。

### 5. 自行决定的记录

- 依据研究重置指令授权自行决定：第二部分第 1 条要求的“重置前快照”把当时工作区里已有的 `docs/00-当前目标.md`、
  `docs/07-实盘与风控.md`、`docs/13-研究框架.md`、`docs/design.md` 修改与未跟踪的 `docs/records.md` 单独提交为
  `eee523f`（排除 `指令/`）；本任务未修改这些设计书的内容。
- 依据研究重置指令授权自行决定：重置前 `ledger.yaml` 中 R5 的 `status` 为 `researching`，但检查显示没有任何
  在流程中的候选或运行中的实例——`candidates` 为空，`systemctl list-units 'trade-runner*'` 为 0 个单元，
  `ps -ef` 无研究进程，tmux 会话 `m3`、`trade` 只有空闲 bash，`artifacts/reports/R5.log` 以判定输出（未达标项与
  拼接夏普、可执行口径）结束，因此继续重置。
- 依据研究重置指令授权自行决定：本地 `artifacts/reports/` 下的小写旧目录（`r0_full_nohalt`、`r3` 等，迁移前旧
  项目产物）与 `artifacts/` 根下的 `r6_report` 等目录不匹配“名字为 R 或 X 后接数字”的清单口径（清单写的是大写
  `R`/`X`），未移动、未删除。
- 依据研究重置指令授权自行决定：删除 `docs/research/` 文件后，若 `scripts/check_docs_sections.py` 或
  `docs/research/` 下的交接文件出现失效引用，只修改交接文件或检查脚本的数据目录配置，不改测试断言与设计书。
- 使用者后续指示（2026-11-27，重置完成报告第 8 点）：要求解决两处遗留引用，已按此执行，覆盖研究重置指令的
  第三部分第 2 条（不改设计书）与第 6 条（`review_log.md`、`progress_migration.md` 保留不动）：
  - `docs/00-当前目标.md` §4 把交接文件拆成两行：常驻文件（`ledger.yaml`、`hypotheses_R1_R10.md`、`engine.md`、
    `decisions.md`、`review_log.md`、`RESET_DONE.md`、`progress_*.md`）与产出物（`rounds/<轮次>.md`、
    `short_report.md`、`candidates/`、`final/`、`shadow/`、`live/`，由对应阶段按需创建）；
  - `docs/10-可视化看板.md` §2 写明 `docs/research/rounds/*.md` 目录存在时读取、不存在时轮次结论列为空（与
    `src/trade/dashboard/app.py` 的 `is_dir()` 判断一致）；
  - `docs/13-研究框架.md` §10 注明 `docs/research/short_report.md` 由 X 轨运行时创建；
  - `docs/research/review_log.md`、`docs/research/progress_migration.md` 在开头各加一段说明：文中对 `rounds/` 与
    `progress_research.md` 的引用属于重置前记录，对应文件已删除，全文只在 git 历史中可查。历史正文一行未改。

## 2026-11-23 一次性准备（00_一次性准备_最先发这个.txt）

（保留与分支名、服务器代码同步方式、指令文件位置、归档清单、数据目录清理有关的第 1、2、3、5 小节与分支改名说明；原第 4、6 小节是代码文件删除与仓库规范改写记录，只在 git 历史中。）

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

依据使用者指示自行决定（2026-11-23，准备完成之后）：
分支名 `devlop` 是 `develop` 的拼写错误。已执行 `git branch -m devlop develop`，把本地分支改名为
`develop`、推送到 `origin/develop`，并删除远端拼写错误的分支 `origin/devlop`（`develop` 与 `devlop`
指向同一个提交，历史未改写）。AGENTS.md §2/§9、`.clinerules/00-core.md`、`docs/research/` 里的分支名
同步改为 `develop`；README 重写为当前体系的使用说明（调度用法、目录结构、文档入口、质量检查、数据与制品、
服务器与网络、分支与提交、安全与已知状态）。`docs/archive/tasks.md` 里的旧记录保持原样（归档文档不随代码改动更新）。


## 2026-11-23 实盘工程与看板（P5 / P6）

- 依据实盘工程任务指令授权自行决定：旧版 `src/trade/live/` 的分钟级模块（`engine.py`、`market_data.py`、`orders.py`、`reconcile.py`、`risk.py`、`bar_builder.py`）与现有测试保留不删，新增低频路径模块（`candidate` / `feed` / `targets` / `execution` / `stops` / `riskguard` / `killswitch`）。理由：删除旧模块必须同时删除 `tests/live/` 下大量既有测试，与 AGENTS.md §6 红线 11 冲突；新模块名与职责清晰，旧模块不再被新入口引用。代价是仓库保留一份历史实现，已在 engine.md 记录。
- 依据实盘工程任务指令授权自行决定：状态库沿用旧表（`orders` / `fills` / `positions` / `equity_snapshots` / `risk_state` / `events` / `scan_cycles` / `scan_state`）并新增引擎表。理由：看板旧接口与测试继续可用，引擎写入同一份数据，避免两套状态库。
- 依据实盘工程任务指令授权自行决定：paper 模式端点改为 `https://demo-fapi.binance.com`（服务器实测 ping 返回 200），保留 `endpoints_for("testnet")` 兼容旧测试；`credentials.build_client` 对 shadow 使用公开端点与假签名材料，并由 `ReadOnlyMarketClient` 拦截一切交易调用。
- 依据实盘工程任务指令授权自行决定：systemd 用两个模板区分内存上限（shadow / paper 400M，live 500M），live 实例名不带模式后缀（`trade-runner-live@<候选ID>`）；`trade.live.units.unit_name` 统一命名，kill_switch 与看门狗共用。
- 依据实盘工程任务指令授权自行决定：`LiveConfig` 新增 `engine` 节，全部字段带默认值，旧 `configs/live.yaml` 与 `tests/live/test_config.py` 不受影响；`configs/live.yaml` 保留 `model_dir` / `sizing` / `exits` 兼容字段但低频引擎不读取。
- 依据实盘工程任务指令授权自行决定：`load_universe_history` 只读取 `YYYY-MM.json`，忽略同目录的 `aliases.json` / `summary.json`（服务器目录实测包含这两个文件，按快照解析会抛错）。
- 发现但未修改：服务器 `data/universe_hist` 只到 `2026-07.json`，当前月份没有选币快照；引擎 `View.universe_symbols()` 会抛带上下文的 `KeyError`（符合红线 12），启动 shadow 前需补齐当月快照。
- 部署：服务器 `sudo -n` 可用，已安装单元并启动看板与看门狗；未创建任何交易实例（总账无候选）。看板 `curl` 返回 200，`/api/status` 显示 `live_available=false`。
