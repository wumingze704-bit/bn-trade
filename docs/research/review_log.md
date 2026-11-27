# 前置复核记录（只追加）

同一复核对象的最新提交哈希与上次相同时，只写一行“未变化，沿用某时间的结论”。

> 本文件是研究重置前的复核记录，文中对 `rounds/R1.md`~`R10.md` 与 `progress_research.md` 的引用对应的文件已在研究重置中删除
> （旧结果按 D1~D16 口径缺陷作废），全文与数字只在 git 历史中可查；重置记录见 `decisions.md` 与 `RESET_DONE.md`。

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

## 2026-11-24 16:10 UTC M6 缺陷登记（登记时 OPEN）

复核对象：`src/trade/live/killswitch.py`、`scripts/kill_switch.py`、`scripts/live_watchdog.py`、
`src/trade/live/runner.py`、`src/trade/live/credentials.py`、`deploy/*.service`（提交 `51e3af8`）。
方法：逐条对照 3 号第五部分第 3、5 条与第六部分第 6、10、12 条、第八部分 6 / 6b / 6c / 6d；
读代码与单元文件，未连接交易所、未读取密钥。

| 编号 | 状态 | 违反的规定 | 现状证据 | 影响 |
| :--- | :--- | :--- | :--- | :--- |
| D8 | OPEN | 3 号第五部分第 5 条（停机顺序：先停实例再动账户；平仓完成前止损单必须存在） | `killswitch.kill_switch` 第 2 步先撤普通挂单、第 3 步就撤全部条件单（止损），第 4 步才市价平仓；`scripts/kill_switch.py` 的 `_stop_unit` 在 `kill_switch()` 返回之后才调用且不确认已停止 | 停机过程中存在“有仓位无止损”的窗口；闭市时引擎仍可能在操作账户 |
| D9 | OPEN | 3 号第六部分第 10 条（有停机标志 / 主动停止不重启；24 小时内第二次超时只告警；sudoers 规则） | `scripts/live_watchdog.py` 不读停机标志、不检查实例是否被主动停止、不检查单元是否启用；`_RESTART_WINDOW_MS` 是滚动 1 小时（之后每小时重启一次）；`systemctl restart` 以普通用户执行、无 sudoers 规则 | 停机或主动停止的实例会被看门狗拉起；崩溃时可能陷入每小时重启循环 |
| D10 | OPEN | 3 号第五部分第 3 条与第六部分第 6 条（策略权益 = min(启动时钱包余额, 上限) + 启动以来盈亏；专用账户与密钥文件权限检查） | `runner._equity` 用 `account.total_wallet_balance + total_unrealized_profit`（账户里多出的资金稀释回撤阈值）；启动检查没有外部持仓 / 挂单、余额 1.5 倍上限与 `.env.live` / 私钥 600 权限检查 | 账户混入其他资金时 R5~R8 与仓位偏大；非专用账户或权限过宽时仍会启动 |
| D11 | OPEN | 3 号第六部分第 12 条（Restart=on-failure、直接调用 venv python、文件系统加固） | `deploy/trade-runner@.service` 与 `trade-runner-live@.service` 为 `Restart=always`；`trade-runner@.service` 与 `trade-dashboard.service` 用 `/bin/bash -lc`；缺少 ProtectSystem / ProtectHome / ReadWritePaths | 主动停止的实例被 systemd 拉起；登录 shell 与无加固扩大进程权限 |

处理方式：按 3 号底线逐项修复，每个缺陷先写能复现它的测试；修复后把状态改为 CLOSED 并写明提交与测试名。



## 2026-11-24 15:55 UTC M6 修复完成（D8~D11 → CLOSED；新增 D16~D18）

复核对象与提交：`src/trade/live/{killswitch,runner,feed,candidate,config,credentials,store,execution}.py`、
`src/trade/live/exchange/client.py`、`src/trade/dashboard/{app,config}.py`、`scripts/{kill_switch,live_runner,live_watchdog}.py`、
`configs/{live,dashboard}.yaml`、`deploy/*`（`66b8c70`、`982df27`；中间提交 `f610371`、`76f1d52`、`bd10d55`、`c1bea65`、`938c6db`、`e737be9` 属同一修复过程中的迭代）。
方法：修复前在 `51e3af8` 的 git worktree 中运行新测试，确认 17 项失败（killswitch 顺序/签名、watchdog 全部规则、deploy 单元加固）与 `check_secret_permissions` 导入失败；修复后本地
`ruff format --check .`、`ruff check .`、`mypy --strict`、`mypy dashboard` 通过，`pytest -q` = `1 failed, 861 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有测试）。

| 编号 | 状态 | 修复 | 复现测试 |
| :--- | :--- | :--- | :--- |
| D8 | CLOSED | `killswitch.kill_switch` 顺序改为 写停机标志 → 停实例并确认 → 撤普通挂单（保留止损）→ 平仓复核到零 → 撤剩余条件单 → 删心跳；实例未确认停止时不操作账户；剩余持仓保留止损并报错。`scripts/kill_switch.py` 停止并 `is-active` 确认。 | `tests/live/test_killswitch.py::{test_先停实例再动账户且平仓完成前止损单仍在, test_实例未确认停止时不动账户, test_停机后删除心跳文件}` |
| D9 | CLOSED | 看门狗读停机标志 / 单元启用 / 主动停止（Result=success）；24 小时冷却（状态文件读回修复）；只经 sudoers 白名单脚本重启 `trade-runner*`；实例名从标准输入读，规则无通配符；引擎 SIGTERM 正常退出删除心跳。 | `tests/live/test_watchdog.py`（8 项）；服务器实测：重启一次、冷却内只告警、停机标志只告警 |
| D10 | CLOSED | `runner._equity` = `min(启动余额, 上限) + 启动以来盈亏 + 未实现`（`equity_baseline`）；live 启动检查专用账户（外部持仓 / 普通挂单 / 余额 1.5 倍）与 `env_file` / 私钥 600 权限；影子盘权益按初始权益 + 累计已实现（含开仓费） + 未实现。 | `tests/live/test_runner_equity.py`（13 项） |
| D11 | CLOSED | 交易与看板单元 `Restart=on-failure`、直接调用 venv python、`NoNewPrivileges/PrivateTmp/ProtectSystem=strict/ProtectHome=read-only/ReadWritePaths`；看门狗单元 sudo 例外用 `ProtectSystem=full`；看板 PIN 从 `control.pin_file` 读取；deploy 文件改 LF。 | `tests/live/test_deploy_units.py`（6 项） |
| D16 | CLOSED | 影子盘权益原先把“含未实现的 equity 快照”再叠加未实现，重复计算；改为 `initial_equity + 累计已实现 + 未实现`，开仓手续费计入已实现。 | `tests/live/test_runner_equity.py::test_影子盘权益不重复计算未实现盈亏` |
| D17 | CLOSED | `Store.update_risk_state` 只 UPDATE 已存在行，首日没有 `risk_state` 行时已实现盈亏被静默丢弃；改为 upsert（未给出的列取默认值）。 | `tests/live/test_runner_equity.py::test_影子盘权益不重复计算未实现盈亏`（依赖当日没有行时写入 realized_pnl） |
| D18 | CLOSED | 影子盘止损失败地用 `last_index_before(now_ms)`，会把当前未收盘 K 线当触发依据；改为 `last_index_before(决策整点)` 并使用 `mark_1h` 的 low/high。 | `tests/live/test_runner_alignment.py::test_影子盘止损只用已收盘K线` |

处理方式：全部按 0 号调度第六部分 M6 与 3 号第五、六部分修复；改动同步 `docs/07`（§2、§4、§5、§8、§10、§11、§12、§13、§14）、`docs/02`（进程守护与看门狗权限）、
`docs/09`（新增两个 REST 接口）、`docs/10`（PIN 文件）。测试改动说明：
- `tests/live/test_killswitch.py`：既有用例改为传入 `stop_runner`（保持原断言），并新增顺序 / 拒绝 / 心跳 / 保留止损用例。
- 服务器实测中的临时实例 `trade-runner@SMOKE-shadow`、override、心跳、`watchdog.json` 与停机标志均已清理；服务器无 `trade-runner@*` 实例。


## 2026-11-24 16:40 UTC M7 完成（旧判定作废与重跑准备）

- `docs/research/ledger.yaml`：R1~R10 均 `status: pending`、`trials: 0`、`result: null`，原判定移入 `void`（含 status / trials / result / reason）；
  `trials_used = 65` 未清零、`global_trial_budget = 400`；`qualified_pool` / `candidates` 为空 → 符合 M7 第 1 条。
- 一致性：Σ trials（0）+ Σ void.trials（65）+ 冻结仓位试验（0）= 65 = `trials_used`；服务器 `EXPERIMENTS.log` 的 `kind=tuning` 仍为 65 条（本次未新增试验）→ 符合。
- `docs/research/rounds/R1.md` ~ `R10.md`：顶部已加“首次判定已作废（迁移 M1~M7）”，文末新增“重跑（迁移后）”一节（当前未运行）；原内容保留 → 符合 M7 第 2 条。
- 网格：`configs/strategies/round_R1.yaml` ~ `round_R10.yaml` 的每组参数乘 `sizing_mode ∈ {fixed_risk, vol_target}`，组数 24/12/16/12/8/18/12/12/8/8（130 组）；未改其他参数、方向、规则与选币范围 → 符合 M7 第 3 条。
- R5 成分选参：`component_params_by_fold` 读取成分网格时去掉 `sizing_mode` 并去重；新增 `tests/research/test_component_params.py`，`tests/research` 51 项通过。
- 质量命令：`ruff format --check .`（375 files）、`ruff check .`、`mypy --strict`（56 files）、`mypy dashboard`（8 files）通过；`pytest -q` = `1 failed, 862 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有测试）。
- 结论：M7 全部符合；下一步按 R3 → R1 → R2 → R4 → R5 → R6~R10 重跑。

## 2026-11-24 16:25 UTC 会话级一致性检查（0 号调度第五部分第 1 条）

- 总账：`docs/research/ledger.yaml` 的 `trials_used = 65`；Σ rounds[].trials = 0；Σ rounds[].void.trials =
  12+6+8+6+4+9+6+6+4+4 = 65；冻结仓位选择的试验 0（`qualified_pool` 为空）→ 0 + 65 + 0 = 65 = `trials_used` **符合**。
- 服务器 `artifacts/reports/EXPERIMENTS.log`：70 条记录，`kind=tuning` 65 条（另有 5 条历史记录无 `kind` 字段）；
  按轮次 R1 12、R2 6、R3 8、R4 6、R5 4、R6 9、R7 6、R8 6、R9 4、R10 4 → 与 `void.trials` 逐轮一致 **符合**。
- 候选与窗口：`candidates` 为空，三个窗口均 `unused`、`candidate: null` → 不存在一个窗口对应两个候选 **符合**。
- 服务器进程：无 `trade-runner@*` 实例；`trade-dashboard` 与 `trade-watchdog.timer` 为 active；`ops` 全为空 **符合**。
- 状态判断：SETUP_DONE.md 第一行“结论：DONE”；`progress_migration.md` 中 M1~M7 全部完成；`review_log.md` 无 OPEN 缺陷
  → 按 0 号调度第四部分第 8 条 a 款执行 `1_自动研究循环.txt`（R1~R10 待重跑）。
- 结论：全部符合。

## 2026-11-24 16:35 UTC 执行 1 号前的前置复核（0 号调度第五部分第 2 条）

- 复核对象与最新提交哈希：`src/trade/research/`（`5c3b674`，仅 M7 的 R5 成分选参改动）、
  `src/trade/strategies/`（`9c82edc`）、`scripts/portfolio_backtest.py`（`5381f16` 之后无改动）、
  `configs/research.yaml` / `configs/sizing_research.yaml`（`5381f16`）、`tests/research/`（`5c3b674`）、
  `docs/research/ledger.yaml`（`5c3b674`）。逐项对照 1 号第五部分 B 第 1~14 条与第三部分底线：
  - B1 数据层：`trade.research.data`/`view` 读 klines_1h、klines_1h_flow、premium_1h、mark_1h、funding；
    资金费缺失按该币过去 7 天均值或 U(R) 中位数计并计数，无近期结算的币不允许开仓（D5 修复）**符合**。
  - B4 执行口径：决策在 t、成交在 `open_time = t` 的 K 线开盘，仓位权益截至 `t − 1h` 收盘（D3 修复）**符合**。
  - B5 止损：`mark_1h` 触发、成交价 = 止损价 + `stop_slip_mult` × 滑点 + 半价差，取价失败抛异常（D4/D6/D7 修复）**符合**。
  - B10 拼接：`Engine.run_path(segments)` 连续路径、折边界换参数、训练期风控不带入（D1 修复）**符合**。
  - B11 守卫：`stage.enforce_research` 只允许研究区间 **符合**；B13 逐组落盘与断点续跑、脚本不写 ledger **符合**。
  - B14 结果可信度测试：`tests/research/test_credibility.py` 的 a~h 与 h5、h6 全部存在并通过
    （`pytest tests/research tests/strategies -q` → 67 passed）。
  - 已完成轮次的数字核对（作废前）：`artifacts/reports/R3/grid.json` 的 `full_period_daily_sharpe`
    0.022298796969279534 与 `rounds/R3.md` 的 0.0223、`full_trades` 150 与 150 笔一致；其余轮次在 2026-11-24
    各次复核中已核对，本次未发现新的不一致。
  - 判定实现与 1 号第七部分第 5 条一致：`pipeline._checks`（a~h、j）与 `_executable_checks`（i）逐项存在 **符合**。
  - 服务器 HEAD `ff117a4` 与本地 `develop` 一致；下一轮运行前重新核对。
- 复核中发现但尚未影响任何结果的事项（登记为观察项，待 R11/R12 运行前处理）：
  `pipeline._checks` 的 delay1 条件是固定“delay1 年化夏普 < base”，而 1 号第八部分 R11/R12 的登记口径是
  “delay1 ≤ base + 0.10”。当前不影响 R1~R10（不适用该例外），R11/R12 开跑前需按登记口径改为可配置。
- 结论：**符合**（观察项 1 项，不影响本轮执行）。

## 2026-11-24 17:20 UTC 缺陷 D19、D20 登记与修复（R3 重跑中发现）

复核对象：`src/trade/common/risk_rules.py`（`5381f16`）、`src/trade/research/pipeline.py`（`5c3b674`）。
发现方式：R3 重跑（服务器 HEAD `ee6f1e5`）的拼接路径在第 1 折之后完全没有交易，
`artifacts/reports/R3/grid.json` 的 `selected_metrics` 显示 `trades=80`、`annualized_sharpe=-0.7998`，
分折 `fold1 net=-0.1745`、`fold2~5 net=0.0000 trades=0`；用同一参数、同一折单独复算拼接路径得到
`risk events 227`，其中 `R8:flatten_all` 每 7 天一次（`1649286000000` = 2022-04-07 起连续出现）。

| 编号 | 状态 | 违反的规定 | 现状证据 | 影响 |
| :--- | :--- | :--- | :--- | :--- |
| D19 | CLOSED | 1 号第二部分第 3 条（`halt_mode: reset` 停 7 个自然日后“以当时权益继续”） | `risk_rules.risk_step` 在停机到期时执行 `peak_equity = max(peak_equity, equity)`，峰值仍为停机前高点 → 回撤始终 ≥ `max_drawdown_halt` → 每次到期立刻再触发 R8，每 7 天循环一次，路径永久停摆 | 任何在路径内触发 R8 的轮次，其后各折全部无交易、收益记 0，判定与试验登记全部失真 |
| D20 | CLOSED | 1 号第三部分第 5 条与 B 第 13 条（`code_hash` 不一致必须整轮重跑） | `pipeline.compute_hashes` 只哈希 `src/trade/research`、`src/trade/strategies` 与 `scripts/portfolio_backtest.py`，不含共享风控 / 仓位模块 `src/trade/common`；修 D19 后哈希不变，断点续跑会静默复用缺陷结果 | 修复后仍复用旧组结果，等于把修复作废；同口径风险在后续任何 `trade.common` 改动时都会重现 |

修复（`src/trade/common/risk_rules.py`、`src/trade/research/pipeline.py`）：
- D19：reset 模式到期恢复时 `state.peak_equity = equity`（按恢复时刻权益重设回撤基准），strict 模式仍用 `_NEVER_MS` 不再恢复。
- D20：`code_hash` 覆盖 `src/trade/common/**/*.py`；`docs/13` §4（reset 停机语义）与 §8（试验记录字段）同步。

复现测试（修复前失败、修复后通过）：
- `tests/common/test_risk_rules.py::test_reset模式停机到期后按恢复时权益继续`（修复前在到期时刻返回 `flatten`、`halted_until_ms` 再次被推后 7 天）；
- `tests/common/test_risk_rules.py::test_strict模式停机后不再恢复`；
- `tests/research/test_records.py::test_代码哈希覆盖共享风控与仓位模块`（修复前两个 `code_hash` 相同）。

质量命令：`ruff format --check .`（376 files）、`ruff check .`、`mypy --strict`（56 files）、`mypy dashboard`（8 files）通过；
`pytest -q` = `1 failed, 865 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有测试）。未修改任何既有测试断言。

处理：R3 的第一次重跑结果与判定作废（试验次数照常计入），修复后重跑整轮（1 号第三部分第 8 条）；
R3 第一次重跑的原始报告保留为 `artifacts/reports/R3/grid_before_D19_fix.json`（服务器）。

对实盘引擎的影响：`src/trade/live/` 固定 `strict`（`halted_until_ms = _NEVER_MS`），不会进入 D19 修改的
“到期恢复”分支，行为不变；`engine.md` 的文件清单（`trade.live` / `trade.dashboard` / `deploy`）不包含
`trade.common.risk_rules`，无需更新。观察项：建议 3 号下次复核时把 `trade.common.risk_rules`、`trade.common.sizing`
纳入 engine.md 的 SHA256 清单（它们是实盘与影子盘共用的执行口径）。


## 2026-11-24 23:08 UTC 会话级一致性检查与执行 1 号前的前置复核（R1 运行中）

- 会话级总账一致性（0 号调度第五部分第 1 条）：`trials_used = 97`；Σ rounds[].trials = 32（R3 的两次重跑）+ Σ rounds[].void.trials = 65 + 冻结仓位选择的试验 0 = 97 **符合**。
  服务器 `artifacts/reports/EXPERIMENTS.log` 的 `"kind": "tuning"` 共 97 条（R1 12、R2 6、R3 40、R4 6、R5 4、R6 9、R7 6、R8 6、R9 4、R10 4；
  R3 的 40 = 作废首轮 8 + 第 1 次重跑 16 + D19 修复后重跑 16），另有 2 条 R3 `kind=exec` 与 5 条无 `round` / `kind` 的历史记录 **符合**。
- 复核对象与最新提交哈希：`src/trade/research/`（`64a0dde`）、`src/trade/strategies/`（`9c82edc`）、`scripts/portfolio_backtest.py`（`5381f16`）、
  `configs/research.yaml` 与 `configs/sizing_research.yaml`（`5381f16`）、`src/trade/common/risk_rules.py`（`64a0dde`）、`tests/research/`（`64a0dde`）、
  `docs/research/ledger.yaml`（`58898a9`）。逐项对照 1 号第五部分 B 第 1~14 条与第三部分底线：
  - B1 数据层：`trade.research.data` / `view` 读 `klines_1h`、`klines_1h_flow`、`premium_1h`、`mark_1h`、`funding`；资金费缺失按该币过去 7 天均值或
    U(R) 中位数估算并计数，完全没有结算记录的币不允许开仓（D5）**符合**。
  - B2 策略接口与 view 防未来函数：`tests/research` 的 view 系列测试通过 **符合**。B3 组合层：模式 A / B、β 中性、总名义 3 倍、净敞口 1.5 倍、
    保证金 0.95、单币成交额与最小下单额约束 **符合**。
  - B4 执行口径：决策在 t、成交在 `open_time = t` 的 K 线开盘、仓位权益截至 `t − 1h` 收盘（D3 修复）**符合**。
  - B5 止损：`mark_1h` 触发、成交价含半价差与平方根冲击、取价失败抛异常（D4 / D6 / D7 修复）**符合**；B6 下架强平 **符合**。
  - B7 风控复用 `trade.common.risk_rules`，reset 模式到期重设回撤峰值（D19 修复）**符合**；B8 七种情景 **符合**。
  - B9 指标含实现 β、alpha 夏普与 Deflated Sharpe（`read_trial_sharpes` 读全部 `kind=tuning`）**符合**。
  - B10 拼接曲线为连续路径（`Engine.run_path`）、折边界换参数、训练期风控不带入（D1）**符合**；B11 守卫 `stage.enforce_research` 只允许研究区间 **符合**。
  - B12 单元测试：`pytest tests/research tests/strategies -q` → 68 passed **符合**。
  - B13 逐组落盘与断点续跑：`ensure_groups` 在全部组的训练曲线与自身拼接曲线跑完后逐组写 `groups/<参数键哈希>.json` 并追加 `EXPERIMENTS.log`
    （因此运行中 `groups/` 目录尚不存在属正常），脚本不写 `ledger.yaml` **符合**。
  - B14 结果可信度测试 a~h（含 h5 / h6）：`tests/research/test_credibility.py` 全部存在并通过 **符合**。
- 判定实现与 1 号第七部分第 5 条：`pipeline._checks` 覆盖 a~h、j，`_executable_checks` 覆盖 i 款三项（丢弃比例 ≤ 20%、净收益 > 0、夏普 ≥ 70%）
  **符合**；发现 g 款的 R11 / R12 登记例外未实现，见下方 D21。
- 已完成轮次的数字核对：`artifacts/reports/R3/grid.json` 的 `selected_metrics` 与 `rounds/R3.md` 第 2 次重跑一节逐项一致
  （年化夏普 0.0702713、最大回撤 0.598931、交易 1511、成本 816.664、资金费 1484.003、多头毛 2736.925、空头毛 -2172.130、
  DSR 0.053547、最差月 -0.158515、亏损月 32、换手 161.723、bootstrap 下限 -133.386）；`exec_metrics` 与文档一致
  （夏普 0.0879345、净 -13.346%、交易 1414、应开仓 3298、因最小下单额丢弃 1506 = 45.7%）。
- 服务器代码：`git rev-parse HEAD` = `93d1b87`（R1 正在该版本运行）；本地 `develop` = `58898a9`，
  `git diff --stat 93d1b87..HEAD` 只有 `docs/research/` 三个文件（ledger / progress_research / review_log）→ 代码与运行版本一致 **符合**。
- R1 首轮（已作废）结果留档：服务器 `artifacts/reports/R1/grid.json` 与 `selected_daily_returns.csv` 复制为
  `grid_first_run_void.json`、`selected_daily_returns_first_run_void.csv`，避免重跑报告覆盖后作废判定的原始证据丢失（未删除任何文件）。

### 缺陷登记与修复

| 编号 | 状态 | 违反的规定 | 现状证据 | 影响 |
| :--- | :--- | :--- | :--- | :--- |
| D21 | CLOSED | 1 号第八部分 R11、R12 的登记口径“delay1 年化夏普 ≤ base + 0.10”（对这两轮替代 g 款的“低于 base”） | `pipeline._checks` 的 `delay1_lt_base` 固定为 `delay1 < base`，网格与配置文件没有可登记该例外的字段 | R11 / R12 若按登记口径本应通过 g 款，会被误判为不达标；不影响 R1~R10（未登记例外，仍为严格口径） |

修复（`src/trade/research/pipeline.py`、`tests/research/test_metrics.py`、`docs/13` §7、§9）：
- 网格可选字段 `delay1_max_excess`（非负，默认 0）：0 时保持 g 款严格口径 `delay1 < base`；正数时 `delay1 ≤ base + delay1_max_excess`；
  非法值直接抛 `ValueError`（红线 12）；`grid.json` 记录该字段，便于复核判定依据。
- 复现（修复前失败、修复后通过）：在 `58898a9` 的临时 worktree 上运行新测试文件 → `ImportError: cannot import name '_delay1_max_excess'`
  （代码没有该口径）；修复后 `tests/research/test_metrics.py` 7 passed。
- 新增测试：`test_delay1条件在容忍度为0时要求严格低于base`、`test_delay1条件按网格容忍度判定`、`test_网格delay1容忍度必须是非负数`。
- 质量命令：`ruff format --check .`（376 files）、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`（56 files）、
  `mypy src/trade/dashboard`（8 files）通过；`pytest -q` = `1 failed, 868 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有测试，未修改任何既有测试）。
- 与 R1 的关系：R1 在服务器 `93d1b87` 上运行，报告的 `checks` 由该版本计算；R1 未登记 delay1 例外，D21 不改变其判定口径。
  R11、R12 开跑前须在其网格写入 `delay1_max_excess: 0.10`（已记入 `progress_research.md` 的下一步）。

### 观察项

1. R1 于 2026-11-24 21:34 UTC 在服务器 tmux 会话 `r1` 启动（`sudo -n systemd-run --scope -p MemoryMax=2000M nice -n 10`），24 组 = 48 次全期运行；
   逐组落盘在全部运行结束后才写入，运行期间不要重复启动（重复启动会重复登记试验）。
2. R3 的两条 `kind=exec` 记录属同一轮第 1 次与第 2 次重跑；`equity_basis` 均为 500，不计入 Deflated Sharpe，符合 1 号第三部分第 5 条。

- 结论：**符合**（新增缺陷 D21 已修复并关闭；观察项 2 项）。


## 2026-11-25 00:10 UTC 会话级一致性检查与执行 1 号前的前置复核（R1 仍在运行）

- 会话前检查（0 号调度第二部分）：当前目录是 git 仓库（origin = `https://github.com/wumingze704-bit/bn-trade.git`），分支 `develop`，
  `git pull --rebase origin develop` → Already up to date（`cc3fd09`，与 `origin/develop` 同步）；`ssh tokyo` 可连通；服务器无 `trade-runner@*` 实例。
- 会话级总账一致性（第五部分第 1 条）：`trials_used = 97`；Σ rounds[].trials = 32（R3 修复后重跑）+ Σ rounds[].void.trials = 65 +
  冻结仓位选择的试验 0 = 97 **符合**；服务器 `artifacts/reports/EXPERIMENTS.log` 的 `"kind": "tuning"` 97 条 **符合**。
- 状态判断（第四部分）：`SETUP_DONE.md` 第一行“结论：DONE”；`progress_migration.md` 中 M1~M7 全部完成；`review_log.md` 无 OPEN 缺陷（D21 已 CLOSED）；
  `candidates` / `qualified_pool` 为空；三个窗口均 `unused`；`engine.md` 为 `ENGINE_READY` → 按第 8 条 a 款执行 1 号（R1 正在运行，R2、R4~R12 待跑）。
- 复核对象与最新提交哈希：`src/trade/research/`（`7a7de4f`）、`tests/research/`（`7a7de4f`）、`src/trade/strategies/`（`9c82edc`）、
  `scripts/portfolio_backtest.py`（`5381f16`）、`configs/research.yaml` 与 `configs/sizing_research.yaml`（`5381f16`）、
  `src/trade/common/risk_rules.py`（`64a0dde`）、`src/trade/common/sizing.py`（`5381f16`）、`docs/research/ledger.yaml`（`0d602df`）、
  `docs/research/rounds/R3.md`（`93d1b87`）。
  - 与 2026-11-24 23:08 的复核相比，只有 `src/trade/research/` 与 `tests/research/` 变化（`7a7de4f`，D21 的 `delay1_max_excess` 可选字段）。
    逐项复核该改动：默认 0 时仍要求 `delay1 < base`；正数时 `delay1 ≤ base + 容忍度`；负数 / 非数值直接抛 `ValueError`（红线 12）；
    `grid.json` 记录该字段；3 项新测试通过。`git diff 93d1b87..HEAD` 显示 R1 正在运行的 `93d1b87` 与当前代码的差异只有该字段（默认值不改变判定）
    与文档，故 B1~B14 与判定实现的结论沿用 23:08 的“符合”。
  - 其余对象自 23:08 复核后未变化，沿用 2026-11-24 23:08 的复核结论（B1~B14 符合；`trade.common` 已计入 `code_hash`）。
  - `rounds/R3.md` 与服务器 `artifacts/reports/R3/grid.json`（21:28 UTC 写入，此后无改动）在 23:08 已逐项核对一致。
- §8 质量命令（本次复跑，代码 `7a7de4f`）：`ruff format --check .`（376 files）、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`
  （56 files）、`mypy src/trade/dashboard`（8 files）通过；`pytest -q tests/research tests/strategies` → 71 passed；
  `pytest -q` → `1 failed, 868 passed, 3 skipped`（唯一失败为本机无 CUDA 的既有 `tests/models/test_dataset.py::test_pinned_cpu_placement`，
  未修改任何测试）。
- R1 运行状态：服务器 tmux 会话 `r1`（`systemd-run --scope` 下 PID 924634），2026-11-24 21:34 UTC 启动；23:31 UTC 已运行 1h57m、RSS 1.05 GB、
  CPU 99.7%、服务器可用内存 1.99 GB；`artifacts/reports/R1/groups/` 尚未生成（逐组落盘在全部组跑完后写入）；本次未重复启动。
- 结论：复核**全部符合**，未发现新缺陷。本次会话不推进 R1 判定（运行中），只完成 R11 / R12 的 500 USDT 可执行性预检（记录见 `decisions.md`）。

## 2026-11-25 10:47 UTC 会话级一致性检查与执行 1 号前的前置复核（R2 运行中）

- 会话前检查（0 号调度第二部分）：当前目录是 git 仓库（origin = `https://github.com/wumingze704-bit/bn-trade.git`），分支 `develop`，
  本地 HEAD `54ec510`；`git pull --rebase origin develop` 因 GitHub 连接被重置（`Recv failure: Connection was reset`）失败，
  本地无未推送的新内容丢失风险，按第五部分第 5 条 d 款记为“无法核实远端是否领先”，继续本地工作；
  `ssh tokyo` 可连通；服务器无 `trade-runner@*` 实例（只有 `trade-dashboard` 与 `trade-watchdog.timer`）。
- 会话级总账一致性（第五部分第 1 条）：`trials_used = 121` = Σ rounds[].trials 56（R1 24 + R3 32）+
  Σ rounds[].void.trials 65 + 冻结仓位选择的试验 0 = 121 **符合**；服务器 `artifacts/reports/EXPERIMENTS.log` 的
  `kind=tuning` 121 条（全文 129 行，另 3 条 `kind=exec` 与 5 条无 `kind` 的历史记录）**符合**。
- 复核对象与最新提交哈希：`src/trade/research/`（`7a7de4f`）、`src/trade/strategies/`（`9c82edc`）、
  `scripts/portfolio_backtest.py`（`5381f16`）、`configs/research.yaml` 与 `configs/sizing_research.yaml`（`5381f16`）、
  `src/trade/common/risk_rules.py`（`64a0dde`）、`src/trade/common/sizing.py`（`5381f16`）、`tests/research/`（`7a7de4f`）。
  以上哈希与 2026-11-25 10:30 UTC 复核完全相同 → 未变化，沿用 2026-11-25 10:30 UTC 的复核结论（B1~B14 与判定实现全部符合）。
- `docs/research/ledger.yaml`（`c037ee8`，R1 判定与 `trials_used 121`）已在 10:30 UTC 复核中逐项核对原始输出；
  R2 由该次会话启动，运行于服务器 `c037ee8`（本地此后只多出 `54ec510` 一个 `docs/research/` 提交，无代码差异）。
- 运行状态：服务器 `~/trade` HEAD `c037ee8`；R2 于 2026-11-25 10:28 UTC 启动（tmux `r2`，`systemd-run --scope`，MemoryMax 2000M），
  本次检查时仍在运行（`ps` 有 3 个匹配进程），未重复启动。
- 结论：复核**全部符合**，无新缺陷；本次会话等待 R2 完成后写判定并启动下一轮。


- 会话前检查（0 号调度第二部分）：当前目录是 git 仓库（origin = `https://github.com/wumingze704-bit/bn-trade.git`），分支 `develop`，
  `git pull --rebase origin develop` → Already up to date（起点 `2fd2186`）；`ssh tokyo` 可连通；服务器无 `trade-runner@*` 实例（`candidates` 为空）。
- 会话级总账一致性（第五部分第 1 条）：`trials_used = 121` = Σ rounds[].trials 56（R1 24 + R3 32）+
  Σ rounds[].void.trials 65 + 冻结仓位选择的试验 0 **符合**；服务器 `artifacts/reports/EXPERIMENTS.log` 的 `"kind": "tuning"` 121 条 **符合**
  （R1 36 = 作废首轮 12 + 重跑 24；R3 40 = 作废首轮 8 + 第 1 次重跑 16 + 修复后重跑 16；其余轮次首轮合计 45）。
- 状态判断（第四部分）：`SETUP_DONE.md` 第一行“结论：DONE”；`progress_migration.md` 中 M1~M7 全部完成；`review_log.md` 无 OPEN 缺陷；
  `candidates` / `qualified_pool` 为空；三个窗口均 `unused`；`engine.md` 为 `ENGINE_READY` → 按第 8 条 a 款执行 1 号。
- 复核对象与最新提交哈希：`src/trade/research/`（`7a7de4f`）、`tests/research/`（`7a7de4f`）、`src/trade/strategies/`（`9c82edc`）、
  `scripts/portfolio_backtest.py`（`5381f16`）、`configs/research.yaml` 与 `configs/sizing_research.yaml`（`5381f16`）、
  `src/trade/common/risk_rules.py`（`64a0dde`）、`src/trade/common/sizing.py`（`5381f16`）、`docs/research/rounds/R1.md`（`c037ee8`）。
  - 代码对象（前六项）的最新提交与 2026-11-25 00:10 复核相同：未变化，沿用该次复核的 B1~B14“符合”结论。
  - `docs/research/rounds/R1.md` 为本会话新写内容，逐项对照服务器原始输出：

    `artifacts/reports/R1/grid.json`（`generated_at` 2026-11-25T04:21:30Z，`hashes.code_hash 9d4479fb…`、`config_hash 1d63621f…`、
    `data_hash bcb2dca3…`，`equity_basis {signal: 10000, exec: 500}`，`params` 24 组）**符合**：
    - `selected_metrics` 逐项一致：年化夏普 −0.530427、日频 −0.027764、净收益 −4159.476、最大回撤 0.529341、
      成本合计 1866.351、成本 / 毛 0.8065、资金费 −20.961、多头净 −6202.548、空头净 +2043.072、实现 β −0.047972、
      alpha 夏普 −0.474024、DSR 0.003344、bootstrap [−49.213, +19.030]、交易 4079、换手 373.270、亏损月 33、最差月 −0.103155、
      风险事件 11、强制平仓 3、资金费估算 6 / 缺失 0、标记价格回退 731、丢弃 351 / 应开仓 4100。
    - `fold_stats` 与文档分折表一致（训练夏普 0.4595 / −0.1528 / −0.1324 / −0.3122 / −0.2695，折内净收益
      −0.15671 / −0.00473 / −0.14355 / −0.31972 / +0.20360，多头毛 − 空头毛 5 折仅第 3 折为正）。
    - `scenarios` 七项与文档情景表一致；`exec_metrics` 一致（应开仓 4269、丢弃 605、因最小下单额 258、净 −0.44640、夏普 −0.602611）。
    - `checks` 14 项与文档“未达标项 / 达标项”逐条对应（`shuffle_le_03`、`delay1_lt_base`、`beta_abs_le_030`、`exec_drop_ratio_le_020` 为 True，其余 False）。
- §8 质量命令（本次复跑，代码 `7a7de4f`、文档 `c037ee8`）：`ruff format --check .`（376 files）、`ruff check .` 通过；
  `pytest -q tests/research tests/strategies` → 71 passed。`mypy --strict src/trade/common src/trade/backtest src/trade/live`
  与 `mypy src/trade/dashboard` 的代码对象与 00:10 复核相同，该次已通过，本次未重复运行。
- 运行环境：R2 启动时服务器 `~/trade` HEAD `c037ee8b5829369bcbefcf8d6700df948c162c29` 与本地一致（`git rev-parse HEAD` 核对）。
  启动方式上的一个操作记录：`systemd-run --scope` 会把工作目录重置为用户家目录（首次启动因此找不到相对路径脚本），
  已改为显式 `--working-directory=/home/ubuntu/trade` 后正常启动；不影响任何计算结果。
- 结论：复核**全部符合**，未发现新缺陷；R1 判定按原始输出写为 FAILED，R2 已启动。


## 2026-11-25 23:20 UTC R4 重跑结果的核对与观察项

- R4（12 组）2026-11-25 14:09 UTC 启动、16:47:59 UTC 完成（`grid.json` 的 `generated_at`），运行时长约 2 小时 38 分；
  服务器 HEAD `dffecab7`。在服务器上用同一 `compute_hashes` 文件清单复算得 `code_hash 66cdbd12…`、`config_hash 32372077…`，
  与 `grid.json` 完全一致；`EXPERIMENTS.log` 的 `kind=tuning` 由 133 增至 145（+12），另新增 1 条 `kind=exec`。
- `rounds/R4.md` 的“重跑（迁移后）”逐项与原始输出一致：拼接年化 −0.5193249、日频 −0.0271827、净 −3321.885、
  最大回撤 0.476977、交易 6307、换手 547.729、成本 2738.644、成本占毛收益 4.8985、资金费 +24.167、
  多头净 −4702.932、空头净 +1381.047、实现 β 0.017043、alpha −0.546984、DSR 0.003749、
  bootstrap [−30.529, +12.532]、风险事件 8、强平 2、资金费估算 33 / 缺失 0、标记价格回退 432、丢弃 778 / 应开仓 6341；
  分折选中参数、训练夏普与折内净收益、七项情景、12 组自身拼接夏普均与 `grid.json` 对应字段一致。
- 观察项（不登记为缺陷）：`pipeline._executable_checks` 的 `exec_sharpe_ge_70pct` 实现为
  `exec_sharpe >= 0.7 × signal_sharpe`；信号夏普为负时该不等式恒松（R4：−0.308 ≥ −0.363 → True）。
  实现与 i 款“年化夏普 ≥ 信号口径的 70%”的字面表述一致，本轮 i 款仍因净收益 −23.70% 不达标，判定不受影响；
  在报告中按字面含义解释该检查项，不自行改动口径（要改成“只对信号夏普为正时比较”属使用者决定）。
- 结论：核对全部一致，R4 判定 FAILED。


## 2026-11-25 23:40 UTC 缺陷 D22（R5 无法运行）登记与修复

复核对象：`src/trade/research/pipeline.py`（`2efa95b` 之后未改动的版本）、`src/trade/strategies/ensemble.py`（`9c82edc`）。

发现方式：按 M7 顺序启动 R5（`configs/strategies/round_R5.yaml`，8 组）后进程立即退出，`~/trade/artifacts/reports/R5.log`：

```text
File "src/trade/research/pipeline.py", line 557, in ensure_groups -> _run_tasks -> run_param
File "src/trade/strategies/registry.py", line 60, in build_strategy
File "src/trade/strategies/ensemble.py", line 99, in build
ValueError: R5 的成分参数必须逐折给出（1 号第八部分 R5）
```

| 编号 | 状态 | 违反的规定 | 现状证据 | 影响 |
| :--- | :--- | :--- | :--- | :--- |
| D22 | CLOSED | 1 号第八部分 R5（第 k 折的训练与验证都用该折的成分参数）与第五部分 B 第 10 条 a、c 款（训练曲线与自身拼接曲线都必须能跑） | `ensure_groups` 的两类任务（全期训练曲线、自身拼接曲线）都不传 `components`，`Ensemble.build` 因此必然抛 `ValueError`；`_worker_task` 与串行分支也不传成分参数，R5 的任何网格都无法运行 | 组合方向 R5 无法运行，重跑顺序在 R5 处中断；R6~R10 依赖 R5 之后继续 |

修复（`src/trade/research/pipeline.py`、`tests/research/test_ensemble_fold_components.py`、`docs/13` §6）：
- `ensure_groups` 新增 `components_by_fold`：给出时每组参数改为“每折一条训练曲线（该折之前的数据 + 该折成分参数）”
  加“一条按折切换成分参数的自身拼接曲线”；`GroupResult` 新增 `train_by_fold` / `train_trades_by_fold` 并写入逐组缓存；
  未给出时（R1~R4、R6~R12）保持原有两个任务的结构不变。
- 新增 `run_path_param`（一组参数按折切换成分参数的连续路径）、`select_choices`（组合方向按折训练曲线选参）；
  `_execute_task` 统一串行与多进程两种执行方式；`evaluate_grid` 先算成分参数再跑组合的逐组结果，避免重复计算。
- `docs/13` §6 补两条规格：组合方向训练曲线按折运行、自身拼接曲线在折边界切换成分参数。

复现测试（修复前失败、修复后通过）：`tests/research/test_ensemble_fold_components.py` 4 项
（`test_ensure_groups为组合方向按折传成分参数`、`test_select_choices按折成分参数选参`、
`test_run_path_param拒绝折数不一致`、`test_按折训练曲线可落盘复用`）。在 `2efa95b` 的临时 worktree 上运行该文件
→ `ImportError: cannot import name 'run_path_param'`（旧代码没有逐折成分参数能力）；修复后 `4 passed`。

质量命令：`ruff format --check .`（378 files）、`ruff check .`、`mypy --strict`（56 files）、`mypy dashboard`（8 files）通过；
`pytest -q tests/research tests/strategies` → 75 passed；`pytest -q` → `1 failed, 872 passed, 3 skipped`
（唯一失败为本机无 CUDA 的既有测试，未修改任何既有测试断言）。

影响范围：R1~R4 的判定基于旧的 `ensure_groups` 路径（未传 `components_by_fold`，行为与修复后完全一致，
不涉及任何成分参数），其结果与报告不受影响；R5 的第一次启动未产生 `kind=tuning` 记录（崩溃在写入之前），
`trials_used` 仍为 145。修复后重新启动 R5。


## 2026-11-27 UTC 会话级一致性检查与 M2/M4/M5 补做

- 状态判断（第四部分）：`RESET_DONE.md` 第一行“结论：DONE”；ledger 有 `void_history`（153 条）且 `trials_used: 0`、
  全部轮次 pending、`candidates` 与 `qualified_pool` 为空；服务器无 `trade-runner*` 单元、看板 active、看门狗 inactive；
  服务器 `~/trade` HEAD 与本地 `db893cb` 一致。
- 复核发现：`progress_migration.md` 把 M2/M4/M5 标为完成，但第三批决定（0 号调度第六部分）对应的子项在代码中缺失：
  `bootstrap` 无 `block_days`、无 `shuffle` 节、风险档位只有三档且数值与 `docs/00` §2 不同；`data_hash` 只含 `manifest.json`；
  研究框架仍用 `trade.backtest.sanity.deflated_sharpe`、逐日 bootstrap、单种子 shuffle、旧达标阈值；
  引擎没有逐仓强平、反向目标不反手、中性策略一侧为空时单边建仓、`max_positions` 先排多头再截断、
  `View.listing_age_days` 用加载窗口第一根；R1 未执行打分正负过滤、R3 叠加 1/波动权重、半价差无 tick 下限、
  168h 调仓落在周四。按第六部分把这些步骤改回“进行中”，先补做子项。
- 本轮已修复并加测试（缺陷编号沿用 0 号调度第六部分：D14~D17、D20~D22）：逐仓强平、反向反手、中性约束
  （一侧为空不建仓 / 交替截断 / 上限后重检最小下单额 / 每日 β 检查 / 一侧清空即平另一侧 / 上市天数取快照）、
  R1 打分符号过滤、R3 权重为 1、半价差 tick 下限、周一锚定、代码闭包与完整数据哈希、DSR 基准均值不为负、
  7 天移动块 bootstrap、四档可执行性与 g/i/l 款。复现测试：`tests/research/test_metrics.py`（13 项）、
  `tests/research/test_engine.py::test_止损触发按止损价加滑点成交并进入冷却`、
  `tests/research/test_credibility.py::test_h6_中性策略的β调整在引擎中生效`、
  `tests/research/test_records.py::test_代码哈希覆盖共享风控与仓位模块` 等。
- 因使用者第三批决定改变了规格而同步修改的既有测试（红线 11 的授权范围）：`test_config.py` 的四档数值与
  `target_vol`；`test_metrics.py` 的达标项名称与阈值；`test_credibility.py::test_d`（中性策略被过滤成单边后按
  docs/13 §3 第 4 条保持空仓）；`test_engine.py` 的止损用例把跌幅从 20% 改为 10%（20% 已越过 5x 逐仓强平价，
  按 D22 应先强平）。未删除测试、未放宽其他断言。
- 仍未完成（记为 M4/M5 的未完成部分）：D19（R5 成分按 R5 组自身的仓位模式选择）、X0~X4 与 R11、R12 的策略
  与网格、`View` 的溢价指数与主动买入访问器、冻结时的四档选档、服务器最小规模运行与 `engine.md` 复核。
- 质量命令：`ruff format --check .`、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`、
  `mypy src/trade/dashboard` 通过；`pytest -q` = `1 failed, 883 passed, 3 skipped`，唯一失败为本机无 CUDA 的既有测试。

