# 前置复核记录（只追加）

同一复核对象的最新提交哈希与上次相同时，只写一行“未变化，沿用某时间的结论”。

## 2026-11-24 10:45 UTC 会话级一致性检查（0 号调度第五部分第 1 条）

- 总账：`docs/research/ledger.yaml` 的 `trials_used = 65`；rounds 的 trials 之和
  12+6+8+6+4+9+6+6+4+4 = 65；`qualified_pool` 为空（无冻结仓位选择的试验）；
  与服务器 `artifacts/reports/EXPERIMENTS.log` 中 `kind: tuning` 记录数 65 一致 → **符合**。
- 候选与窗口：`candidates` 为空，`final_windows` 三个窗口均为 `unused`、`candidate: null`
  → 不存在一个窗口对应两个候选 → **符合**。
- 服务器进程：无 `trade-runner@*` 实例；`trade-dashboard` 与 `trade-watchdog.timer` 为 active
  → 与 `ops` 全为空一致 → **符合**。
- 结论：全部符合。执行对象：一次性迁移 M1~M7（`progress_migration.md` 不存在）。

## 2026-11-24 10:44 UTC M1 服务器转换前的差异核查

- 复核对象：服务器 `~/trade`（552 个非数据文件）与本地 develop `b59c437`。
  方法：本地 `git bundle create` → scp → 服务器临时 `GIT_DIR=/tmp/m1ref/.git`、
  `GIT_WORK_TREE=/home/ubuntu/trade`，`git read-tree develop` + `git update-index --refresh` +
  `git diff-index --name-status develop`（不触碰 `~/trade` 内容）。
- 结果：25 处差异，全部是服务器为旧版（UTF-8 BOM、旧排版、缺 `rounds/R6~R10.md` 与 `tests/live/*`），
  没有服务器独有的较新代码；`data/history/manifest.json` 的 SHA256 转换前后一致；
  服务器没有 `.env.*` 与私钥文件 → **符合**。
- 处理：按指令转换并记录差异清单（`decisions.md`）。

## 2026-11-24 11:20 UTC M2 配置核查

- 复核对象：`configs/research.yaml`、`configs/sizing_research.yaml`、`configs/live.yaml`、
  `src/trade/research/config.py`、`src/trade/live/runner.py`（提交 `35ee65a`）。
- 结果：研究与最终检验读 `configs/research.yaml: risk`（reset、7 天）；实盘 / 测试网 / 影子盘固定 strict
  且有测试（`tests/live/test_runner_halt.py`）；两种权益口径 10000 / 500；组合上限 3.0 / 1.5；
  实盘资金上限 500（范围 (0, 1000]）；`global_trial_budget` 400 → **符合**。
- 待办：缺陷 D1~D11 尚未登记，按迁移 M4（D1~D7）与 M6（D8~D11）执行时登记为 OPEN。
