# 自动研究循环进度（进行中）

状态：第二轮待运行（R11~R14 已登记 pending）。2026-11-23 使用者批准调整风险包络后更新。执行文件：`指令/自动多轮/1_自动研究循环.txt`。

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

- 无。R1~R10 全部结束（旧包络）；R11~R14 已登记为 pending（新包络），等待下一次研究循环执行。

## 风险包络第二轮（使用者批准）

- `configs/sizing_research.yaml`：`risk_per_trade` 默认 0.015，冻结候选时在 {0.01, 0.015, 0.02} 中选；`max_risk_per_trade` 0.03；`daily_loss_halt` 0.10；`max_drawdown_halt` 0.25；`daily_loss_stop` 0.05、`max_consecutive_losses` 6 不变。
- `configs/backtest.yaml`：`halt_mode: reset`、`halt_reset_days: 7`。
- 达标条件中的最大回撤同时放宽到 25%（研究、最终检验）。
- 新增网格：`configs/strategies/round_R11.yaml`（同 R1）、`round_R12.yaml`（同 R6）、`round_R13.yaml`（同 R8）、`round_R14.yaml`（同 R9）。
- R1~R10 的结果不参与第二轮的选参或达标判定。

## 结论

- 旧包络下 10 轮全部 FAILED：拼接年化夏普 R1 −0.470、R2 −0.607、R3 无法选参、R4 −0.939、R5 −1.078、
  R6 0.000（训练期停机）、R7 −0.026、R8 0.000（训练期停机）、R9 −0.317、R10 −0.180。
- `trials_used` 65 / 200；没有候选，因此不涉及冻结、最终检验、影子盘与实盘。
- 旧包络的结构性约束（`daily_loss_halt = 8%`、`max_drawdown_halt = 20%` 与 `halt_mode: strict`）会让一次坏日子永久停止该参数的交易，验证折被置零；100 USDT × 0.75% 风险与最小下单额 5 × 1.1 让宽止损仓位被丢弃。使用者已批准第二轮新包络。

## 下一步

- 下一次收到研究循环指令时，从 R11 开始按第七部分执行；每轮先写 `rounds/R11.md` 等假设与网格，再跑 12 / 9 / 6 / 4 组并登记试验。
- 全部轮次结束后若仍无合格策略，按第十二部分第 3 条报告；若达标则按第九部分冻结候选并分配最终检验窗口。

## 断点续跑说明

新会话收到本指令时：先读本文件与 `ledger.yaml`，从“正在进行 / 下一步”继续。服务器报告在
`~/trade/artifacts/reports/<轮次>/grid.json`，试验登记 `~/trade/artifacts/reports/EXPERIMENTS.log`；
代码同步用 tar（`src/trade/strategies/`、`configs/strategies/`），不覆盖服务器上的 `docs/research/ledger.yaml`。

## 推送状态

本地提交与推送状态见文末记录：本机到 GitHub 的推送多次失败（`Empty reply from server` /
`Recv failure: Connection was reset`），每次重试仍失败时在报告中记录提交哈希与未推送原因。

