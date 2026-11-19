# phase2：测试段（M13~M22）确认回测结论

结论：FAIL

- 评估区间：测试段 M13 起点 2025-10-01 ~ M22 末尾 2026-08-01（10 个 UTC 自然月）。打分与均线预热由同一次运行覆盖的 M1~M12 数据提供，预热不计入测试段收益。
- 冻结配置：`configs/rotation_frozen.yaml` + `configs/sizing.yaml`（`position_frac=0.08`、`risk_per_trade=0.01`、`leverage=5`、`max_positions=5`、`risk` 段默认值）。
- 单次运行：`run_id=rot_confirm`、`stage=confirm`，一次跑完，未重跑。全部数字来自 `artifacts/reports/rotation/rot_confirm/` 的实际产物（`summary.json`、`equity.parquet`、`trades.parquet`、`neighbors.json`、`confirm_summary.json`）。
- 判定结果：6 项固定标准中第 1、3、5 项不通过，结论 FAIL。未根据测试段结果做任何调整。

## 1. 前置检查

| 检查项 | 要求 | 实测 | 结论 |
| :--- | :--- | :--- | :--- |
| `docs/rotation/phase1.md` 结论 | PASS | `结论：PASS` | 通过 |
| 冻结文件 SHA256（10 个） | 与 phase1.md 一致 | 逐个一致（见 §2） | 通过 |
| `docs/rotation/phase2.md` | 不存在或结论为 NOT_READY / CRASHED | 运行前不存在 | 通过 |
| `ruff format --check .` | 通过 | 272 files already formatted | 通过 |
| `ruff check .` | 通过 | All checks passed | 通过 |
| `mypy --strict src/trade/common src/trade/backtest src/trade/live` | 通过 | Success: no issues found in 46 source files | 通过 |
| `mypy src/trade/dashboard` | 通过 | Success: no issues found in 8 source files | 通过 |
| `pytest -q` | 通过 | 691 passed, 3 skipped | 通过 |

## 2. 冻结文件 SHA256 核对

| 文件 | SHA256（phase1.md 记录 = 本次实测） | 结论 |
| :--- | :--- | :--- |
| `configs/rotation_frozen.yaml` | `e93cdba1fa8c256a8441efd0642b4816fea24032d13c68770bfb7160eeaddd6e` | 一致 |
| `configs/sizing.yaml` | `399e4e27680056751c3af06a38c57d667883f024b17d07108b25bc52d5f9dfb1` | 一致 |
| `scripts/rotation_backtest.py` | `e6bebb4b90ea22c6c09dbbe5d57536a00ca034f61a38220a2c50029184d5f727` | 一致 |
| `src/trade/rotation/__init__.py` | `dbcbe77270d2c43910b31a02e017da230a80d2c1dee3c28c036e4fdd2ece148d` | 一致 |
| `src/trade/rotation/config.py` | `169f9f4a12841aaa87ac207955874e61ad3df88c6bd810be6c7e0c4e3dba1954` | 一致 |
| `src/trade/rotation/data.py` | `22d47c6cdf81b7ef3e3642ba3341f5fa52994510b8e4641809dbb333c64ab9fa` | 一致 |
| `src/trade/rotation/engine.py` | `e6ee989057b6b72d9c0d4a75bb02c8ecd58a3dca7cabf5a5d301780557a4b3f0` | 一致 |
| `src/trade/rotation/metrics.py` | `1329bdc15c52c653d5e8be92ad8c4ff4f0775e2a46cce55bbb746321d8b9aef6` | 一致 |
| `src/trade/rotation/signals.py` | `19f161536c3c096d1734f691f6d4f746319d25dcba79365d68bac3b60de18421` | 一致 |
| `src/trade/rotation/stage.py` | `1f02e457068dc95d4717558d97d32235df5eb8f47ae9e3266cc496eac8f9fa90` | 一致 |

核对方式：脚本读取 `docs/rotation/phase1.md` §6 表格中的路径与哈希，逐个用 `hashlib.sha256(file).hexdigest()` 比对，10 个文件全部相同（`ALL_MATCH True`）。

## 3. 测试段（M13~M22）情景对比（判定口径）

区间起点权益 125.9007 USDT（M12 最后一日权益），base 期末权益 115.9136 USDT。

| 情景 | 年化夏普 | 净收益 | 最大回撤 | 交易笔数 | 亏损月数 | 最差月 | 成本占比 | 换手（年化） | bootstrap 下限 | bootstrap 上限 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| base | −0.711 | −7.93% | 14.71% | 203 | 3 | −8.43% | — | 58.6 | −0.000964 | +0.000558 |
| cost2x | −1.060 | −11.22% | 15.34% | 203 | 3 | −8.73% | — | 57.6 | −0.001083 | +0.000422 |
| nohalt | −0.711 | −7.93% | 14.71% | 203 | 3 | −8.43% | — | 58.6 | −0.000964 | +0.000558 |
| delay1 | −0.422 | −4.46% | 12.47% | 194 | 3 | −5.96% | — | 54.5 | −0.000784 | +0.000568 |
| shuffle | −1.810 | −9.96% | 11.49% | 144 | 3 | −4.58% | — | 43.1 | −0.000745 | +0.000063 |

- 成本占比 = Σ(按 `p_1s` 计的毛收益 − 净收益) ÷ Σ毛收益；测试段毛收益 ≤ 0 时无定义，写 —。base 测试段总成本 4.3716 USDT，cost2x 7.8985 USDT。
- 胜率 35.96%、盈亏比（profit factor）0.817、持仓天数中位数 0.67 天（base）。
- 日收益均值 bootstrap：10000 次整日重抽样、种子 42、样本 304 个 UTC 自然日，点估计 −0.00025。
- `nohalt` 与 `base` 全部指标一致：测试段内 R6/R8 停机与 R7 暂停均未触发。

## 4. base 逐月收益（M13~M22）

| 月份 | 2025-10 | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 | 2026-06 | 2026-07 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 收益 | −3.06% | 0.00% | 0.00% | −6.89% | 0.00% | +0.68% | +3.52% | +6.88% | 0.00% | −8.43% |

- 亏损月 3 个（2025-10、2026-01、2026-07），最差月 2026-07 的 −8.43%。
- 收益记 0.00% 的 4 个月（2025-11、2025-12、2026-02、2026-06）该月日末权益完全不变（月内最小值 = 最大值），即整月空仓、无成交；这与“大盘过滤不通过时清空持仓且不开仓”的规则一致，但落盘产物没有逐次调仓的过滤状态，无法逐条复核当时是过滤不通过还是没有合格候选。
- 区间内权益峰值 135.9108 USDT（2026-05），最大回撤 14.71% 由该峰值回落至期末 115.9136 USDT。

## 5. 完整 confirm 窗口（M2~M22）情景对比（`summary.json` 原值）

| 情景 | 年化夏普 | 净收益 | 最大回撤 | 交易笔数 | 亏损月数 | 最差月 | 成本占比 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| base | 0.650 | +15.91% | 16.69% | 513 | 8 | −8.43% | 0.361 |
| cost2x | 0.353 | +7.38% | 17.78% | 513 | 8 | −8.73% | 0.693 |
| nohalt | 0.650 | +15.91% | 16.69% | 513 | 8 | −8.43% | 0.361 |
| delay1 | 0.526 | +11.75% | 14.10% | 506 | 8 | −6.45% | 0.421 |
| shuffle | −0.526 | −13.30% | 19.85% | 924 | 9 | −8.06% | 87.788 |

切片口径自校验：完整窗口 +15.91% 等于调参期 125.9007/100 − 1 = +25.90% 与测试段 −7.93% 的连乘（1.259007 × 0.920674 = 1.15913），说明测试段切片与同一次运行的权益曲线一致。

## 6. 判定逐项

| 判据 | 要求 | 实测（base，测试段） | 结论 |
| :--- | :--- | :--- | :--- |
| 1. base 净收益 > 0 且年化夏普 > 0.8 | net_return > 0，sharpe > 0.8 | net_return = −7.93%，sharpe = −0.711 | **不通过** |
| 2. base 最大回撤 ≤ 25% | max_drawdown ≤ 0.25 | 14.71% | 通过 |
| 3. cost2x 净收益 > 0 | net_return > 0 | −11.22% | **不通过** |
| 4. 10 个月中亏损月 ≤ 4，最差月 ≥ −12% | losing_months ≤ 4，worst_month ≥ −0.12 | 3 个月，−8.43% | 通过 |
| 5. bootstrap 95% 置信区间下限 > 0 | bootstrap_mean_low > 0 | −0.000964 | **不通过** |
| 6. neighbors 中至少一半净收益 > 0 | ≥ 一半 | 2/4 组为正 | 通过 |

合计 3 项通过、3 项不通过，结论 **FAIL**。第 1、3、5 项不通过，三项都指向同一事实：冻结配置在测试段 10 个月里没有正收益，且统计上不显著（置信区间含 0）。风险控制项（回撤、月度亏损分布）仍在阈值内。

`neighbors`（冻结开关不变，`base` 情景，confirm 窗口 M2~M22 区间值）：

| 变体 | 夏普 | 净收益 | 最大回撤 | 笔数 |
| :--- | ---: | ---: | ---: | ---: |
| N20 L14 K3 | 0.496 | +12.14% | 16.24% | 583 |
| N50 L3 K3 | −0.017 | −2.25% | 18.82% | 511 |
| N50 L7 K3 | −0.348 | −8.93% | 18.70% | 435 |
| N50 L14 K5 | 0.366 | +8.28% | 18.17% | 376 |

## 7. 泄露检查

- 触发条件（任一月收益 > 20% 或年化夏普 > 3）：**未触发**。完整 confirm 窗口最高月收益 +14.06%（2025-07，属调参期），测试段最高月收益 +6.88%（2026-05），测试段年化夏普 −0.711。
- `delay1`（信号整体延后一个调仓周期，测试段）：夏普 −0.422、净收益 −4.46%。比 base 高 3.47 个百分点但同样为负；在负收益区间这只是路径差异，不构成“更快信号带来更高收益”的迹象。
- `shuffle`（截面打分随机打乱，种子 42，测试段）：夏普 −1.810、净收益 −9.96%、144 笔，低于 base，符合“打乱后没有 alpha”。
- `cost2x`（成本 × 2，测试段）：−11.22%，比 base 差 3.29 个百分点。
- 逐行核对打分与过滤的数据下标（`trade.rotation.signals`、`trade.rotation.engine`）：
  - 大盘过滤用 `i_f = hour_index(t) − 1`，即收盘于调仓时刻 `t` 的那根 1h K 线；
  - 打分用 `i_last = hour_index(t − score_lag_hours) − 1`，即收盘于 `t − 4h` 的最后一根 1h K 线，OI 与资金费取同一下标（资金费过滤另用 `i_f`）；
  - 成交价用 `open_time = t` 那一分钟的 `p_1s`；止损只用入场分钟之后的 1m `low`，入场分钟不参与判定；ATR 只用满足 `open_time + 4h ≤ t` 的 4h K 线。
- 未来函数单测 `tests/rotation/test_signals.py::test_未来函数_修改调仓时刻之后的数据不改变决策`（把 `t` 及之后的小时收盘价整体改写后过滤状态、排名与名次不变）在本次运行前的 `pytest -q` 中通过。

结论：没有发现泄露；测试段收益为负，因此也不属于“结果异常偏高”的情形。

## 8. 运行方式与产物

运行命令（仓库根目录，Python 3.13.7，`trade` 包已可导入）：

```text
python scripts/rotation_backtest.py --stage confirm --run-id rot_confirm \
    --rotation configs/rotation_frozen.yaml \
    --scenarios base,cost2x,nohalt,delay1,shuffle --kind test
python scripts/rotation_backtest.py --stage confirm --run-id rot_confirm \
    --rotation configs/rotation_frozen.yaml \
    --neighbors-from docs/rotation/phase1.md
python scripts/rotation_report_confirm.py --run-dir artifacts/reports/rotation/rot_confirm
```

产物目录 `artifacts/reports/rotation/rot_confirm/`：

| 文件 | 内容 |
| :--- | :--- |
| `summary.json` | 完整 confirm 窗口（M2~M22）五个情景的全部指标 |
| `equity.parquet` / `trades.parquet` | 日末盯市权益与逐笔明细（五个情景） |
| `report.md` | 脚本生成的完整窗口情景与月度/分段表 |
| `neighbors.json` | 4 个相邻组的 `base` 指标 |
| `confirm_summary.json` | 测试段（M13~M22）全部指标、逐月收益、判定与泄露检查 |
| `confirm_report.md` | 上述内容的表格版 |
| `console.log` / `neighbors_console.log` | 两次运行的脚本输出 |

`artifacts/reports/EXPERIMENTS.log` 中本次运行的两条记录：开始记录（`event=start`、`kind=test`、`stage=confirm`）与脚本完成记录（`kind=test`、`sharpe=0.6497`、`net_return_pct=0.1591`、`cost2x_net_return_pct=0.0738`、`failed=false`）。

## 9. 口径说明与限制

1. 测试段结果只产生一次，未重跑；本次运行没有崩溃。
2. `confirm` 阶段的评估区间写死在冻结文件 `src/trade/rotation/stage.py`（M2 起点 ~ M22 末尾），没有只跑 M13~M22 的开关。因此单次运行产出 M1 预热 + M2~M22 的完整曲线，判定所需的 M13~M22 指标由同一次运行落盘的 `equity.parquet` / `trades.parquet` 切片、再用冻结的 `trade.rotation.metrics.compute_metrics` 计算，切片区间起点权益取 M12 最后一日权益（125.9007 USDT），未重算策略（见 `docs/rotation/decisions.md` 第 20 条）。
3. 切片结果里不含 `skipped`、`risk_events`、`filter_off_decisions` 明细：这三项没有落盘在产物中，切片不伪造，报告里也不引用。
4. `neighbors` 的净收益是 confirm 窗口（M2~M22）的区间值，脚本对邻居组只落盘该口径；冻结组同一口径的值为 +15.91%。
5. 成本占比在测试段毛收益 ≤ 0 时无定义（写作 —）；base 测试段总成本 4.3716 USDT。
6. 换手率按评估年数 304/365 年化。
7. 未读取 M23~M24：`confirm` 的 `allow_holdout=False`，且本地 `data/bars` 没有 202608 及之后的月文件。
