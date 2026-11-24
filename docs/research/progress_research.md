# 自动研究循环进度（进行中）

## 本次运行状态（2026-11-25 第五次运行：等 R1、前置复核与 R11/R12 预检）

- 判断（0 号调度第四部分）：`SETUP_DONE.md` 为 DONE；M1~M7 全部完成；`review_log.md` 无 OPEN 缺陷；无候选、窗口全为 unused；
  `engine.md` = ENGINE_READY；R1 为 researching、R2/R4~R12 为 pending、`trials_used` 97 < 400 → 第 8 条 a 款执行 1 号。
  1 号第六部分第 3 条的首个轮次是 R1（researching），它在服务器运行中，故本次只做前置复核与预检，不重复启动、不写尚未产生的判定。
- 前置复核：见 `review_log.md` 的“2026-11-25 00:10 UTC”一节（B1~B14 沿用 23:08 的“符合”，只新增对 `7a7de4f` 的 D21 字段复核；
  §8 全部通过，`pytest -q` = `1 failed, 868 passed, 3 skipped`，失败为本机无 CUDA 的既有测试）。会话级一致性：
  `trials_used` 97 = Σ trials 32 + Σ void.trials 65 + 0 = `EXPERIMENTS.log` 的 `kind=tuning` 97 条。
- R1 运行状态：服务器 tmux `r1`（`systemd-run --scope`，PID 924634），2026-11-24 21:34 UTC 启动；23:31 UTC 已运行 1h57m、
  RSS 1.05 GB、CPU 99.7%；按 R3 实测（16 组约 4 小时 10 分）推算 24 组约 5.8 小时，预计 2026-11-25 03:20 UTC 前后完成。
  完成判据：`ps` 无 `portfolio_backtest`、`artifacts/reports/R1/grid.json` 的 `generated_at` 为新时间、
  `EXPERIMENTS.log` 新增 24 条 `kind=tuning`（R1）与 1 条 `kind=exec`。**不要重复启动**。
- R11 / R12 的 500 USDT 可执行性预检已完成（见 `decisions.md` 2026-11-25 00:15 UTC 一节）：16 组全部低于 20% 丢弃门槛，
  按预登记网格照抄执行；R12 的 BTC 对冲腿在 BTC 价格高于约 83.3k 的月份会被丢弃（占目标数 ≤ 16.7%），须写入 `rounds/R12.md`。
- 未推送提交：无。上一节记录的推送失败已解决，`git log origin/develop -1` 与本地 HEAD 同为 `cc3fd09`（含 `7a7de4f`）。
- 下一步（新会话）：
  1. 确认 R1 是否结束（`ps -ef | grep portfolio_backtest`、`R1/grid.json`、`EXPERIMENTS.log`）；结束后读 `grid.json` 写
     `rounds/R1.md` 的“重跑（迁移后）”一节（选参、分折、情景、可执行口径、判定逐项），更新 `ledger.yaml`
     （R1 status / trials 24 / result 与 `trials_used` = 121），提交推送。
  2. 启动 R2（12 组）：服务器 `git fetch` 后 `git checkout --detach <本地哈希>`（须含 D21 修复与本次交接提交），
     核对 `git rev-parse HEAD` 与本地一致后用 tmux + `systemd-run` 启动。
  3. R11 / R12 开跑前：网格写 `delay1_max_excess: 0.10`，并把本次预检数字抄进 `rounds/R11.md` / `rounds/R12.md` 的假设部分。


## 本次运行状态（2026-11-24 第四次运行：前置复核与 R1 重跑跟踪）

- 判断（0 号调度第四部分）：`SETUP_DONE.md` 第一行“结论：DONE”；`progress_migration.md` 中 M1~M7 全部完成；`review_log.md` 无 OPEN 缺陷
  （本次新增 D21 已修复并关闭）；`candidates` 为空、无候选需要实时运行；`engine.md` = ENGINE_READY → 按第四部分第 8 条 a 执行 1 号。
- 前置复核：见 `review_log.md` 的“2026-11-24 23:08 UTC”一节（B1~B14 符合；R3 数字与 `grid.json` 逐项一致；
  服务器 HEAD `93d1b87` 与本地代码一致，本地多出的提交只有 `docs/research/` 下三个文件）。会话级一致性：`trials_used` 97 =
  Σ trials 32 + Σ void.trials 65 + 0 = `EXPERIMENTS.log` 的 `kind=tuning` 97 条。
- R1：服务器 tmux 会话 `r1`，2026-11-24 21:34 UTC 启动，代码 HEAD `93d1b87`，24 组（48 次全期运行）；按 R3 的耗时（16 组约 4 小时 10 分）
  预计 5~6 小时。运行期间 `artifacts/reports/R1/groups/` 不存在属正常（逐组落盘在全部运行结束后写入）；**不要重复启动**。
- 本次代码改动：`pipeline._checks` 支持网格字段 `delay1_max_excess`（R11 / R12 的登记例外；默认 0 与 R1~R10 口径相同），
  新增 3 项测试，`docs/13` §7、§9 同步。R1 在服务器 `93d1b87` 上运行，判定口径不受影响。
- 试验预算：97 / 400。
- 下一步（新会话）：
  1. `ps -ef | grep portfolio_backtest` 确认 R1 是否结束；结束则读 `artifacts/reports/R1/grid.json`（`generated_at` 应为 2026-11-24/25 的新时间），
     核对 `hashes.code_hash` 属于 `93d1b87`、`EXPERIMENTS.log` 新增 24 条 `kind=tuning`（R1）与 1 条 `kind=exec`。
  2. 写 `rounds/R1.md` 的“重跑（迁移后）”一节（选参、分折、情景、可执行口径、判定逐项），更新 `ledger.yaml`
     （R1 status / trials 24 / result 与 `trials_used`），提交推送。
  3. 启动 R2（12 组）：先把服务器切到含 D21 修复的新提交，`git rev-parse HEAD` 核对一致后用 tmux + `systemd-run` 启动。
  4. R11 / R12 开跑前：网格写 `delay1_max_excess: 0.10`，并做 500 USDT 可执行性预检（0 号调度第七部分第 2 条 c 款）。
- 未推送提交：`7a7de4f`（D21 修复与复核记录）与本条记录的提交。`git push origin develop` 连续失败
  （`Empty reply from server`、`Failed to connect to github.com:443`、`Recv failure: Connection was reset`，按 1、2、4 分钟重试 3 次仍失败）；
  本地提交保留，下次会话开始时先补推（补推成功后删除本节内容）。


## 本次运行状态（2026-11-24 第三次运行：迁移后重跑）

状态判断（1 号第六部分）：第五部分已完成（`SETUP_DONE.md` DONE、M1~M7 完成、`data/` 三类补充数据与
`universe_hist` 就绪）；冻结点未到（`qualified_pool` 为空）；R3 已 FAILED，R1 为 `researching`（服务器运行中，
代码 HEAD `93d1b87`），R2、R4~R10 为 `pending`（首次判定已作废），R11、R12 已按第六部分第 1b 条追加为 `pending`。

- 执行顺序（0 号调度第六部分 M7 第 3 条）：R3 → R1 → R2 → R4 → R5 → R6~R10 → R11、R12。
- 网格：R1 24、R2 12、R3 16、R4 12、R5 8、R6 18、R7 12、R8 12、R9 8、R10 8 组（原网格 × 仓位模式 {A, B}）。
- 试验预算：`trials_used` 97 / 400（作废前 65 + R3 第 1 次重跑 16 + R3 修复后重跑 16）。
- 进度：R3 完成（FAILED）；R1 已在服务器 tmux 会话 `r1` 后台运行（见下方“下一步”）。

## 迁移后重跑结果

| 轮次 | 服务器 HEAD | 组数 | 拼接年化夏普 | 最大回撤 | 5 折为正 | DSR | 可执行丢弃 | 判定 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| R3 | `64a0dde` | 16 | 0.070 | 59.89% | 2/5 | 0.054 | 45.7% | FAILED（第 1 次重跑因 D19 作废） |

## 迁移后状态（2026-11-24）


- M1~M7 已完成。R1~R10 的首次判定已作废（ledger `void`，`rounds/*.md` 顶部已标记），`trials_used = 65`、预算 400。
- 重跑网格已按 `sizing_mode ∈ {fixed_risk, vol_target}` 加倍：R1 24、R2 12、R3 16、R4 12、R5 8、R6 18、R7 12、R8 12、R9 8、R10 8（共 130 组）。
- 下一步：按 R3 → R1 → R2 → R4 → R5 → R6~R10 的顺序逐轮重跑（0 号调度第六部分 M7 第 3 条），结果写入各 `rounds/R*.md` 的“重跑（迁移后）”一节与 `ledger.yaml`。
- 重跑完成后由第四部分第 8 条继续（R11、R12 由 1 号第八部分第 1b 条追加）。

状态：第二次运行（首次运行已完成 R1~R10，判定已作废），2026-11-23 21:30 UTC 起（R6 起）。执行文件：`指令/自动多轮/1_自动研究循环.txt`。


## 已完成

- R1~R5：首轮网格全部不达标（数字见 `rounds/R1.md` ~ `R5.md`）。
- R6 `funding_adj_momentum`（资金费调整的截面动量，周频）：9 组，拼接夏普 0.000；选中组训练期内
  2021-10-07 触发 R6 单日亏损停机，验证折无交易；`nohalt` 年化 0.124、回撤 41.6%。FAILED，见 `rounds/R6.md`。
- R7 `xs_low_vol_ls`（低波动异常，周频）：6 组，拼接夏普 −0.026、回撤 22.4%；多头 −16.69 USDT、
  空头 +8.73 USDT，与假设方向相反。FAILED，见 `rounds/R7.md`。
- R8 `vol_timed_momentum`（波动率择时动量，周频）：6 组，选中组训练期停机、验证折无交易；
  σ_target 0.5 两组自身拼接日频夏普 +0.031/+0.022；`nohalt` 净 −0.14%。FAILED，见 `rounds/R8.md`。
- R9 `btc_hedged_momentum`（BTC 对冲单边动量，周频）：4 组，拼接夏普 −0.317、回撤 19.5%；
  多头 −19.05、空头 +5.30；2022-08-19 触发 R8 停机。FAILED，见 `rounds/R9.md`。
- R10 `liquidity_tiered_momentum`（流动性分层动量，周频）：4 组，拼接夏普 −0.180、回撤 29.4%；
  多头 −25.76、空头 +14.26。FAILED，见 `rounds/R10.md`。

## 正在进行

- 无。R1~R10 全部结束，`qualified_pool` 为空。

## 结论

- 10 轮全部 FAILED：拼接年化夏普 R1 −0.470、R2 −0.607、R3 无法选参、R4 −0.939、R5 −1.078、
  R6 0.000（训练期停机）、R7 −0.026、R8 0.000（训练期停机）、R9 −0.317、R10 −0.180。
- `trials_used` 65 / 200；没有候选，因此不涉及冻结、最终检验、影子盘与实盘。
- 结构性约束（使用者确定，Agent 不得修改）：`daily_loss_halt = 8%`、`max_drawdown_halt = 20%` 与
  `halt_mode: strict` 会让一次坏日子永久停止该参数的交易，验证折被置零；100 USDT × 0.75% 风险与
  最小下单额 5 × 1.1 让宽止损仓位被丢弃。

## 下一步

- 顺序：R1 → R2 → R4 → R5 → R6~R10 → R11、R12（0 号调度第六部分 M7 第 3 条）。
- **R1 已在服务器后台运行**：tmux 会话 `r1`，
  `sudo -n systemd-run --scope -p MemoryMax=2000M nice -n 10 ~/trade-venv/bin/python scripts/portfolio_backtest.py --grid configs/strategies/round_R1.yaml`，
  日志 `~/trade/artifacts/reports/R1.log`，报告 `~/trade/artifacts/reports/R1/grid.json`。
  服务器 HEAD 必须是 `64a0dde`（`git rev-parse HEAD` 核对后才启动）。
  新会话开始先确认该进程是否仍在运行：`ps -ef | grep portfolio_backtest`；已结束则直接读取 `R1/grid.json` 写判定，
  未结束则等待，不要重复启动（重复启动会同时登记两套试验）。
- 运行成本（实测）：D19 修复后拼接路径会在全研究期持续交易，R3 的 16 组 + 8 段路径运行耗时约 4 小时 10 分
  （服务器 2 核、单进程）。R1 有 24 组（48 次全期运行），预计 5~7 小时；R1~R10 全部重跑预计需要 40~50 小时服务器时间，
  按“每完成一轮提交推送”在多个会话中推进。
- 每轮完成后：把数字写入 `rounds/R*.md` 的“重跑（迁移后）”一节，更新 `ledger.yaml`（status / trials / result 与 `trials_used`），
  提交推送后再启动下一轮。

- 按第十二部分第 3 条报告“在当前市场与数据上未找到合格策略”并给出建议；若使用者调整风险包络或
  允许 `halt_mode: reset`，需要使用者本人决定后重新下达指令。

## 断点续跑说明

新会话收到本指令时：先读本文件与 `ledger.yaml`，从“正在进行 / 下一步”继续。服务器报告在
`~/trade/artifacts/reports/<轮次>/grid.json`，试验登记 `~/trade/artifacts/reports/EXPERIMENTS.log`；
代码同步用 tar（`src/trade/strategies/`、`configs/strategies/`），不覆盖服务器上的 `docs/research/ledger.yaml`。

## 推送状态

- 2026-11-24：`develop` 已推送至 `5d40de4`；`5c3b674`（M7 作废与网格加倍）与 `6f34153`（M7 提交哈希记录）因 GitHub 连接被重置 / 无法连接尚未推送。

本地提交与推送状态见文末记录：本机到 GitHub 的推送多次失败（`Empty reply from server` /
`Recv failure: Connection was reset`），每次重试仍失败时在报告中记录提交哈希与未推送原因。


