# phase2 进度（测试段确认任务：M13~M22 一次性确认回测）

> 本文件只记进度与下一步，供上下文中断后接手；结论写 `docs/rotation/phase2.md`。

## 当前状态（更新至 2026-11-19）

- [x] 前置检查：phase1.md 结论 PASS、10 个冻结文件 SHA256 一致、phase2.md 运行前不存在、AGENTS.md §8 五条质量命令通过
- [x] EXPERIMENTS.log 写 `kind=test` / `run_id=rot_confirm` 开始记录（2026-11-19T13:21:22Z）
- [x] 运行 `scripts/rotation_backtest.py --stage confirm`（base/cost2x/nohalt/delay1/shuffle），一次跑完未重跑
- [x] 运行 neighbors（`--neighbors-from docs/rotation/phase1.md`）
- [x] 只读汇总脚本 `scripts/rotation_report_confirm.py` 产出 M13~M22 指标表（`confirm_summary.json` / `confirm_report.md`）
- [x] 判定并写 `docs/rotation/phase2.md`：结论 FAIL（第 1、3、5 项不通过）
- [x] EXPERIMENTS.log 补写完成记录（`event=complete`、`verdict=FAIL`）
- [x] 提交到 devlop（提交 `3531372`，5 个文件）
- [ ] push 到 origin devlop：两次尝试均失败（`Failed to connect to github.com:443`，本机当前无法联外网）；本地 devlop 领先 origin/devlop 2 个提交（含 phase1 的 `9848962`），有网络时执行 `git push origin devlop` 即可

## 下一步

1. 只剩 `git push origin devlop`（需外网）；之后本任务结束，不做下载新数据、封存集检验、影子盘或实盘工作。
2. `confirm` 已跑过且结论 FAIL，不得重跑；`--stage final` 因 phase2 非 PASS 被守卫拒绝。
