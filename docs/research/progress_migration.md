# 一次性迁移进度（M1~M7）

执行文件：`指令/自动多轮/0-自动调度-每次发这个.txt` 第六部分。每完成一步即提交推送并更新本文件；
中断后再次执行从第一个未完成的步骤继续。

| 步骤 | 状态 | 提交 |
| :-- | :-- | :-- |
| M1 服务器改为 git 副本 | 完成 | `816c31b` |
| M2 配置（risk / equity / 风险档位 / 500 USDT） | 完成 | `35ee65a` |
| M3 数据补充与防幸存者偏差 | 完成 | `18cbdee` |
| M4 研究框架修复（D1~D7）与可信度测试 | 进行中 | — |
| M5 仓位模式 B 与 β 中性 | 未开始 | — |
| M6 实盘安全修复（D8~D11）与引擎对齐 | 未开始 | — |
| M7 旧判定作废并准备重跑 | 未开始 | — |

## 当前状态

- 当前子步骤：M3 已完成（代码 `44f21cd` / `75448fb`，结果提交 `18cbdee`，文档 `f73aa7f`）。
  服务器补充数据：`flow` 20207、`premium` 19320、`mark` 20488 个文件，全部 `ok`、失败 0；
  选币快照 70 个月按新规则来源重生成（exchange 897 + synthetic 31，TradFi 241 排除），
  入选「币·月」3348，`research_start` 保持 2021-02-01；最小下单额预检 0/3337 被丢弃。
  下载与快照统计见 `decisions.md`。
- M4 已开始：`review_log.md` 登记 D1~D7、D12、D13（状态 OPEN），逐条附代码证据与影响；
  尚未修改任何研究框架代码。M4 前置复核同时确认：1 号 B 第 14 条的结果可信度测试在
  `tests/research/` 下尚不存在；服务器 HEAD `75448fb` 与本地 `src/`、`scripts/`、`configs/`
  完全一致（其后只有 docs 提交）。
- 下一步（M4）：按 `review_log.md` 的 D1~D7、D12、D13 逐条写复现测试再修复——
  D3 需把引擎主循环改为“t 时刻用 bar(t−1h) 的收盘盯市、在 open_time = t 的 K 线开盘成交”，
  同时修 `_mark` 的取价；D6 需要把止损触发切到 `mark_1h`；D1 需要新增“以折内选中参数从折起点按
  初始权益与空风控状态连续重跑”的拼接路径（`rolling.py` 与 `pipeline.py` 联动）；D2 需要把 R5
  的成分参数改为逐折选择；D12/D13 涉及试验记录字段、逐组落盘与两种权益口径。修完再实现 B 第 14 条
  a~h 的可信度测试，最后做 1 方向 × 1 组参数 × 3 个月的最小规模运行。
- 未推送提交（本地 develop，网络无法访问 GitHub）：`44f21cd`、`75448fb`、`f73aa7f`、`18cbdee`、
  `13d8113` 与本次缺陷登记提交；恢复网络后按顺序 `git push origin develop`。




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
