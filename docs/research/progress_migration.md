# 一次性迁移进度（M1~M7）

执行文件：`指令/自动多轮/0-自动调度-每次发这个.txt` 第六部分。每完成一步即提交推送并更新本文件；
中断后再次执行从第一个未完成的步骤继续。

| 步骤 | 状态 | 提交 |
| :-- | :-- | :-- |
| M1 服务器改为 git 副本 | 完成 | `816c31b` |
| M2 配置（risk / equity / 风险档位 / 500 USDT） | 完成 | `35ee65a` |
| M3 数据补充与防幸存者偏差 | 完成 | `18cbdee` |
| M4 研究框架修复（D1~D7、D12、D13）与可信度测试 | 完成 | `9c82edc`、`a2bafb4` |
| M5 仓位模式 B 与 β 中性 | 完成 | `5381f16` |
| M6 实盘安全修复（D8~D11）与引擎对齐 | 未开始 | — |
| M7 旧判定作废并准备重跑 | 未开始 | — |

## 当前状态

- M5 已完成（代码 `5381f16`）：
  - 仓位模式 B（`vol_target`）：按过去 `vol_lookback_days` 天日收益标准差的倒数分配权重，用过去
    `cov_lookback_days` 天日收益协方差的组合事前波动缩放到 `target_vol`；防灾止损固定 `execution.stop_max`，
    单仓名义 ≤ 权益 × `max_risk_per_trade` ÷ `stop_max`；中性策略按 60 天 β 调整两边名义，保证组合事前
    |β| ≤ `beta_abs_max` 且多头总名义 ÷ 空头总名义在 `ls_ratio_range` 内。
  - 配置项 `sizing_mode`、`target_vol`、`vol_lookback_days: 30`、`cov_lookback_days: 60`、`beta_lookback_days: 60`、
    `beta_abs_max: 0.10`、`ls_ratio_range: [0.5, 2.0]` 进入 `SizingConfig`（默认保持模式 A）与
    `configs/sizing_research.yaml`；`with_tier()` 用选中档位的 `target_vol` 覆盖。删除不再使用的 `portfolio.neutral_max_gap`。
  - 指标新增实现 β 与 alpha 夏普，达标项新增 j 款（中性策略 |β| ≤ 0.30 且 alpha 夏普 ≥ 0.8）。
  - 测试：`tests/research` 50 项全通过（新增 `test_metrics.py`、模式 B 手算 / 单仓上限 / β 中性 / β 样本不足、
    `test_h5_引擎按段使用模式B`、`test_h6_中性策略的β调整在引擎中生效`、配置项校验、取整状态一致性）；
    `pytest -q` = `1 failed, 826 passed, 3 skipped`，唯一失败为本机无 CUDA 的
    `tests/models/test_dataset.py::test_pinned_cpu_placement`（准备阶段既有失败，未修改测试）。
  - 服务器 `HEAD = 5381f16` 与本地一致；最小规模运行（R4 第 1 组参数 × 研究窗口最后 3 个月，模式 A、B 各一次）：
    - 模式 A（`fixed_risk`）：信号口径 448 笔、丢弃 0、年化夏普 -2.618、实现 β 0.086、alpha 夏普 -2.188；
      可执行口径 414 笔、应开仓 452、丢弃 38（8.4%）、年化夏普 -3.387、实现 β 0.082。
      结果文件 `artifacts/reports/smoke/20261124T144439Z.json`。
    - 模式 B（`vol_target`）：信号口径 442 笔、应开仓 445、丢弃 3（0.67%）、年化夏普 -1.178、实现 β 0.008、
      alpha 夏普 -1.118；可执行口径 371 笔、应开仓 464、丢弃 93（20.04%）、年化夏普 -0.632、实现 β 0.017。
      结果文件 `artifacts/reports/smoke/20261124T144647Z.json`。
    - 两次运行的 `funding_estimated` 分别为 28 / 4，`funding_missing 0`，`mark_fallback` 128 / 104；
      均未写 `EXPERIMENTS.log`（`kind=tuning` 仍为 65 条）。
- 下一步（M6）：实盘安全修复 D8~D11 与引擎对齐（紧急停机顺序、看门狗、专用账户与密钥文件权限、systemd 加固，
  引擎复用研究框架与模式 B / β 中性，实时数据视图含 `mark_1h` / flow / premium）。
- 推送：`origin/develop` 已到 `5381f16`，无未推送提交。


## M4 前置事实（2026-11-24 核查）




## M3 前置事实（2026-11-24 核查，只读）

- `data/universe_hist` 现有 72 个条目（含 `aliases.json`、`summary.json`），月份文件覆盖 2020-10 ~ 2026-07；
  M3 需为 2021-01 ~ 2026-07 重新生成快照，旧文件先复制到 `data/universe_hist_pre_m3/`（data/ 下的覆盖由
  M3 第 2 条授权，复制清单写入 `decisions.md`）。
- `artifacts/exchange_info/` 有 3 个历史快照，可作为交易规则来源②；其余下架币按来源③合成。
- 现有数据体量：`data/klines_1h` 551 MB、`data/klines_1d` 162 MB、`data/funding` 121 MB；磁盘剩余 37 GB。
- 需新增的三类数据目录尚不存在：`data/klines_1h_flow/`、`data/premium_1h/`、`data/mark_1h/`，
  对应 manifest 为 `data/history/manifest_flow.json`、`manifest_premium.json`、`manifest_mark.json`。
- `research_end` = 2026-07-31，下载月份上限为 2026-07（不得越过）。

## M1 记录

- 备份：服务器 `~/trade/artifacts/server_backup/20261124T104404Z.tar.zst`，552 个文件，1.79 MB
  （排除 `data/`、`artifacts/`、`.venv`、`__pycache__`）。
- 差异核对：服务器 552 个非数据文件 vs 本地 develop `b59c437`，25 处差异、无服务器独有较新代码
  （清单见 `decisions.md`）。
- 转换：服务器 `~/trade` 执行 `git init` 后从 bundle `git fetch`，`git checkout -f --detach b59c437`；
  `git rev-parse HEAD` = `b59c4375361e99709fa3c1541a29e1a1dd95c530` 与本地一致。
- `data/history/manifest.json` SHA256 转换前后均为 `bcb2dca37c5581416d5ceaaf17c3d40d98b5c9b6b75739dc4030a11cf27840ee`；
  服务器无 `.env.*` 与私钥文件（尚无实盘密钥），`~/.ssh/authorized_keys` 已是 600。
- 看板与看门狗：转换前 `systemctl is-active trade-dashboard trade-watchdog.timer` 均为 active，
  转换后 `curl 127.0.0.1:8501` 返回 200，无需重启。
- 服务器 `.git/info/exclude` 追加历史遗留目录与临时脚本（`.bak-code/`、`tmp_deploy/`、`_*.py` 等），
  保证服务器工作区 `git status` 干净；未执行任何 `git clean`。
