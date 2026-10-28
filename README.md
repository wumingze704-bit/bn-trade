# bn-trade

币安山寨币超短线做多量化工具。

工程阶段与验收标准见 [docs/design.md](docs/design.md) 和 [docs/08-开发计划与验收.md](docs/08-开发计划与验收.md)。

## 环境

- Python 3.11~3.13（本地以 3.13 为主，服务器用发行版 Python）
- Windows 研究环境按 PyTorch 官方说明安装 CUDA 12.x 对应版本；Ubuntu 实盘环境使用 CPU 版本。

## 安装

本地（中国大陆、不开 VPN）与服务器的安装命令一致，区别只在 PyPI 镜像源：本地走国内镜像，服务器走官方源（docs/02 §5.2）。

### 本地 Windows（国内镜像）

```powershell
# 依赖（清华镜像；也可换 https://mirrors.aliyun.com/pypi/simple/）
python -m pip install -r requirements.lock -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

PyTorch CUDA 12.x 版本请按 PyTorch 官方说明或阿里云 pytorch-wheels 镜像安装，版本与 `requirements.lock` 一致。

### 服务器（Ubuntu，官方源）

```bash
python -m pip install -r requirements.lock
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
