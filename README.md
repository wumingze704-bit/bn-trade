# bn-trade

币安山寨币超短线做多量化工具。

工程阶段与验收标准见 [docs/design.md](docs/design.md) 和 [docs/08-开发计划与验收.md](docs/08-开发计划与验收.md)。

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

## P0 检查

```powershell
python scripts/check_env.py
python -m pytest -q
ruff format --check .
ruff check .
mypy --strict src/trade/common src/trade/backtest src/trade/live
mypy src/trade/dashboard
```

`scripts/latency_probe.py` 会访问币安公开 USDⓈ-M 时间接口及 BTCUSDT 逐笔 WebSocket；它不读取 API Key，也不发送交易请求。网络或地区访问受限时应停止并记录实际错误，不得使用代理或隧道规避限制。
