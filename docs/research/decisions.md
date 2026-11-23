# 决策记录（共用，只追加）

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
