# 一次性迁移进度（M1~M7）

执行文件：`指令/自动多轮/0-自动调度-每次发这个.txt` 第六部分。每完成一步即提交推送并更新本文件；
中断后再次执行从第一个未完成的步骤继续。

| 步骤 | 状态 | 提交 |
| :-- | :-- | :-- |
| M1 服务器改为 git 副本 | 完成 | `816c31b` |
| M2 配置（risk / equity / 风险档位 / 500 USDT） | 完成 | `35ee65a` |
| M3 数据补充与防幸存者偏差 | 未开始 | — |
| M4 研究框架修复（D1~D7）与可信度测试 | 未开始 | — |
| M5 仓位模式 B 与 β 中性 | 未开始 | — |
| M6 实盘安全修复（D8~D11）与引擎对齐 | 未开始 | — |
| M7 旧判定作废并准备重跑 | 未开始 | — |

## 当前状态

- 当前子步骤：M2 已完成并提交（`35ee65a`）：`configs/research.yaml` 增加 `risk`（reset / 7 天）与 `equity`
  （10000 / 500），组合上限改为 3.0 / 1.5；`configs/sizing_research.yaml` 初始权益 500 并新增三档 `risk_tiers`；
  `configs/live.yaml` 资金上限 500（范围 (0, 1000]）；`global_trial_budget` 400；
  研究与最终检验读 research 的 risk 节，实盘 / 测试网 / 影子盘固定 strict（新增测试）。
- 下一步：M3（数据补充与防幸存者偏差：在服务器下载主动买入成交、溢价指数、标记价格，
  重新生成 2021-01~2026-07 每月选币快照并按规则顺序补齐下架合约交易规则，统计写入 decisions.md）。

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
