# bn-trade

币安 **USDⓈ-M 永续多策略研究与实盘工具**，允许做多与做空，5x 逐仓，100 USDT 起步。

```text
规则化月度选币 → 研究期滚动验证调参（全部试验登记）→ 冻结候选
→ 最终检验窗口（只检验一次）→ 影子盘 shadow → 测试网 paper
→ 使用者启动 100 USDT 实盘 live → 实盘监控
```

## 当前怎么用

平时只需要把 **`指令/自动多轮/0_自动调度_每次发这个.txt`** 的全部内容发给 AI（Cline）。调度器读取
`docs/research/ledger.yaml` 与各结果文件，判断现在该做哪一项并执行：

| 指令文件 | 任务 |
| :--- | :--- |
| `00_一次性准备_最先发这个.txt` | 一次性准备（已完成，见 `docs/research/SETUP_DONE.md`） |
| `1_自动研究循环.txt` | 按预登记方向逐轮研究、调参、冻结候选 |
| `2_最终检验.txt` | 在从未读取过的窗口上检验候选一次 |
| `3_实盘工程与看板.txt` | 通用实盘引擎（shadow / paper / live）与看板 |
| `4_影子盘与上线评估.txt` | 影子盘运行与上线评估 |
| `5_实盘监控.txt` | 实盘日常监控与对账 |

也可以在其他窗口单独发送 1~5 号指令并行执行；各任务只通过 `docs/research/` 下的文件交接。

## 文档入口

| 文档 | 内容 |
| :--- | :--- |
| [docs/00-当前目标.md](docs/00-当前目标.md) | 目标、市场与风险包络、研究与验证流程 |
| [docs/design.md](docs/design.md) | 设计书索引 → `02` 架构与部署、`03` 数据与选币、`07` 实盘与风控、`09` 币安接口、`10` 看板、`13` 研究框架 |
| [docs/research/](docs/research/) | 任务交接文件：总账 `ledger.yaml`、轮次、候选、最终检验、影子盘、实盘记录、`decisions.md` |
| [docs/archive/](docs/archive/README.md) | 已结束的旧策略文档（分钟级突破模型、只做多轮动），只供查阅 |
| [AGENTS.md](AGENTS.md) | 工程规范与 14 条量化红线；`.clinerules/` 是它的摘要 |

## 目录结构

```text
src/trade/
  common/      共用口径：仓位、出场、成本、风控 R5~R8、日志
  data/        历史数据下载、聚合、规则化月度选币
  features/    指标与特征计算
  labels/      标签
  backtest/    回测与健全性检验（含 Deflated Sharpe）
  models/      旧策略的模型训练（已归档策略，代码保留）
  rotation/    旧策略的轮动实现（已归档策略，代码保留）
  live/        实盘客户端、订单、风控、对账、控制、通知
  dashboard/   看板（FastAPI + 静态前端）
scripts/       下载、聚合、回测、实盘入口、看板与运维脚本
configs/       YAML 配置（pydantic 校验）
tests/         与 src/trade/ 一一对应的 pytest 测试
docs/          设计书、`docs/research/` 交接文件、`docs/archive/` 归档
指令/          使用者下达的指令文件（不进 git）
data/ artifacts/   数据与制品（不进 git）
```

## 环境

- Python 3.11~3.13（本地 Windows、服务器 Ubuntu 均可用）。
- 本地研究可用 CUDA 版 torch（`torch==2.14.1+cu126`）；服务器实盘用 CPU 版 torch。
- 当前本地已装 Python 3.11.9 与 CPU 版 torch（2.15.0+cpu）；要做 GPU 训练请按下面“安装”用
  `requirements-research.lock` 重装。

## 安装

依赖按环境拆分（docs/02 §4/§5.2）：`requirements-base.lock`（共享核心）、`requirements-research.lock`
（本地研究，CUDA torch）、`requirements-live.lock`（服务器实盘，CPU torch），均由
`pip-tools --generate-hashes` 生成、哈希锁定。本地走国内镜像，服务器走官方源。

### 本地 Windows（研究，国内镜像）

```powershell
# 研究环境（含 lightgbm / matplotlib / plotly / CUDA torch）
python -m pip install -r requirements-research.lock -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
# 开发工具（pytest / ruff / mypy / pip-tools）
python -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

只跑测试与静态检查时，`pip install -e ".[dev]"` 已足够；`requirements-research.lock` 内置 PyTorch cu126
索引（`--extra-index-url https://download.pytorch.org/whl/cu126`）以获取 `torch==2.14.1+cu126`。

### 服务器（Ubuntu，实盘/看板，官方源）

```bash
python -m pip install -r requirements-live.lock
python -m pip install -e .
```

## 质量检查

完成任务前在仓库根目录全部通过（AGENTS.md §8）：

```powershell
ruff format --check .
ruff check .
mypy --strict src/trade/common src/trade/backtest src/trade/live
mypy src/trade/dashboard
pytest -q
```

辅助检查：

```powershell
python scripts/check_env.py            # 环境自检
python scripts/check_docs_sections.py  # 设计文档章节引用检查
```

## 数据与制品（不进 git）

- 研究数据（服务器）：`data/klines_1h/`、`data/klines_1d/`、`data/funding/`、`data/metrics_1h/`、
  `data/universe_hist/`、`data/history/`；选币快照 `data/universe/`。
- 运行产物：`artifacts/reports/`（含 `EXPERIMENTS.log`、`FINAL_RUNS.log`）、`artifacts/live/book/`
  （盘口录制，用于校准点差）、`artifacts/live/<候选ID>/`（状态库与日志）。
- 旧策略的 1 分钟 bars 与数据集已按准备清单清理；需要时随时可从 data.binance.vision 重新下载。

## 服务器

- 连接：`ssh tokyo`（仅密钥登录，密钥内容不写入任何文件）。项目在 `~/trade`，虚拟环境 `~/trade-venv`。
- **访问币安（REST、WebSocket、data.binance.vision）只在服务器执行**，本地不直连、不做代理或隧道。
- 服务器 `~/trade` 目前不是 git 仓库，代码用 `scp`/tar 同步；看板走 SSH 隧道：`scripts/sync.ps1 tunnel`
  （转发 `127.0.0.1:8501`）。

## 分支与提交

- 开发分支 `develop`（远端 `origin/develop`），稳定分支 `main`；临时分支 `feat/<阶段>-<简述>`、
  `fix/<简述>`、`docs/<简述>`。不直接提交到 `main`。
- 提交信息：`<类型>(<模块>): <中文简述>`，类型为 `feat|fix|refactor|test|docs|chore|perf`。
- 禁止提交：`data/`、`artifacts/`、`指令/`、`.env`、任何密钥、大于 1 MB 的文件（`.gitignore` 已覆盖）。

## 使用 Cline 时的规则文件说明

- 工作区根目录必须是本仓库根目录（`AGENTS.md` 与 `.clinerules/` 所在目录），否则 Cline 找不到规则文件。
- `.clinerules/00-core.md` 无 frontmatter，每个任务都注入：AGENTS.md 的红线 1~14、工作流程、质量命令、完成报告要点。它是 AGENTS.md 的摘要，冲突时以 AGENTS.md 为准。
- `.clinerules/10-docs.md` 的 `paths` 为 `docs/**`，只在上下文涉及设计文档时生效；`.clinerules/20-quant.md` 的 `paths` 为 `src/trade/**`、`scripts/**`、`configs/**`、`tests/**`，只在涉及量化核心代码时生效。
- `.clineignore` 让 Cline 不读取 `data/`、`artifacts/`、`docs/archive/`、`.env` 与密钥类文件（AGENTS.md §6 红线 6、13）。
- 摘要与 AGENTS.md 的一致性由 `tests/test_agent_rules.py` 校验：改 AGENTS.md 后同步 `.clinerules/` 并运行该测试。
- `指令/` 不进 git 仓库（`.gitignore` 已覆盖），只在本地作为任务来源。

## 长时脚本与 numba 编译缓存

`scripts/` 下的长时脚本可在启动前设置环境变量 `TRADE_NUMBA_CACHE=1`，让 numba 把编译结果缓存到模块旁的
`__pycache__`，后续进程直接复用（02 §7）。默认关闭：`pytest` 与一次性脚本不写缓存，缓存目录已在
`.gitignore` 覆盖。

```powershell
$env:TRADE_NUMBA_CACHE = "1"
python scripts/<长时脚本> <参数>
```

研究框架的长时脚本（下载、组合回测、研究循环）由 `1_自动研究循环.txt` 建立；旧策略的
`tuning_exits.py`、`tuning_models.py`、`train_walkforward.py`、`build_dataset.py`、`bench_backtest.py`
仍保留在 `scripts/` 供查阅。

## 安全

- 密钥只来自环境变量，不写入代码、配置、日志、测试、报告与通知消息；API Key 不开提现权限。
- `shadow` 模式不得调用任何下单、撤单、改杠杆、改保证金模式接口；真钱实盘的启动与加资金只由使用者决定。
- 任何时刻不得出现“有仓位无止损”的窗口：先下交易所侧止损并确认，才算开仓完成。
- 看板只监听 `127.0.0.1`，控制操作必须经 `trade.live.control` 与 PIN 二次确认，不提供清除停机标志或切换运行模式。

## 已知状态

- `tests/models/test_dataset.py::test_pinned_cpu_placement` 需要 CUDA 设备，纯 CPU 机器上失败。
- `docs/13-研究框架.md` 由 `1_自动研究循环.txt` 首次运行时建立。
