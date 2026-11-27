# 研究循环进度（1 号自动研究循环）

## 当前状态（2026-11-27 14:00 UTC）

- 研究重置后首次进入普通研究轮次。迁移 M1~M7 全部完成（见 `progress_migration.md`）。
- `ledger`：`trials_used: 0`、`global_trial_budget: 400`、全部轮次 pending（X0 已置 researching）、
  `qualified_pool: []`、`candidates: []`；`void_history` 记录研究重置前作废的 153 次试验。
- 执行顺序（1 号第八部分与 0 号调度 M7 第 3 条）：X0~X4 纯做空探索轨最先 → R3 → R1、R2、R4、R5 →
  R6~R10 → R11、R12。
- 当前轮次：**X0**（`configs/strategies/round_X0.yaml`，2 组，模式 B）。
  - 假设与网格见 `rounds/X0.md`；服务器 `HEAD = d5ed53e`。
  - 运行命令（服务器 `~/trade`）：`nice -n 10 /home/ubuntu/trade-venv/bin/python
    scripts/portfolio_backtest.py --grid configs/strategies/round_X0.yaml`，日志
    `artifacts/reports/X0.log`。
  - 启动时间与结果：见下方“X0”一节（运行结束后追加）。
- 数据：`data/klines_1h`、`klines_1d`、`funding`、`klines_1h_flow`、`premium_1h`、`mark_1h`、
  `universe_hist` 均已在服务器（M3 完成）；`research_start = 2021-02-01`、
  `research_end = 2026-07-31`。
- 代码与文档：本轮补齐 X0~X4、R11、R12 方向与 `View` 访问器，修 D19/D23/D24（见 `review_log.md`）；
  §8 质量命令全部通过（`pytest -q` 唯一失败为本机无 CUDA 的既有测试）。
- 服务器最小规模运行（3 个月）已在服务器完成并通过：R3 模式 A/B 与 X0 模式 B，输出在
  `artifacts/reports/smoke/20261127T134759Z.json`、`20261127T135038Z.json`、`20261127T135042Z.json`。

## X0

启动：2026-11-27 14:00 UTC（服务器 `HEAD = d5ed53e`）。

结果：运行中，结束后写入本节（含选中参数、拼接曲线指标、七项情景、四档可执行性与判定）。
