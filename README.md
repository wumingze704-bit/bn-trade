# bn-trade

币安山寨币超短线做多量化工具。

工程阶段与验收标准见 [docs/design.md](docs/design.md) 和 [docs/08-开发计划与验收.md](docs/08-开发计划与验收.md)。

## 环境

- Python 3.11
- Windows 研究环境按 PyTorch 官方说明安装 CUDA 12.x 对应版本；Ubuntu 实盘环境使用 CPU 版本。

## 安装

在仓库根目录执行：

```powershell
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
