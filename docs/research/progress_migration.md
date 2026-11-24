# 一次性迁移进度（M1~M7）

执行文件：`指令/自动多轮/0-自动调度-每次发这个.txt` 第六部分。每完成一步即提交推送并更新本文件；
中断后再次执行从第一个未完成的步骤继续。

| 步骤 | 状态 | 提交 |
| :-- | :-- | :-- |
| M1 服务器改为 git 副本 | 完成 | `816c31b` |
| M2 配置（risk / equity / 风险档位 / 500 USDT） | 完成 | `35ee65a` |
| M3 数据补充与防幸存者偏差 | 进行中 | — |
| M4 研究框架修复（D1~D7）与可信度测试 | 未开始 | — |
| M5 仓位模式 B 与 β 中性 | 未开始 | — |
| M6 实盘安全修复（D8~D11）与引擎对齐 | 未开始 | — |
| M7 旧判定作废并准备重跑 | 未开始 | — |

## 当前状态

- 当前子步骤：M3 进行中。`scripts/fetch_history.py` 已支持 `flow` / `premium` / `mark` 三类补充数据
  （独立目录与 manifest、`flow` 逐行核对 `open_time`）；新增 `src/trade/data/rules_research.py` 与
  `scripts/build_universe_hist.py` 的规则来源顺序（exchange → snapshot → synthetic）与
  `rules_source.json` / `summary.json`。服务器上正在下载 2020-10~2026-07 的补充数据
  （`systemd-run` 单元 `m3-fetch`，MemoryMax=1500M、nice 10）。
- 下一步：下载完成后备份→重生成 2021-01~2026-07 选币快照，写入新旧对比与最小下单额统计，再更新
  `docs/03`、`docs/13` 并在服务器上做最小规模验证。


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
