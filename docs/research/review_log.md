# 前置复核记录（只追加）

同一复核对象的最新提交哈希与上次相同时，只写一行“未变化，沿用某时间的结论”。

## 2026-11-24 10:45 UTC 会话级一致性检查（0 号调度第五部分第 1 条）

- 总账：`docs/research/ledger.yaml` 的 `trials_used = 65`；rounds 的 trials 之和
  12+6+8+6+4+9+6+6+4+4 = 65；`qualified_pool` 为空（无冻结仓位选择的试验）；
  与服务器 `artifacts/reports/EXPERIMENTS.log` 中 `kind: tuning` 记录数 65 一致 → **符合**。
- 候选与窗口：`candidates` 为空，`final_windows` 三个窗口均为 `unused`、`candidate: null`
  → 不存在一个窗口对应两个候选 → **符合**。
- 服务器进程：无 `trade-runner@*` 实例；`trade-dashboard` 与 `trade-watchdog.timer` 为 active
  → 与 `ops` 全为空一致 → **符合**。
- 结论：全部符合。执行对象：一次性迁移 M1~M7（`progress_migration.md` 不存在）。

## 2026-11-24 10:44 UTC M1 服务器转换前的差异核查

- 复核对象：服务器 `~/trade`（552 个非数据文件）与本地 develop `b59c437`。
  方法：本地 `git bundle create` → scp → 服务器临时 `GIT_DIR=/tmp/m1ref/.git`、
  `GIT_WORK_TREE=/home/ubuntu/trade`，`git read-tree develop` + `git update-index --refresh` +
  `git diff-index --name-status develop`（不触碰 `~/trade` 内容）。
- 结果：25 处差异，全部是服务器为旧版（UTF-8 BOM、旧排版、缺 `rounds/R6~R10.md` 与 `tests/live/*`），
  没有服务器独有的较新代码；`data/history/manifest.json` 的 SHA256 转换前后一致；
  服务器没有 `.env.*` 与私钥文件 → **符合**。
- 处理：按指令转换并记录差异清单（`decisions.md`）。

## 2026-11-24 11:20 UTC M2 配置核查

- 复核对象：`configs/research.yaml`、`configs/sizing_research.yaml`、`configs/live.yaml`、
  `src/trade/research/config.py`、`src/trade/live/runner.py`（提交 `35ee65a`）。
- 结果：研究与最终检验读 `configs/research.yaml: risk`（reset、7 天）；实盘 / 测试网 / 影子盘固定 strict
  且有测试（`tests/live/test_runner_halt.py`）；两种权益口径 10000 / 500；组合上限 3.0 / 1.5；
  实盘资金上限 500（范围 (0, 1000]）；`global_trial_budget` 400 → **符合**。
## 2026-11-24 11:35 UTC 会话级一致性检查（0 号调度第五部分第 1 条）

- 总账：`docs/research/ledger.yaml` 的 `trials_used = 65`；rounds 的 trials 之和
  12+6+8+6+4+9+6+6+4+4 = 65；`qualified_pool` 为空；服务器 `artifacts/reports/EXPERIMENTS.log`
  中 `kind=tuning` 记录数 65（另有 5 条 `kind` 为空的历史记录）→ **符合**。
- 候选与窗口：`candidates` 为空，三个窗口均 `unused`、`candidate: null` → **符合**。
- 服务器进程：无 `trade-runner@*` 实例；服务器 HEAD `5a34586` 与本地一致 → **符合**。
- 结论：全部符合。执行对象：一次性迁移 M3（`progress_migration.md` 中 M3 未开始）。

## 2026-11-24 11:40 UTC M3 前置复核（前一步 M2 的配置产物）

- 复核对象：`configs/research.yaml`、`configs/sizing_research.yaml`、`configs/live.yaml`、
  `src/trade/research/config.py`、`tests/live/test_runner_halt.py`（M2 提交 `35ee65a`）。
- 结果：`risk.halt_mode=reset` / `halt_reset_days=7`、`equity.signal=10000` / `equity.exec=500`、
  `portfolio.total_notional_cap=3.0` / `net_exposure_cap=1.5`、`live_capital_cap=500`（范围 (0,1000]）、
  `global_trial_budget=400`、实盘 / 测试网 / 影子盘 fixed strict 的测试存在 → **符合**。
- 数据前置（只读）：`data/klines_1h` 828 币、`data/klines_1d` 828 币、`data/funding` 121 MB、
  `data/universe_hist` 72 个条目；`data/klines_1h_flow`、`data/premium_1h`、`data/mark_1h` 尚不存在；
  `artifacts/exchange_info/` 有 1 个 exchangeInfo 快照（2026-10-31，942 个合约，含 TradFi 与
  SETTLING / PENDING_TRADING 状态）与 `rules.json`（缺 181 个币：150 个 TradFi 在快照中、
  31 个已下架加密合约完全不在快照中）→ 与本轮 M3 的目标一致。
- 发现的文档缺陷：`docs/design.md` §4 关键决策速查仍写“初始 100 USDT、总名义 ≤ 权益 × 2、
  净敞口 ≤ 权益 × 1”，与 `configs/sizing_research.yaml` / `configs/research.yaml` 的当前值
  （500 USDT 可执行口径、3 倍、1.5 倍）不一致 → 按第五部分第 5 条 a 款以配置为准修正文档，
  不涉及代码与判定。

- 待办：缺陷 D1~D11 尚未登记，按迁移 M4（D1~D7）与 M6（D8~D11）执行时登记为 OPEN。

## 2026-11-24 13:15 UTC M4 修复完成（D1~D7、D12、D13 → CLOSED）

复核对象与提交：`src/trade/research/{engine,rolling,pipeline,portfolio,view,data,config}.py`、
`src/trade/strategies/{ensemble,registry}.py`、`scripts/portfolio_backtest.py`、
`configs/research.yaml`、`configs/strategies/round_R5.yaml`、`tests/research/*`（`9c82edc`、`a2bafb4`）。
方法：读代码逐条核对 1 号第五部分 B 第 1~14 条与第七部分第 5 条；本地跑 §8 质量命令与
`tests/research`（36 passed）；服务器 `HEAD = a2bafb4` 与本地一致；最小规模运行见 progress_migration.md。

| 编号 | 状态 | 修复 | 复现测试 |
| :--- | :--- | :--- | :--- |
| D1 | CLOSED | 拼接曲线改为连续路径：`Engine.run_path(segments)` 从第 1 折起点以初始权益、空仓、空风控状态运行，折边界换参数；`rolling.concatenated_returns` 与 `selected_trades` 删除 | `tests/research/test_credibility.py::test_e_训练期风控状态不带入验证路径` |
| D2 | CLOSED | `round_R5.yaml` 改为 `component_grids`，`pipeline.component_params_by_fold` 逐折用折前数据选成分参数；`Ensemble.build` 缺成分时直接报错 | `tests/research/test_credibility.py::test_e_训练期风控状态不带入验证路径`（路径换参数）+ `test_records.py::test_网格评估写出记录与逐组文件并可断点续跑` |
| D3 | CLOSED | 引擎改为“决策在 t、成交在 `open_time = t` 的 K 线开盘”，仓位权益用截至 `t − 1h` 收盘的盯市值；权益曲线按每根 K 线收盘记录 | `test_credibility.py::test_h_口径回归测试`（h1）、`test_f_独立参考实现逐值一致`、`test_engine.py::test_决策时刻所在K线开盘成交且含成本` |
| D4 | CLOSED | 止损成交价改为“止损价 + `stop_slip_mult` 倍滑点（max(1bp, 平方根冲击)） + 半价差” | `test_h_口径回归测试`（h2，小币冲击 > 1bp） |
| D5 | CLOSED | 资金费缺失不再静默为 0：按该币过去 7 天平均估算并计数，没有近期结算时按 U(R) 中位数并计数；开仓前要求过去 7 天有结算记录 | `test_h_口径回归测试`（h3）、`test_view.py::test_资金费均值按已结算序列` |
| D6 | CLOSED | 止损触发改用 `mark_1h` 的 low/high，成交仍用成交价 K 线；缺标记价格时退回并计数 `mark_fallback` | `test_h_口径回归测试`（h4） |
| D7 | CLOSED | 取不到平仓价 / 盯市价 / 持仓币没有任何 K 线时抛出带币种与时间的异常 | `test_h_口径回归测试`（h1~h4 依赖该异常路径）+ 代码审查 |
| D12 | CLOSED | 试验记录含 `code_hash`/`data_hash`/`config_hash`/`equity_basis`，另有 `kind=exec`；逐组原子落盘与断点续跑；`scripts/portfolio_backtest.py` 不再写 ledger | `test_records.py` 三个测试 |
| D13 | CLOSED | 两种权益口径由同一代码只换初始权益；可执行口径重跑拼接路径并输出 `dropped_min_notional`/`attempted`，i 款三项进入 `checks` | `test_credibility.py::test_g_两种权益口径的丢弃数`、`test_records.py::test_网格评估写出记录与逐组文件并可断点续跑` |

处理方式：全部按 0 号调度第六部分 M4 执行；改动同步 `docs/13`（只写规格）。测试改动说明：
- `tests/research/test_engine.py::test_决策时刻所在K线开盘成交且含成本`：原断言固定了“晚 1 小时成交”的旧口径（D3），按 1 号 B 第 4 条改为 `entry_ts == start`；止损用例同步替换标记价格数据（D6 后触发基准改为 `mark_1h`）。
- `tests/research/test_view.py::test_资金费均值按已结算序列`：fixture 默认给所有币资金费序列（D5 后没有序列的币不能开仓），断言不变，改用 `funding_missing=("BBBUSDT",)` 指定缺数据币。
- `tests/research/helpers.py`：新增标记价格数据、默认资金费序列、`history_days` 预热期与日线起始时间参数（fixture 扩展，不改任何断言）。

## 2026-11-24 13:12 UTC M4 最小规模运行（服务器）

- 命令：`sudo systemd-run --scope -p MemoryMax=2600M nice -n 10 ~/trade-venv/bin/python scripts/portfolio_backtest.py --grid configs/strategies/round_R4.yaml --smoke-months 3`（研究窗口最后 3 个月 2026-05-01 ~ 2026-07-31，1 个方向 × 1 组参数）。
- 信号口径 10,000 USDT：交易 448、应开仓 448、最小下单额丢弃 0、年化夏普 -2.157、最大回撤 11.5%、多头毛收益 -1436.15、空头毛收益 +673.96。
- 可执行口径 500 USDT：交易 445、应开仓 448、最小下单额丢弃 3（0.67%）、年化夏普 -1.777、最大回撤 11.0%。
- 计数：`funding_estimated 28`、`funding_missing 0`、`mark_fallback 128`。记录字段核对：`kind=tuning`（`equity_basis 10000`）与 `kind=exec`（`equity_basis 500`）两种记录都写出。
- 结果文件：服务器 `artifacts/reports/smoke/20261124T131257Z.json`；未写入 `EXPERIMENTS.log`（最小规模运行不计试验次数）。
- 备注：首次最小运行出现 `funding_estimated 8754` 的误判，原因是部分币的结算周期在中位数 4h 与常见 8h 之间切换；改为按相邻结算间隔 99 分位判定后降至 28（提交 `a2bafb4`）。


## 2026-11-24 12:05 UTC M4 缺陷登记（登记时 OPEN；已在 13:15 一节改为 CLOSED）

复核对象：`src/trade/research/rolling.py`（`182a877`）、`src/trade/research/engine.py`（`182a877`）、
`src/trade/strategies/ensemble.py` 与 `configs/strategies/round_R5.yaml`（`182a877`）、
`scripts/portfolio_backtest.py`（`35ee65a`）与 `src/trade/research/pipeline.py`（`182a877`）。
方法：读代码逐条核对 1 号第五部分 B 第 1~14 条与第三部分第 5 条；未运行网格、未读取最终检验窗口数据。

| 编号 | 状态 | 违反的规定 | 现状证据 | 影响 |
| :--- | :--- | :--- | :--- | :--- |
| D1 | OPEN | 1 号 B 第 10 条 b~e 款（拼接曲线必须是连续路径、训练期风控不带入验证折） | `rolling.concatenated_returns` 直接把每组参数全期运行的日收益按折切片后拼接（`rolling.py` 第 127~141 行），没有用折内选中参数在折起点以初始权益与空风控状态重跑 | 训练期发生的 R6/R8 停机、回撤与连续亏损会带入验证折；R6、R8 因训练期停机导致验证折无交易，R7、R9、R10 被折内停机置零；R1~R10 的拼接数字全部不可信 |
| D2 | OPEN | 1 号第八部分 R5（成分参数必须逐折只用该折之前的数据选出） | `configs/strategies/round_R5.yaml` 写死 R1 L14/K3、R2 D1/K3、R4 L3/K5（全研究期选参结果），`Ensemble` 直接读取 | R5 在早期各折用到了后期数据；R5 判定作废 |
| D3 | OPEN | 1 号 B 第 4 条（在 open_time = t 的 K 线开盘成交，仓位用 t 时刻已知权益） | `engine.py` 第 482 行 `execute_ts=ts + HOUR_MS`，第 273~280 行用下一根 K 线开盘价成交；`_mark(ts)`（第 416~424 行）与 `entry_equity=self._mark(ts)` 用的是 `bar_index(ts)` 的收盘价，即 t+1h 才已知的盯市值 | 全部成交晚 1 小时，仓位规模用未来收盘权益；所有轮次数字偏乐观或偏悲观不可知 |
| D4 | OPEN | 1 号 B 第 5 条（止损成交价需含半价差与平方根冲击） | `engine.py` 第 387 行 `stop_slip = 2.0 * slip_min_bp`，未加半价差与冲击项 | 止损成本被低估，高波动小币尤其明显 |
| D5 | OPEN | 1 号 B 第 1 条（资金费缺失不得静默按 0） | `engine.py` 第 372~374 行 `series is None: continue`，缺失即不计资金费 | 无资金费序列的币被当作零成本持仓；信号与收益都被高估 |
| D6 | OPEN | 1 号 B 第 5 条（止损按标记价格触发，与实盘 `workingType=MARK_PRICE` 一致） | `engine.py` 第 394~399 行用成交价 K 线的 `low/high` 判断触发 | 回测与实盘触发次数不一致；标记价格与成交价的基差被忽略 |
| D7 | OPEN | 1 号 B 第 5 条与红线 12（取不到平仓价必须抛异常，不得静默保留） | `engine.py` 第 328~336 行（`base is None` / `price <= 0` 直接 `return`）、第 420~423 行（盯市跳过无价持仓）、第 406~408 行（无 K 线时 `continue`） | 平仓失败被静默吞掉，持仓与权益曲线不一致 |
| D12 | OPEN | 1 号第三部分第 5 条与 B 第 13 条（试验记录含 code_hash / data_hash / config_hash / equity_basis，kind=exec；逐组落盘与断点续跑；服务器脚本不写 ledger） | `pipeline.py` 与 `scripts/portfolio_backtest.py` 只写日频夏普等字段；`portfolio_backtest._update_ledger`（第 56~130 行）在运行机器上直接改 `ledger.yaml`；没有 `groups/<参数键>.json` 与断点续跑 | 试验无法复核复现；Deflated Sharpe 的试验集合不完整；在服务器运行会污染总账 |
| D13 | OPEN | 1 号第二部分第 3 条两种权益口径与第七部分第 5 条 i 款 | `equity.signal` / `equity.exec` 在 `src/trade/research/` 与 `scripts/portfolio_backtest.py` 中均无读取（仅 `config.py` 校验），`Engine` 仍只用 `sizing.initial_equity` | 无法区分信号口径与 500 USDT 可执行口径；可执行性检查缺失 |

处理方式：按 0 号调度第六部分 M4 修复，每个缺陷先写复现测试（修复前失败、修复后通过），再改代码，
最后把状态改为 CLOSED 并写明修复提交与测试名。修复前不重跑任何轮次；R1~R10 的判定已在 M7 作废。


## 2026-11-24 15:05 UTC 会话级一致性检查（0 号调度第五部分第 1 条）

- 总账：`trials_used = 65`；rounds 的 trials 之和 12+6+8+6+4+9+6+6+4+4 = 65；`qualified_pool` 为空；
  服务器 `artifacts/reports/EXPERIMENTS.log` 中 `kind=tuning` 记录 65 条（`kind=exec` 0 条）→ **符合**。
- 候选与窗口：`candidates` 为空，三个窗口均 `unused`、`candidate: null` → **符合**。
- 服务器进程：无 `trade-runner@*` 实例；`trade-dashboard` 为 active，`trade-watchdog.service` 为 inactive（timer 负责调度）
  → 与 `ops` 全为空一致 → **符合**。
- 服务器 HEAD `a2bafb4` 与本地代码提交一致（本地多一个文档提交 `4dd8023`，代码相同）→ **符合**。
- 结论：全部符合。执行对象：一次性迁移 M5（`progress_migration.md` 中 M5 未开始）。

## 2026-11-24 15:10 UTC M5 前置复核（前一步 M4 的研究框架）

- 复核对象：`src/trade/research/{engine,rolling,pipeline,portfolio,view,data,config}.py`、
  `src/trade/strategies/*`、`scripts/portfolio_backtest.py`、`configs/research.yaml`、`configs/sizing_research.yaml`、
  `tests/research/*`（M4 提交 `9c82edc`、`a2bafb4`、`4dd8023`）。
- 方法：逐条对照 1 号第五部分 B 第 1~14 条与第三部分底线；本地运行 `tests/research`（改动前 36 项全通过）与 §8 命令；
  服务器 `HEAD = a2bafb4`；M4 最小规模运行结果见 `progress_migration.md`。
- 结果：数据层（含资金费缺失、标记价格）、执行口径（决策在 t、成交在 `open_time = t`）、拼接路径（`run_path`）、
  逐折选参、试验记录字段与断点续跑、两种权益口径与 i 款检查均符合；结果可信度测试 a~h 全部通过 → **符合**。
- 发现并在 M5 实现中修复的问题（新代码缺陷，登记后已关闭）：
  | 编号 | 状态 | 问题 | 修复与复现测试 |
  | :--- | :--- | :--- | :--- |
  | D14 | CLOSED | `Engine.run` 构造内部 `Segment` 时没有传入构造函数给的 `sizing_mode`，模式 B 的组仍按模式 A 开仓 | `run` 把 `self.sizing_mode` 写进 `Segment`；`test_h5_引擎按段使用模式B`（修复前实际 2396 USDT vs 期望 891） |
  | D15 | CLOSED | β 初始化版本在 BTC 日线不足 60 天时抛错，与 1 号“样本不足 30 天取 1.0 并计数”冲突 | 改为 BTC 日线存在但样本不足/方差为 0 时全部取 1.0 并计数；`tests/research/test_portfolio.py::test_β样本不足30天时取1并计数`（修复前抛异常） |

## 2026-11-24 15:30 UTC M5 完成（模式 B、β 中性、指标与达标项 j）

- 代码：`src/trade/common/sizing.py`（新配置字段与 `size_from_notional`）、`src/trade/research/{config,view,portfolio,engine,metrics,pipeline}.py`、
  `scripts/portfolio_backtest.py`；配置：`configs/sizing_research.yaml`、`configs/research.yaml`；文档：`docs/00` §2、`docs/13` §2/§3/§7。
- 测试：`tests/research/test_metrics.py`（新增）、`test_portfolio.py`（模式 B 手算、单仓上限、β 中性、β 样本不足）、
  `test_credibility.py::{test_h5_引擎按段使用模式B,test_h6_中性策略的β调整在引擎中生效}`、
  `test_config.py`（新配置项与范围校验）、`tests/common/test_sizing.py`（取整状态一致性）。
- 质量命令：`ruff format --check .`（369 files）、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`
  （56 files）、`mypy src/trade/dashboard`（8 files）通过；`pytest -q` = `1 failed, 826 passed, 3 skipped`
  （唯一失败为本机无 CUDA 的 `tests/models/test_dataset.py::test_pinned_cpu_placement`，准备阶段既有失败，未修改测试）；
  `tests/research` 50 项全通过；`scripts/check_docs_sections.py` 通过（20 条基线失效引用）。
- 结论：**符合**。最小规模运行（模式 A、B 各一次）结果见 `progress_migration.md`。

