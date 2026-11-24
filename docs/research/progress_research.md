# 自动研究循环进度（进行中）

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

- 按第十二部分第 3 条报告“在当前市场与数据上未找到合格策略”并给出建议；若使用者调整风险包络或
  允许 `halt_mode: reset`，需要使用者本人决定后重新下达指令。

## 断点续跑说明

新会话收到本指令时：先读本文件与 `ledger.yaml`，从“正在进行 / 下一步”继续。服务器报告在
`~/trade/artifacts/reports/<轮次>/grid.json`，试验登记 `~/trade/artifacts/reports/EXPERIMENTS.log`；
代码同步用 tar（`src/trade/strategies/`、`configs/strategies/`），不覆盖服务器上的 `docs/research/ledger.yaml`。

## 推送状态

本地提交与推送状态见文末记录：本机到 GitHub 的推送多次失败（`Empty reply from server` /
`Recv failure: Connection was reset`），每次重试仍失败时在报告中记录提交哈希与未推送原因。

