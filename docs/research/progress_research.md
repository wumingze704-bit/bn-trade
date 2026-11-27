# 研究循环进度（1 号自动研究循环）

## 当前状态（2026-11-27 16:10 UTC）

- 研究重置后首次进入普通研究轮次。迁移 M1~M7 全部完成（见 `progress_migration.md`）。
- `ledger`：`trials_used: 2`（= X0 的 2 组 `kind=tuning`）、`global_trial_budget: 400`；
  X0 已判定 `reported_fail`，其余轮次 pending；`qualified_pool: []`、`candidates: []`；
  `void_history` 记录研究重置前作废的 153 次试验。
- 执行顺序（1 号第八部分与 0 号调度 M7 第 3 条）：X0~X4 纯做空探索轨最先 → R3 → R1、R2、R4、R5 →
  R6~R10 → R11、R12。
- 当前轮次：**X1**（`configs/strategies/round_X1.yaml`，6 组，模式 B）。
- 数据：`data/klines_1h`、`klines_1d`、`funding`、`klines_1h_flow`、`premium_1h`、`mark_1h`、
  `universe_hist` 均已在服务器（M3 完成）；`research_start = 2021-02-01`、`research_end = 2026-07-31`。
- 服务器 `HEAD = d5ed53e`（代码树与本地一致；本地只多出 `.gitignore` 与 `docs/research/` 提交）。

## X1

未运行。

## X0

已完成并判定 `reported_fail`（2026-11-27 13:52 UTC 启动、15:51 UTC 结束，约 2 小时）。

- 选中参数 `basket_n = 20`（5 折全部选中），信号口径 10,000 USDT 拼接曲线：年化夏普 **0.1243**、
  净收益 **−10.50%**、复合月化 −0.2056%、最大回撤 48.86%、最长水下 1,054 天、最差月 −18.80%、
  最差单周 −16.43%；交易 1,204 笔、换手 97.6 倍、成本 488.09（占毛收益 0.485）、资金费收入 +443.49、
  空头毛 −1,005.52、实现 β −0.5245、alpha 夏普 0.6270、DSR 0.0137、bootstrap 90% [−0.000648, +0.000910]。
- 5 折净收益 +24.83% / +4.88% / −24.23% / −0.17% / −9.63%（2/5 为正）；情景：cost2x −22.29%、
  nohalt +19.74%、delay1 +51.12%（> base + 0.30，g 款不通过）。
- 四档 500 USDT：标准 夏普 0.1179 / 月化 −0.165% / 丢弃 20.2%；进取 −0.2681 / −2.276% / 54.6%；
  激进 −0.4938 / −3.925% / 78.2%；极限 −0.1501 / −3.705% / 77.7% → `qualified_exec_tiers = []`。
- 达标：仅 shuffle（不适用）一条通过，其余 a、b、c、d、e、g（delay1）、h、i 均不通过 →
  `reported_fail`。结论：做空山寨篮子本身不赚钱（毛亏损 −1,006 + 成本 488，资金费 +443 只补回四成），
  且对执行时点高度敏感（delay1 反而 +51%）。
- 试验登记：`EXPERIMENTS.log` 新增 2 条 `kind=tuning` + 4 条 `kind=exec`；`trials_used` 由 0 改为 2。
- 结果与诊断全文见 `rounds/X0.md`；`grid.json` 的 `code_hash 77eed9e4…`、`data_hash c847434d…`、
  `config_hash 32a9a2d2…`。

