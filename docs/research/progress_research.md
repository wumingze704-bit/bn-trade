# 自动研究循环进度（进行中）

状态：首次运行，2026-11-23 开始（UTC）。执行文件：`指令/自动多轮/1_自动研究循环.txt`。

## 已完成

1. `docs/research/ledger.yaml`：version 2，10 轮，预算 200，research_start 2021-02-01，research_end 2026-07-31。
2. 历史数据下载（服务器）：`scripts/fetch_history.py`，2020-10 ~ 2026-07 的 1h/1d K 线与资金费，
   59,703 个文件、约 834 MB，0 缺失、0 失败（`data/history/manifest.json`）。
3. 选币快照：`scripts/build_universe_hist.py`，70 个月写入 `data/universe_hist/`；
   各月入选数与缺失规则币名单见 `docs/research/decisions.md`。
4. 研究框架：`src/trade/research/`（数据层、View、组合层、执行模拟、滚动验证、指标、守卫、流水线）、
   `src/trade/strategies/`（R1~R5）、`scripts/portfolio_backtest.py`、`tests/research/`、`tests/strategies/`。
5. 设计书：`docs/13-研究框架.md` 首次写入（只写规格）。
6. 研究位置：东京服务器（峰值内存 630 MB、单组 R1 耗时 8.8 秒）。

## 正在进行

- 首轮 R1~R5 已完成，全部未达标（拼接年化夏普：R1 −0.470、R2 −0.607、R3 无法选参、R4 −0.939、
  R5 −1.078）；逐组数字、诊断与判定见 `docs/research/rounds/R1.md` ~ `R5.md`。
- `trials_used` 36 / 200；`qualified_pool` 为空；没有候选，因此不涉及冻结。

## 下一步

- R6~R10（ai_proposed）：按第八部分要求，每轮先在 `rounds/<轮次>.md` 写明机制、文献/市场结构依据、
  与前四个方向的区别，再建网格（每轮 ≤ 10 组）并运行。
- 已发现的硬约束：100 USDT × 0.75% 风险 ÷ 15% 止损 ≈ 4.9 USDT 名义，低于最小下单额 5 × 1.1；
  宽止损策略会被大量丢弃。新方向必须优先设计止损较紧、换手较低的方向，或明确说明丢弃仓位的影响。
- 每轮完成后同步更新 `ledger.yaml` 与 `rounds/<轮次>.md`，并提交推送。

## 断点续跑说明

新会话收到本指令时：先读本文件与 `ledger.yaml`，从“下一步”继续。服务器上的研究报告在
`~/trade/artifacts/reports/<轮次>/grid.json`，试验登记 `~/trade/artifacts/reports/EXPERIMENTS.log`；
代码同步用 tar（排除 `docs/research/ledger.yaml`，避免覆盖服务器上的研究状态）。

提交状态：本次提交 `182a877`（feat(research): 建立研究框架并完成 R1~R5 首轮网格）已创建；
推送到 `origin/develop` 时本机到 GitHub 的连接被重置（`Recv failure: Connection was reset`），
重试 3 次仍未成功，**未推送**。下一次先执行 `git push origin develop`。
