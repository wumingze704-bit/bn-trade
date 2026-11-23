# phase1 进度（研究任务：调参期参数选择与冻结）

> 本文件只记进度与下一步，供上下文中断后接手；结论写 `docs/rotation/phase1.md`。

## 当前状态（更新至 2026-11-19）

- [x] 读 AGENTS.md、.clinerules、docs/06 §5、`configs/*.yaml`、`trade.common.sizing/risk_rules/funding/costs`、`trade.data.loader/universe`。
- [x] 改 `configs/sizing.yaml`：`mode=fixed_risk`、`position_frac=0.08`、`risk_per_trade` 起始 0.0075、第 3 轮后为 0.01。
- [x] `trade.common.sizing.calc_notional` 增加 fixed_risk 每仓上限截断（`position_cap`）+ 3 条新测试。
- [x] 新建 `src/trade/rotation/`（config/data/signals/engine/metrics/stage）、`configs/rotation.yaml`、`scripts/rotation_backtest.py`、`tests/rotation/`（29 条测试）。
- [x] 全量质量命令通过：`ruff format --check .`、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`、`mypy src/trade/dashboard src/trade/rotation`、`pytest -q`。
- [x] 第 1 轮 12 组（`rot_r1_*`）、第 2 轮 7 组（`rot_r2_*`）、第 3 轮 6 组（`rot_r3_*`）+ 冻结确认 5 情景（`rot_phase1_frozen`）+ neighbors（`rot_phase1_neighbors`）全部跑完。
- [x] 泄露检查：未触发四项特殊检查，`delay1` / `cost2x` / `shuffle` 与未来函数单测均通过。
- [x] `configs/rotation_frozen.yaml`、`docs/rotation/phase1.md`（结论 PASS + 网格 + 全部结果 + SHA256）、`docs/11-轮动策略.md`、`docs/design.md` 文档清单、`docs/06` §5、`docs/01` §5、`docs/07` R1、`docs/10` 顶栏示例 同步完成。
- [ ] 提交并 push 到 devlop 分支（最后一步）。

## 下一步

1. 复跑一遍 AGENTS.md §8 质量命令并复核 SHA256（确认冻结文件未变）。
2. `git add` 本次新建/修改的文件，提交并 push 到 `devlop`。

