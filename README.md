# bn-trade

币安 USDⓈ-M 永续多策略研究与实盘工具（允许做多与做空）。

当前工作按 `指令/自动多轮/` 下的指令执行：**平时只发 `指令/自动多轮/0_自动调度_每次发这个.txt`**，Agent 会读取 `docs/research/ledger.yaml` 与各结果文件判断该做哪一项（自动研究循环 / 最终检验 / 实盘工程与看板 / 影子盘与上线评估 / 实盘监控）并执行。

- 目标与风险包络：[docs/00-当前目标.md](docs/00-当前目标.md)
- 文档索引：[docs/design.md](docs/design.md)；任务交接文件：`docs/research/`
- 已结束的旧策略文档在 [docs/archive/](docs/archive/README.md)，只供查阅，不代表当前规格。

## 环境

- Python 3.11~3.13（本地以 3.13 为主，服务器用发行版 Python）
- Windows 研究环境使用 CUDA 12.6 版 torch（`torch==2.14.1+cu126`，来自 PyTorch cu126 索引）；Ubuntu 实盘环境使用 CPU 版 torch（`torch==2.14.1`，PyPI 默认）。

## 安装

依赖按环境拆分（docs/02 §4/§5.2）：`requirements-base.lock`（共享核心）、`requirements-research.lock`（本地研究，CUDA torch）、`requirements-live.lock`（服务器实盘，CPU torch），均由 `pip-tools --generate-hashes` 生成、哈希锁定。本地走国内镜像，服务器走官方源。

### 本地 Windows（研究，国内镜像）

```powershell
# 研究环境（含 lightgbm / matplotlib / plotly / CUDA torch）
python -m pip install -r requirements-research.lock -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
# 开发工具（pytest / ruff / mypy / pip-tools）
python -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

`requirements-research.lock` 内置了 PyTorch cu126 索引（`--extra-index-url https://download.pytorch.org/whl/cu126`）以获取 `torch==2.14.1+cu126`；CUDA 12.x 版本也可按 PyTorch 官方说明或阿里云 pytorch-wheels 镜像加速。

### 服务器（Ubuntu，实盘/看板，官方源）

```bash
python -m pip install -r requirements-live.lock
python -m pip install -e .
```

## 使用 Cline 时的规则文件说明

- 工作区根目录必须是本仓库根目录（`AGENTS.md` 与 `.clinerules/` 所在目录），否则 Cline 找不到规则文件。
- `.clinerules/00-core.md` 无 frontmatter，每个任务都注入：AGENTS.md 的红线 1~14、工作流程、质量命令、完成报告要点。它是 AGENTS.md 的摘要，冲突时以 AGENTS.md 为准。
- `.clinerules/10-docs.md` 的 `paths` 为 `docs/**`，只在上下文涉及设计文档时生效；`.clinerules/20-quant.md` 的 `paths` 为 `src/trade/**`、`scripts/**`、`configs/**`、`tests/**`，只在涉及量化核心代码时生效。
- `.clineignore` 让 Cline 不读取 `data/`、`artifacts/`、`docs/archive/`、`.env` 与密钥类文件（AGENTS.md §6 红线 6、13）。
- 摘要与 AGENTS.md 的一致性由 `tests/test_agent_rules.py` 校验：改 AGENTS.md 后同步 `.clinerules/` 并运行该测试。
- `指令/` 不进 git 仓库（`.gitignore` 已覆盖），只在本地作为任务来源。

## 长时脚本与 numba 编译缓存

`scripts/` 下的长时任务（`tuning_exits.py`、`tuning_models.py`、`train_walkforward.py`、`build_dataset.py`、`bench_backtest.py`）可在启动前设置环境变量 `TRADE_NUMBA_CACHE=1`，让 numba 把编译结果缓存到模块旁的 `__pycache__`，后续进程直接复用（02 §7）。默认关闭：`pytest` 与一次性脚本不写缓存，缓存目录已在 `.gitignore` 覆盖。

```powershell
$env:TRADE_NUMBA_CACHE = "1"
python scripts/tuning_exits.py --features-dir data/features/ds_20261107 --limit 2
```

## 质量检查

```powershell
python scripts/check_env.py
python -m pytest -q
ruff format --check .
ruff check .
mypy --strict src/trade/common src/trade/backtest src/trade/live
mypy src/trade/dashboard
```

`scripts/latency_probe.py` 会访问币安公开 USDⓈ-M 时间接口及 BTCUSDT 逐笔 WebSocket；它不读取 API Key，也不发送交易请求。网络或地区访问受限时应停止并记录实际错误，不得使用代理或隧道规避限制。
