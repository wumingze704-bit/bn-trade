结论：DONE

准备完成时间：2026-11-23 11:40 UTC（本地 19:40 CST）。执行文件：`指令/自动多轮/00_一次性准备_最先发这个.txt`。
提交：`54fe7f4`（chore(setup): 一次性准备），已推送到 `origin/develop`。

## 一、环境检查结果

- 指令文件：`指令/自动多轮/` 下 7 个文件齐全（0 调度、00 准备、1 研究循环、2 最终检验、3 实盘工程与看板、4 影子盘与上线评估、5 实盘监控）。
- git：开发分支 `develop`（远端 `origin/develop`；原分支名 `devlop` 为拼写错误，已改名并推送）；`git pull --rebase` 成功；`origin` = `https://github.com/wumingze704-bit/bn-trade.git`。
- Python：3.11.9（`C:\Users\32837\AppData\Local\Programs\Python\Python311\python.exe`）。
- 测试基线（改动前，补装依赖后）：`1 failed, 690 passed, 3 skipped, 16 warnings in 103.11s`。
  失败项：`tests/models/test_dataset.py::test_pinned_cpu_placement`（`RuntimeError: Cannot access accelerator device when none is available`，本机无 CUDA 设备，按指令不修改测试）。
  改动前首次运行为 78 errors during collection（本地解释器缺 numba/pandas/torch/lightgbm/fastapi 等依赖），已按 `pyproject.toml` 与锁文件补装（基础依赖 + lightgbm + fastapi + uvicorn + binance SDK + matplotlib + plotly + CPU 版 torch 2.15.0）。
- 改动后质量命令：`ruff format --check .` 通过（286 files already formatted）；`ruff check .` 通过；`mypy --strict src/trade/common src/trade/backtest src/trade/live` 通过（46 files）；`mypy src/trade/dashboard` 通过（8 files）；`pytest -q` → `1 failed, 690 passed, 3 skipped`（与基线一致，无新增失败）。
- 服务器：`ssh tokyo` 可连通；Ubuntu 26.04；删除前磁盘剩余 8.4 G（59 G 总量，86% 已用）、内存总 3654 MB / 可用 2951 MB；删除后磁盘剩余 **38 G**（34% 已用），内存可用 2911 MB。`~/trade` 与 `~/trade-venv` 均存在。
- 服务器代码状态：`~/trade` **不是 git 仓库**（`fatal: not a git repository`），无法 `git pull`。本次代码同步用 tar 打包上传解压（`scripts/sync.ps1` 的 `push-code` 依赖服务器 git 仓库，无法使用）。
- 网络（服务器上实测）：`data.binance.vision`（BTCUSDT 1h 月度目录）HTTP 200；`fapi.binance.com/fapi/v1/ping` HTTP 200，返回 `{}`。

## 二、归档清单（`git mv`，全部可恢复）

`docs/01-需求与范围.md`、`docs/04-特征与标签.md`、`docs/05-模型与训练.md`、`docs/06-回测引擎.md`、
`docs/08-开发计划与验收.md`、`docs/11-轮动策略.md`、`docs/records.md`、`docs/回测结果.md`、
`docs/tasks.md`、`docs/rotation/`（9 个文件）、`universe_report.md` → 全部在 `docs/archive/` 下（与原文件名一致，`docs/rotation/` → `docs/archive/rotation/`）。
`docs/12-*.md`、`docs/review.txt`、`docs/qa.txt` 不存在，无需处理；`tmp_train_lgbm.yaml`、`_commit_msg.txt` 不存在，无需 `git rm`。

## 三、删除清单（`data/` 与 `artifacts/`，按指令清单）

服务器（`~/trade`）：`data/bars/` 2149 文件 8.7 G、`data/bars_r0_15/` 1904 文件 7.7 G、
`data/features/` 238 文件 4.9 G、`data/labels/` 2372 文件 256 M、`data/raw/` 61324 文件 4.3 G、
`data/raw_r0_15/` 0 文件 8.0 K、`artifacts/cache/` 1641 文件 3.1 G、`artifacts/bench/` 38 文件 2.2 M、
`artifacts/signals/` 716 文件 164 M。`artifacts/models/`、`artifacts/quality/` 不存在。
`data/bars_r0_15/` 与 `artifacts/cache/` 含 root 属主文件，改用 `sudo rm -rf` 删除。

本地：`data/features/` 2142 文件 48.85 G、`data/labels/` 2133 文件 2.42 G（本地无 `data/raw/`）。

保留：`data/universe/`、`data/daily/`、`data/raw_ioc/`（服务器，不在清单内）、`artifacts/live/book/`（盘口录制）、`artifacts/reports/`、`artifacts/costs/`、`artifacts/exchange_info/`、本地 `data/bars/`。

## 四、新建与改写的文件

- 新建：`docs/00-当前目标.md`、`docs/tasks.md`、`docs/archive/README.md`、`docs/research/p5p6_backlog.md`、
  `docs/research/progress_setup.md`、`docs/research/decisions.md`、`docs/research/SETUP_DONE.md`（本文件）。
- 改写：`AGENTS.md`（红线 14 条、§1/§2/§5/§10 改为新体系）、`.clinerules/00-core.md`、`.clinerules/10-docs.md`、
  `.clinerules/20-quant.md`、`README.md`、`docs/design.md`、`docs/07-实盘与风控.md` 与 `docs/09-币安数据与API.md`
  的 `docs/records.md` 链接、`scripts/check_docs_sections.py`（归档文档算有效章节目标）、
  `tests/test_agent_rules.py`（解析适配 14 条红线）、`.clineignore`（+`docs/archive/`）、`.gitignore`（+`/指令/`）。

## 五、需要使用者处理的事项

1. **服务器 `~/trade` 不是 git 仓库**：无法 `git pull`，本次用 tar 同步。是否在服务器上 `git init` + 配置
   `origin` 由使用者决定（属于服务器配置类操作，本任务未执行）。
2. **`tests/models/test_dataset.py::test_pinned_cpu_placement` 仍失败**：本机无 CUDA 设备。需要在有 GPU 的机器
   或 CUDA 版 torch 上复核；按指令不修改测试。
3. **本地 torch 是 2.15.0+cpu**（`requirements-research.lock` 为 `2.14.1+cu126`）：本任务只装了 CPU 版让测试可跑；
   如需 GPU 训练，按 README 用 `requirements-research.lock` 重装。
4. **`scripts/check_docs_sections.py` 对重写后的 `docs/design.md` 报差异**（标题不一致、旧决策表的数字 token 与
   `H_label`/`H_max`/`bf16` 配置键丢失，共 21 处）：这是“设计书由完整规格改写为索引”的预期差异，旧内容已归档在
   `docs/archive/`。是否调整该脚本对新文档的检查口径，需要使用者决定。
5. **`docs/13-研究框架.md` 尚未存在**：由 `1_自动研究循环.txt` 首次运行时创建，`docs/design.md` 已把它列为当前有效文档。
6. **服务器上已有旧文档副本**：同步后把服务器 `~/trade/docs` 下的旧文档移到 `~/trade/docs/archive/`（未删除）。
