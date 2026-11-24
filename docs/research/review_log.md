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

## 2026-11-24 12:05 UTC M4 缺陷登记（OPEN；状态在修复后改为 CLOSED）

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
