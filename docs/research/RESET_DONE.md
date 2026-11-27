结论：DONE

重置时间：2026-11-27 12:01:39 UTC（本地与服务器本地时间 20:01 CST）。执行文件：`指令/自动多轮/00-研究重置-单独发送.txt`（由使用者单独发送）。

重置前快照提交：`eee523f`（chore: 重置前快照，只含已有的 docs 未提交改动与 `docs/records.md`，排除 `指令/`）。

## 一、开始前的检查（第二部分）

1. git：当前是 git 仓库、位于 `develop` 分支；`git pull --rebase origin develop` 成功（Fetch 后 `Current branch develop is up to date`，无远端新提交）。工作区原有未提交改动（`docs/00-当前目标.md`、`docs/07-实盘与风控.md`、`docs/13-研究框架.md`、`docs/design.md` 的修改与未跟踪的 `docs/records.md`）先单独提交为 `eee523f`。
2. `docs/research/SETUP_DONE.md` 存在，第一行为“结论：DONE”。
3. 总账：`qualified_pool` 与 `candidates` 均为空。重置前 R5 的 `status` 为 `researching`，但没有任何在流程中的候选或运行中的实例：服务器 `systemctl list-units 'trade-runner*'` 返回 0 个单元；`ps -ef` 无 `portfolio_backtest.py`、`fetch_history.py` 等研究进程；tmux 会话 `m3`、`trade` 各只有空闲 bash（`pane_current_command=bash`，capture 无运行内容）；`artifacts/reports/R5.log` 以判定输出结束（未达标项、拼接夏普 −0.910、可执行口径夏普 −1.171），最后修改 2026-11-26 15:49:26（服务器本地）。
4. `ssh tokyo` 可连通（`VM-0-12-ubuntu`）；服务器无正在运行的研究进程（同第 3 条）；`~/trade` 是 git 副本，重置前 HEAD `83500d0`，工作区干净。

结论：四项全部通过，继续执行删除与清空。

## 二、保留的预登记文件

`docs/research/hypotheses_R1_R10.md`（392 行）：R1~R10 逐轮的“假设（先写后跑）”一节原样复制 + `configs/strategies/round_<轮次>.yaml` 参数列表。开头写明“R1~R10 的预登记假设与网格；不含任何运行结果”。

逐行检查后删掉 3 处由结果得出的措辞：R5 成分参数的“（各自首轮网格在全研究期按年化夏普、交易数 ≥ 30 选出的参数）”、R6 的“周频调仓把换手与成本压到 R1 的约 1/20”、R9 的“（R1 的多头端 −39.9、空头端 +18.3 就是这种混合的结果）”。R1 数据行对旧 `decisions.md` 的缺口记录补注“（重置前的记录，只在 git 历史中）”。

## 三、删除（`docs/research/`，`git rm`，历史可恢复）

| 路径 | 内容 |
| :--- | :--- |
| `docs/research/rounds/` | 10 个文件：`R1.md`~`R10.md` |
| `docs/research/progress_research.md` | 1 个文件 |

不存在、未做任何操作（按指令“不存在时跳过并记‘不存在’”）：`docs/research/short_report.md`（不存在）、`docs/research/candidates/`（不存在）、`docs/research/final/`（不存在）、`docs/research/shadow/`（不存在）、`docs/research/live/`（不存在）。本地与服务器 `~/trade/docs/research/` 下都确认没有这五项。

## 四、清空并重写的文件

- `docs/research/ledger.yaml`：按模板重写，30 行。`trials_used: 0`；`research_start: "2021-02-01"`、`research_end: "2026-07-31"` 与 `final_windows`（W1~W3）沿用重置前的值（窗口未改动、未读取）；`rounds` 为 X0~X4、R3、R1、R2、R4、R5、R6~R10、R11、R12 共 17 条，全部 `status: pending, trials: 0, result: null`；`qualified_pool`、`candidates`、`live_candidates` 置空。
- `docs/research/decisions.md`：重写为 202 行，只保留“2026-11-27 研究重置”、原文照抄的“一次性准备”事实（第 1、2、3、5 小节与分支改名说明）与原文照抄的“实盘工程与看板（P5 / P6）”。

## 五、artifacts 的移动与删除

服务器 `~/trade/artifacts/reports/` → `~/trade/artifacts/reports/_void_20261127T120139Z/`（只移动，不删除）：

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

- 移动前确认无进程在用：`ps -ef` 无研究进程；`lsof +D /home/ubuntu/trade/artifacts/reports` 无输出（无打开文件）。
- `R6`~`R10` 目录属主为 root，用 `sudo -n mv` 移动；其余用普通 `mv`。
- `artifacts/reports/` 下无 `X` 后接数字的目录（`ls -d X[0-9]*` 报不存在）。

本地 `artifacts/reports/` → `artifacts/reports/_void_20261127T120139Z/`：`EXPERIMENTS.log` 83 370 B（修改时间 2026-11-19 23:10:34 本地）。本地 `artifacts/reports/` 下无大写 `R`/`X` 后接数字的轮次目录（小写旧目录 `r0_full_nohalt`、`r3` 为迁移前旧项目产物，不匹配清单口径，未动）。

- 删除：`artifacts/research_cache/` 在服务器与本地都不存在，未删除任何内容。
- 未动：`artifacts/reports/FINAL_RUNS.log`（服务器上不存在）、`artifacts/reports/research_final/`（服务器上不存在）、`artifacts/live/`（含 `book/`）、`artifacts/exchange_info/`、`artifacts/costs/`、`artifacts/server_backup/`、`artifacts/reports/R*.log`（各轮日志，不在清单内）与 `EXPERIMENTS.log.before_*` 备份。

## 六、void_history 的数字

- `void_history.trials = 153`：服务器旧 `EXPERIMENTS.log` 共 164 条 JSON 记录，逐行解析结果为 `kind=tuning` 153 条、`kind=exec` 6 条、无 `kind` 字段 5 条，无坏行。
- `void_history.log = artifacts/reports/_void_20261127T120139Z/EXPERIMENTS.log`（第五部分移动后的实际路径）。
- `void_history.rounds = "R1~R10 首次运行"`，`reason = "D1~D16 口径缺陷，研究重置时作废"`。

## 七、旧记录的 git 提交哈希

`git log -1 --format=%h -- docs/research/decisions.md` 在重写前的结果为 `2efa95b`（旧研究记录全文见 git 历史）。

## 八、收尾检查

- `python scripts/check_docs_sections.py`：退出码 0，“检查通过：8 份文档的标题、数字、配置键与全仓库章节引用一致（基线已有的失效引用 19 条，无基线而跳过的文档 0 份）”。19 条失效引用全部在 `src/trade/**` 的代码注释里（07 §6.4~§6.11、§12，11 §4.4~§5.6），与本次删除无关，基线中同样存在。
- `python -m pytest -q tests/test_agent_rules.py tests/test_check_docs_sections.py`：`9 passed`。未修改任何测试断言。
- 未修改源代码、测试、配置（`src/`、`scripts/`、`tests/`、`configs/`）；`data/` 未动；未运行任何研究、网格或回测。
- 使用者随后的补正指示（解决残留失效引用）已记入 `decisions.md` 的“自行决定的记录”，涉及
  `docs/00-当前目标.md` §4、`docs/10-可视化看板.md` §2、`docs/13-研究框架.md` §10，以及
  `docs/research/review_log.md`、`docs/research/progress_migration.md` 开头的说明段（历史正文未改）。
