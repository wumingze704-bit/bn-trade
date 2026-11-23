# 一次性准备进度（进行中）

状态：已完成（2026-11-23，结论见 SETUP_DONE.md）

## 一、指令文件检查（第二部分第 1 条）

`指令/` 下原有 7 个指令文件（文件名用连字符），已统一放入 `指令/自动多轮/` 并改名为调度文件
`0-自动调度-每次发这个.txt` 中引用的下划线形式：

- 0_自动调度_每次发这个.txt
- 00_一次性准备_最先发这个.txt
- 1_自动研究循环.txt
- 2_最终检验.txt
- 3_实盘工程与看板.txt
- 4_影子盘与上线评估.txt
- 5_实盘监控.txt

结果：7 个文件齐全，通过。`指令/` 下没有其他旧指令文件夹或文件。

## 二、git 检查（第二部分第 2 条）

- 分支：仓库的实际开发分支名是 `devlop`（`origin/devlop`，远端不存在 `develop`；远端分支为
  `main`、`devlop`、`feat/p4-p6-dashboard-models`）。按“develop 分支”的实际含义使用 `devlop`，
  不新建 `develop`（理由见 decisions.md）。
- 准备前未提交改动：`docs/rotation/decisions.md`、`docs/rotation/progress_round2.md`、
  `docs/rotation/round2.md`、`scripts/rotation_grid_scan.py`（`指令/` 未跟踪，已排除）。
  已单独提交 `chore: 准备前快照`（0c90cf2）。
- `git pull --rebase origin devlop`：成功（当前分支已是最新）。
- `git remote -v`：`origin https://github.com/wumingze704-bit/bn-trade.git`（fetch/push）。

## 三、Python 环境与测试基线（第二部分第 3 条）

- `python --version`：Python 3.11.9（`C:\Users\32837\AppData\Local\Programs\Python\Python311\python.exe`），≥ 3.11。
- 改动前首次 `pytest -q`：78 errors during collection（`ModuleNotFoundError: numba` 等，
  本地解释器缺少项目依赖）。按仓库锁文件补装基础依赖（`pip install -e ".[dev]"` 以及
  lightgbm / fastapi / uvicorn / binance-sdk / matplotlib / plotly）与 CPU 版 torch 2.15.0 后复测。
- 依赖安装完成后的基线 `python -m pytest -q`：
  `1 failed, 690 passed, 3 skipped, 16 warnings in 103.11s`。
  失败项：`tests/models/test_dataset.py::test_pinned_cpu_placement`
  （`RuntimeError: Cannot access accelerator device when none is available`，本机无 CUDA 设备；
  本任务不修，测试断言不改）。
- 其它质量命令基线：`ruff format --check .` → 280 files already formatted；
  `ruff check .` → All checks passed；`mypy --strict src/trade/common src/trade/backtest src/trade/live`
  → Success（46 files）；`mypy src/trade/dashboard` → Success（8 files）。

## 四、东京服务器（第二部分第 4 条）

- `ssh tokyo` 可连通。系统 Ubuntu 26.04（内核 7.0.0-30-generic），系统 Python 3.14.4。
- 磁盘：`/` 共 59G，已用 48G，剩余 8.4G（86%）。
- 内存：总 3654 MB，已用 702 MB，free 1050 MB，available 2951 MB；swap 2047 MB。
- `~/trade` 与 `~/trade-venv` 均存在。
- `ssh tokyo 'cd ~/trade && git status'`：**服务器上的 `~/trade` 不是 git 仓库**
  （`fatal: not a git repository`），因此无法 git pull，代码同步只能走 `scripts/sync.ps1`/rsync。
  服务器代码状态：以文件形式存在，无法用 git 记录版本（需要使用者处理，见 SETUP_DONE.md）。
- 目录占用（删除前，`du -sh`）：
  data/bars 8.7G、data/bars_r0_15 7.7G、data/features 4.9G、data/raw 4.3G、data/labels 256M、
  data/raw_ioc 81M、data/daily 55M、data/universe 440K、data/raw_r0_15 8K；
  artifacts/live 5.9G（其中 book 5.9G）、artifacts/cache 3.1G、artifacts/signals 164M、
  artifacts/reports 8.8M、artifacts/bench 2.2M、artifacts/costs 24K、artifacts/exchange_info 2.1M。
- 进程检查：无 trade-runner / 下载 / 聚合进程；`systemctl list-units --type=service --state=running`
  只有系统服务；`systemctl list-units --type=scope` 只有登录会话；tmux 会话 `trade` 只有 1 个空闲 bash
  （`pane_current_command=bash`，位于 `/home/ubuntu`），没有脚本在运行；未发现使用待删目录的进程。
  `artifacts/live/book/` 是早前录制的盘口数据（5.9G），当前无录制进程。

## 五、网络（第二部分第 5 条，在服务器上）

- `https://data.binance.vision/?prefix=data/futures/um/monthly/klines/BTCUSDT/1h/` → HTTP 200。
- `https://fapi.binance.com/fapi/v1/ping` → HTTP 200，返回 `{}`。

## 六、本地数据占用（删除前）

- `data/bars` 8.29 GB（2077 文件）、`data/features` 48.85 GB（2142 文件）、
  `data/labels` 2.42 GB（2133 文件）、`data/daily` 0 GB（73 文件）、`data/universe` 0 GB（25 文件）。
- 本地没有 `data/raw`、`data/bars_r0_15`、`data/raw_r0_15`；`artifacts/` 为空目录。
- 本地 C: 剩余 44.2 GB。


## 七、删除执行结果（第四部分第 10 条）

- 服务器：删除了 `data/bars/`、`data/bars_r0_15/`、`data/features/`、`data/labels/`、`data/raw/`、
  `data/raw_r0_15/`、`artifacts/cache/`、`artifacts/bench/`、`artifacts/signals/`（清单与文件数见
  decisions.md）。`data/bars_r0_15/` 与 `artifacts/cache/` 内含 `root` 属主文件，首次 `rm -rf` 报
  `Permission denied`，改用 `sudo rm -rf` 后删除。磁盘：剩余 8.4 G → **38 G**。
- 服务器保留：`data/universe/`（440 K）、`data/daily/`（55 M）、`data/raw_ioc/`（81 M，不在清单内）、
  `artifacts/live/`（5.9 G，其中 `book/` 为盘口录制）、`artifacts/reports/`、`artifacts/costs/`、
  `artifacts/exchange_info/`、`artifacts/symbols_*.txt`。
- 本地：删除了 `data/features/`（2142 文件，48.85 GB）与 `data/labels/`（2133 文件，2.42 GB）；
  保留 `data/bars/`（8.29 GB）、`data/daily/`、`data/universe/`、`data/universe_rules.json`。
  本地剩余空间 44.2 GB → **94 GB**。
