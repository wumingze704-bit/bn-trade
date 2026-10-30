# tasks.md —— 开发进度与待办

> 依据：docs/ 设计书（2026-10-30 定版：G2 统计显著性、规则化选币、数据管道提速、使用者确认的四项决定、看板方案）、docs/reports/\*.md、src/ 与 scripts/ 现有代码（2026-10-30 静态审阅）。
> 测试是否通过取自各完成报告，本次**未重新运行**测试。
> 状态：`[x]` 已完成且仍符合当前设计 `[~]` 部分完成（写明缺什么） `[ ]` 未开始 `[!]` 已发现问题，需修复 `[↺]` 已完成但因设计变更需要重做
> 执行规则：每次只做一个任务；完成后运行 AGENTS.md §8 质量命令，通过才标 `[x]`，然后停下等使用者确认。
> 编号说明：`T0.x`（点号）为审阅发现的修复与设计对齐任务；`T0-x`、`T1-x`……（横杠）为 08 各阶段的原有任务。

---

## 执行顺序（先看这里）

| 顺序 | 任务                                                            | 说明                                                                                      |
| :--- | :-------------------------------------------------------------- | :---------------------------------------------------------------------------------------- |
| 1    | T0.9                                                            | git，使用者自行处理                                                                       |
| 2    | T0.1 → T0.2 → T0.4 → T0.5 → T0.6 + T0.7 + T0.11 + T0.12 → T0.18 | 回测正确性修复，T0.1（封存集泄露）最优先；只用 fixture，不依赖真实数据                    |
| 3    | T0.10                                                           | 依赖拆分，服务器全量跑数据前完成，两端环境一致                                            |
| 4    | T0.14                                                           | 服务器验证已下架合约数据（只读、约半天），结论影响 T0.13                                  |
| 5    | T0.13 ∥ T1-8 ∥ T0.16 ∥ T0.17                                    | 选币代码、聚合器提速、bars 组装、按月质量报告，可并行开发                                 |
| 6    | T1-7                                                            | 服务器基准测试（T1-8 前后各测一次）                                                       |
| 7    | T1-3a → T1-3（快照）→ 使用者审阅 `reviewed: true`               | 快照决定下载清单，**必须在全量下载之前**                                                  |
| 8    | T0.15 → T0.19                                                   | 回测与数据集按月交易范围和数据段运行；`exit_sim` 标签（可与 7 并行开发，用 fixture 验证） |
| 9    | 清理服务器旧冒烟产物 → T1-9 分阶段全量下载                      | 前提：T1-8 等价测试通过、快照已审阅                                                       |
| 10   | 调参期数据到本地后：T3-9a、P2 数据集、T4-3 第 1 步              | 服务器同时继续处理测试段                                                                  |
| 11   | 全部数据完成 → G1                                               | 关键路径；期间可并行做 T5-1 通知模块、T6-1~T6-3 看板骨架与研究页                          |
| —    | T0.3                                                            | P5 实盘引擎完成后、G4 之前                                                                |
| —    | T0.8                                                            | T0.16（fundingRate 合入 bars）之后                                                        |

---

## 已完成工作中需要作废或重做的部分（设计变更导致）

| 已完成的内容                                                         | 位置                                                                                                                                                                               | 原因                                                         | 处理                                                              |
| :------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------- | :---------------------------------------------------------------- |
| 固定名单校验 `--validate`、盲测 `--draw-blind`、`--suggest` 及其测试 | `scripts/select_coins.py`、`src/trade/data/universe.py`（`CoinsConfig`、`validate_coins`、`draw_blind`、`eligible_blind_candidates`、`dump_coins`）、`tests/data/test_universe.py` | 设计改为规则化月度选币                                       | 删除并重写（T0.13）；`exchange.py` 的可交易性校验函数保留复用     |
| `configs/coins.yaml`、`configs/universe.yaml`（旧字段）              | `configs/`                                                                                                                                                                         | 同上                                                         | 按 03 §4.6 重写 `universe.yaml`，删除 `coins.yaml`（T0.13）       |
| `exchange.check_history` 的 train/holdout/blind 历史长度规则         | `src/trade/data/exchange.py` 约 160~195 行、`tests/data/test_exchange.py`                                                                                                          | 角色改为哈希分配，历史长度改为“上线 ≥ 60 天”                 | 改写为 03 §4.2 第 3 步（T0.13）                                   |
| 依赖 `coins.yaml` 的脚本                                             | `scripts/download.py`、`scripts/aggregate.py`、`scripts/r0_signals.py`、`scripts/backtest.py`、`scripts/build_dataset.py`、`tests/test_build_dataset.py`                           | 币种改为来自月度快照                                         | T0.13 / T0.15 / T1-9 中改为读快照                                 |
| 两遍聚合脚本，且**首日使用当日 p99**（含当日未来信息，违反 03 §2.2） | `scripts/aggregate.py` 约 65~70 行                                                                                                                                                 | 设计改为单遍；首日应多下载 1 天                              | T1-8 重写，删除该回退                                             |
| `quality.compute_quality` 的判定                                     | `src/trade/data/quality.py` 约 91~95 行                                                                                                                                            | 口径改为“逐笔缺失分钟占比 > 1%”，并按币 × 30 天窗口计算      | T0.17                                                             |
| 服务器上的 P1 冒烟产物（SOLUSDT 2026-10-27，别名 `C01`）             | 服务器 `~/p1-smoke`、`data/bars/C01/`                                                                                                                                              | 旧别名、旧聚合逻辑、只有原子量没有 K 线列                    | **使用者已同意删除**，T1-9 前执行（执行前列出将删除的路径再操作） |
| 指标分组 `train/holdout/blind`、S10 盲测                             | `src/trade/backtest/metrics.py`、`engine.py`/`event_sim.py` 的默认分组 `"train"`、`sanity.py`                                                                                      | 分组改为训练组 / 留出组 / 新进入组                           | T0.15                                                             |
| P2 特征流水线（在拼接后的整段 bars 上计算）                          | `src/trade/features/pipeline.py`、`scripts/build_dataset.py`                                                                                                                       | 新增“数据段”规则（03 §6、04 §5）                             | T0.15；已有特征手算、EWM 无未来、批量/增量一致性测试仍有效        |
| P2 标签只有三重障碍                                                  | `src/trade/labels/triple_barrier.py`、`scripts/build_dataset.py`、`configs/features.yaml`                                                                                          | 默认标签改为 `exit_sim`（04 §7.5）；三重障碍保留为对照       | T0.19；现有三重障碍代码与测试保留                                 |

---

## 0. 优先修复（审阅发现的代码问题）

- [x] **T0.1 回测脚本可读到封存集（违反 AGENTS.md §6 红线 2）**
  - 问题：`scripts/backtest.py` 与 `scripts/r0_signals.py` 默认 `--start 2024-10-01 --end 2026-09-30`，覆盖封存集 M23~M24（2026-08~09）；两者调用 `load_bars` 时都未传 `holdout_start_ms`，守卫不生效。`scripts/build_dataset.py` 已正确传入，不受影响。
  - 涉及：`scripts/backtest.py`、`scripts/r0_signals.py`、`src/trade/data/loader.py`
  - 验收：
    - 时间窗口从 `base.yaml` 推导（把 `build_dataset._window_bounds` 抽到 `trade.data.loader` 共用），删除硬编码日期默认值；
    - `load_bars` 的 `holdout_start_ms` 改为必填参数（或提供不可绕过的包装），防止以后漏传；
    - 只有 `backtest.py --final` 可以打开 `allow_holdout`，并在 `artifacts/reports/FINAL_RUNS.log` 追加一行（06 §8）；
    - 新增测试：不带 `--final` 请求 M23 数据时报错。

- [x] **T0.2 S9 对拍没有比较名义价值与净盈亏**
  - 问题：`sanity.s9_compare` 构造的元组含 `notional`、`net_pnl`，但只比较了 `a[:4]` 和 `a[4]`、`a[5]`，仓位与盈亏不一致时仍判通过。
  - 涉及：`src/trade/backtest/sanity.py`、`tests/backtest/test_sanity.py`
  - 验收：比较全部字段（价格误差 < 1e-9，名义价值与净盈亏也设容差）；新增测试：只改一笔 `notional` 时 S9 判失败。

- [ ] **T0.3 event_sim 改为调用实盘引擎决策代码**（**已决定：P5 实盘引擎完成后、G4 之前**；06 §3.4 已写明阶段性限制）
  - 现状：`event_sim` 直接调用 `engine.precompute_trades`，S9 目前只覆盖组合层。
  - 现在要做的：在 `gate.json` 与 `report.md` 中把 S9 标注为“仅覆盖组合层”（并入 T0.6）。
  - P5 之后要做的：event_sim 逐分钟推进，调用实盘引擎的开仓过滤（含 06 §3.1 条件 8）、追价、入场价与 `exits` 单步更新，不调用 `precompute_trades`；S9 在 2 币 × 1 月 fixture 上通过。

- [x] **T0.4 EXPERIMENTS.log 位置与配置哈希错误，S11 无法统计试验次数**
  - 问题：日志写到 `report_dir/EXPERIMENTS.log`（每次运行一份），06 §8 要求统一写 `artifacts/reports/EXPERIMENTS.log`；`config_hash = sha256(run_id)`，不是配置内容。结果 N 永远为 1。
  - 涉及：`scripts/backtest.py`、`src/trade/backtest/report.py`
  - 验收：日志路径固定为 `artifact_root/reports/EXPERIMENTS.log`；哈希基于全部生效配置（base/backtest/exits/sizing/features/train/universe）、所用快照文件与信号文件的 SHA256；测试：两次相同配置哈希相同，改一个参数哈希不同。

- [x] **T0.5 Deflated Sharpe 公式不正确**
  - 问题：`deflated_sharpe` 未使用 `n_obs`（缺 `sqrt(n_obs-1)`）；期望最大夏普用了简化的 `sqrt(2 ln N)`，没有乘试验间夏普的标准差，也没有 Euler-Mascheroni 插值项；方差项乘 `(1-γ)` 无出处。
  - 完成：按 Bailey & López de Prado (2014)（论文 Snippet 1 + Eq.2）重写 `deflated_sharpe`，新增 `expected_max_sharpe_ratio`。SR0 = 试验均值 + 试验间标准差（ddof=1）× Euler–Mascheroni 插值项；PSR 分母用 √(n−1) 与 (γ₄−1)/4·SR²。单次试验 E[max]=0、空列表、n_obs≤1、方差项≤0 均返回 0。用论文 Snippet 1 参考值与独立 scipy 数值做单测。
  - **遗留**：`EXPERIMENTS.log` 尚未记录 Sharpe/skew/kurtosis/n_obs，S11 端到端数据流未接入（见完成报告），不得视为 S11 已完成。

- [x] **T0.6 回测脚本里的硬编码与缺项**
  - 问题：`scripts/backtest.py` 中 `initial_equity=10000.0` 硬编码 3 处；06 §5.1 的 `reference`/`user` 两个运行只跑了一个；脚本未计算 S1/S4/S5/S6/S9，`run_sanity_checks` 默认把 S6、S9 记为 failed，报告结论没有意义。
  - 涉及：`scripts/backtest.py`、`configs/backtest.yaml`、`configs/sizing.yaml`
  - 完成：新增 `BacktestConfig.reference`（initial_equity/mode/risk_per_trade/base）并写入 `configs/backtest.yaml`，reference 权益来自配置；脚本同时产出 `reference` 与 `user` 两个运行（`run_results[run][scenario]`）；实际计算 S1/S4/S5/S6/S9 并写入 `gate.json`；`run_sanity_checks` 的 S9 附注“仅覆盖组合层（06 §3.4）”，S4/S5/S6/S9 均由脚本计算结果驱动（不再默认 failed）。
  - 验收：参考权益来自配置；同时产出 `reference` 与 `user` 运行；脚本实际计算 S1、S4、S5、S6、S9 并写入 `gate.json`；S9 结果附注“仅覆盖组合层（06 §3.4）”。

- [x] **T0.7 S1 随机入场基准与 06 §7 定义不一致**（与 T0.6 合并）
  - 问题：`random_entry_baseline` 是从候选盈亏里有放回抽样；06 §7 要求在预过滤样本中随机选入场点、保持每币每月笔数、用相同出场规则重新模拟。
  - 完成：`compute_random_entry_candidates` 把预过滤样本（is_sample=1）全部视为候选入场点、复用 `precompute_trades`（含 `simulate_exit` 出场逻辑）与 reference 仓位算单笔净盈亏；`random_entry_baseline` 按每币每月笔数从该组候选抽样、固定种子可复现。
  - 验收：按 06 §7 实现，固定种子可复现的测试。

- [ ] **T0.8 资金费未计入**（依赖 T0.16 把 fundingRate 合入 bars）
  - 问题：bars 无 `funding_rate/funding_time` 列时按 0 计；06 §3.3 要求持仓跨结算时刻扣资金费，06 §6 要求报告资金费合计。
  - 验收：跨结算时刻的持仓扣 `funding_rate × 名义价值`（回测与 `exit_sim` 标签都扣）；`trades.parquet` 含资金费列；手工样例单测。

- [ ] **T0.9 初始化 git 仓库并做首次提交**（**使用者自行处理**）
  - 现状：当前目录没有 `.git`，所有文件时间戳相同。`.gitignore` 已覆盖 `data/`、`artifacts/`、`*.pem`、`.venv/`。

- [x] **T0.10 依赖锁文件按环境拆分**
  - 问题：`requirements.lock` 只有一份，`torch==2.14.1` 无法区分 CPU/CUDA；服务器 P1 冒烟时手动安装了 5 个包，未走锁文件；锁文件按 Python 3.11 生成，本地主力为 3.13；未生成哈希。
  - 完成：`pyproject.toml` 拆 `research`（lightgbm、matplotlib、plotly、`torch==2.14.1+cu126`）、`live`（fastapi、uvicorn、币安官方 SDK `binance-sdk-derivatives-trading-usds-futures==18.2.0`、`torch==2.14.1`）、`dev`；删除 streamlit 与 live 侧 plotly；生成 `requirements-base.lock`、`requirements-research.lock`（CUDA 12.6 torch，cu126 索引）、`requirements-live.lock`（CPU torch，Linux manylinux_2_28），均 `--generate-hashes` 并锁定 setuptools；旧 `requirements.lock` 已删除；README、02 §4/§5.2/§6、AGENTS §3.2 已同步。币安 SDK 包名/版本经 PyPI 官方仓库核实（developers.binance.com 有 Cloudflare 校验无法直读）。
  - 验收：本地 3.13 research 锁可解析并实际安装，torch 为 `2.14.1+cu126`（2602.8 MB CUDA 轮子），`cuda_available: true`（RTX 3060 Laptop GPU，6GB 显存）；服务器 live 锁未实际安装（无服务器环境），标记未验证。

- [x] **T0.11 `trades.parquet` 混入全部情景且标签错误**（与 T0.6 合并）
  - 问题：`report.write_report` 把各情景的交易合并后统一以 `run="reference"、scenario="base"` 写入，同一笔信号出现多次，按此文件统计的笔数、胜率、PF 都会错；`skipped.parquet` 同样混合。
  - 完成：`write_report` 改为接收 `run_results[run][scenario]`，逐（run, scenario）调用 `trades_to_frame`/`skipped_to_frame`，每笔交易与放弃信号都带真实 `run` 与 `scenario`；`skipped.parquet` 增加 `scenario`（及 `run`）列。
  - 验收：每笔交易带真实 `run` 与 `scenario`；`skipped.parquet` 增加 `scenario` 列；测试：2 个情景各 N 笔时 `scenario=="base"` 恰为 N 笔。

- [x] **T0.12 实现 G2“统计显著性”判定**（08 §3 G2、06 §6）
  - 内容：单笔净期望的 UTC 日分块 bootstrap 95% 置信区间（10000 次、种子来自配置）；`gate.json` 写入下限、上限、次数、种子与是否通过；全部 / 训练组 / 留出组分别计算。
  - 涉及：`metrics.py`、`report.py`、`configs/backtest.yaml`（新增配置项同步 pydantic）
  - 完成：`metrics.daily_block_bootstrap_ci` 实现 UTC 日分块 bootstrap 95% CI（10000 次、种子 `sanity.bootstrap_seed`）；`gate_payload` 写入 `g2_bootstrap`（全部/训练组/留出组各自下限、上限、次数、种子、是否通过）；`BacktestConfig.sanity` 新增 `bootstrap_iters`/`bootstrap_seed`。
  - 验收：固定种子两次结果一致；“均值 > 0 但笔数很少”判不通过，“均值明显 > 0 且笔数足够”判通过。

- [x] **T0.18 权益曲线只在平仓时记录，最大回撤偏乐观**（06 §3.4、§5.1、§6，08 G2）
  - 问题：`engine.simulate_portfolio` 与 `event_sim` 只在平仓时向 `equity_rows` 追加记录，权益曲线只含已实现盈亏；持仓期间的浮亏不计入，G2 的“最大回撤 ≤ 15%”和 `max_drawdown_halt` 判定偏乐观。06 §3.4 要求按 `c_t` 盯市。
  - 说明：仓位基准与权益下限用钱包余额（已实现）是设计确定的口径，现有代码这部分**符合设计，不需要改**。
  - 内容：组合模拟逐分钟（或至少在每个持仓的每根 K 线收盘）按 `c_t` 计算未实现盈亏，权益曲线 = 钱包余额 + 未实现盈亏；回撤与回撤停机基于此曲线。
  - 完成：`simulate_portfolio`/`_event_combine` 逐分钟按 `c_t` 盯市（权益 = 钱包余额 + 未实现盈亏），回撤与 `max_drawdown_halt` 基于该曲线；`compute_metrics` 组合层 max_drawdown 改用 equity_curve；仓位基准/权益下限仍用钱包余额（未改）。
  - 验收：手工构造“持仓中先浮亏 10% 再止盈”的样例，最大回撤反映 10% 浮亏；钱包余额与仓位计算结果不变。

---

## 1. 设计变更后的代码对齐（选币、数据管道、标签与质量口径）

- [ ] **T0.13 实现规则化月度选币**（03 §4）
  - 配置：按 03 §4.6 重写 `configs/universe.yaml`（含 `rule.new_entry_delay_days`；**不含数据窗口**，统一取 `base.yaml`）并同步 pydantic 模型；删除 `configs/coins.yaml`。两者必须与代码一起改，单独改配置会让现有测试失败（模型为 `extra="forbid"`）。
  - 代码：
    - `trade.data.universe.build_universe(R, daily_bars, rules, cfg) -> UniverseSnapshot`：03 §4.2 第 1~6 步，第 7 步读取 T0.17 的按窗口质量结论；只用 `R` 之前的数据；
    - 哈希角色（03 §4.5）；`data/universe/aliases.json` 只增不改、不复用；
    - `trade.data.loader.load_universe_snapshot(month)`；现有读取配置的 `universe.load_universe(path)` 改名为 `load_universe_config`，避免重名；
    - `scripts/select_coins.py --build --month/--all`、`--report`；删除旧模式；
    - `exchange.py` 可交易性校验复用到第 5 步；历史交易规则按 03 §4.3 近似（已下架合约从 aggTrades 推断 `tickSize`/`stepSize`）。
  - 验收（08 P1）：选币未来函数测试（修改 `R` 之后的日线，`U(R)` 不变）；各排除规则、不找替补、哈希角色稳定、别名只增不改的单测；同一输入两次结果一致；规则哈希不同时 `--build` 报错不覆盖。
  - 依赖：T0.14（上线/下架时间来源）、T0.17（第 7 步）。

- [ ] **T0.14 服务器验证已下架合约的历史数据**（03 §4.4）
  - 内容：抽查 data.binance.vision 中已下架 USDⓈ-M 永续的日线、1m K 线、aggTrades 是否仍可下载；确认上线/下架时间来源（exchangeInfo 是否列出，否则用首末 K 线时间）；列出全市场 USDT 永续（含已下架）symbol 清单。只读公开数据，不需要 API Key。
  - 验收：P1 报告中有可下载情况统计表与样例 URL。
  - **已决定**：若下架合约数据缺失，接受残余偏差继续推进；按 03 §4.4 在 06 §9 A14 与每份回测报告首页注明“交易范围不含已下架合约，结果偏乐观”，并统计缺失合约数量与其当月排名。

- [ ] **T0.15 回测、信号与数据集按月交易范围和数据段运行**（06 §3.1 条件 8、03 §6、04 §5、05 §5）
  - 内容：
    - `engine.precompute_trades` 与 `event_sim`：信号月不在 `U(R)` 的币不开仓；新进入币自 `R` 起 `universe.yaml: rule.new_entry_delay_days` 天内不开仓；跌出范围的持仓正常出场；
    - 指标分组改为全部 / 训练组 / 留出组 / 新进入组（06 §6）；S10 改为新进入组（笔数 < 50 只报告）；删除默认分组 `"train"` 的回退，未知币直接报错；
    - **数据段**：`compute_features` 与 `build_dataset.py` 按连续数据段分别计算特征、EWM 与滚动窗口，段首 `warmup_days` 天不产生样本；
    - `build_dataset.py` 只对入选月份产生样本；特征以 fp16 落盘；manifest 记录 `universe_rule_hash`；
    - `scripts/backtest.py`、`scripts/r0_signals.py` 从快照取币，不再读 `coins.yaml`。
  - 验收：手工构造 3 币 × 3 月的快照与 bars：不在范围的月份零交易、新进入币前 2 天零交易、跌出范围的持仓正常出场；修改段 A 的数据，段 B 的特征不变；分组统计与手算一致；`tests/test_build_dataset.py` 改用快照。
  - 依赖：T0.13；建议与 T0.1 一起改 `scripts/`。

- [ ] **T0.16 补齐 bars 组装步骤（现有代码缺失）**（03 §2.3、§2.4）
  - 问题：`scripts/aggregate.py` 只输出 aggTrades 原子量，没有合并 1m K 线、补齐缺失分钟（`is_filled`）、metrics 与 fundingRate，就写入了 `data/bars/{alias}/{YYYYMM}.parquet`；而 `load_bars` 要求 K 线 11 列 + 原子量 + `is_filled`，读取会失败。P1 冒烟时的合并是临时手工做的。
  - 内容：实现“1m K 线 + 原子量 + metrics（5 分钟滞后对齐，04 §2 第 2 条）+ fundingRate（最近已结算一期）→ bars 月文件”，复用 `aggregate.py` 已有的补齐分钟函数；**逐笔缺失的分钟原子量按无成交填充（成交额 0，`p_*` 取上一分钟收盘价）并置 `is_filled = 1`**（03 §2.4）。
  - 验收：输出通过 `load_bars` 的 schema 校验；“K 线有、逐笔缺失”的分钟被置 `is_filled = 1` 且被质量报告计入逐笔缺失；metrics 对齐的未来函数测试（T 时刻的值只出现在 `close_time ≥ T + 300000` 的分钟）。

- [ ] **T0.17 质量报告按“币 × 30 天窗口”计算，并按设计口径判定**（03 §2.4、§4.2 第 7 步）
  - **口径（已决定，03 §2.4）**：以下任一条即不合格——K 线缺失分钟占比 > 1%；逐笔缺失分钟占比 > 1%；一致性误差中位数 > 2%。删除现有“`aggtrades_missing_count == 0`”的硬性要求。
  - 内容：`QualityReport` 增加逐笔缺失占比字段；按币、按 30 天窗口计算并缓存，供选币第 7 步与训练/评估使用。
  - 验收：P1 的 SOLUSDT 27 分钟缺失样例：单日窗口判不合格（约 1.9%），30 天窗口判合格（约 0.06%）；其余现有质量测试继续通过。

- [ ] **T0.19 实现 `exit_sim` 标签**（04 §7.0、§7.5，05 §1、§6.1）
  - 内容：
    - `configs/features.yaml` 增加 `label.mode`（`exit_sim` 默认 / `triple_barrier`）与 `label.y_min_r: 0.25`，同步 pydantic 模型；
    - 新增 `trade.labels.exit_sim`：入场价、初始止损、成本与 `engine.precompute_trades` 完全一致——**把入场价与成本计算抽成一个共享函数**（放在 `trade.common` 或 `trade.backtest.costs`），标签与回测都调用它，并调用同一个 `trade.common.exits.simulate_exit`，不另写出场逻辑；
    - 输出字段按 04 §7.5（`y`、`r_mult`、`exit_type`、`sl`、`ret_net`、`bars_held`、`t_exit`）；样本独特性权重与 Purge 使用 `t_exit`；
    - `build_dataset.py` 按 `label.mode` 选择标签；manifest 记录标签模式与 `exits.yaml` 哈希（出场参数改变即视为新数据集）。
  - 验收：同一组 bars 与信号，`exit_sim` 标签的出场时间、出场价、净收益与回测 `precompute_trades` 逐笔完全一致；`r_net ≥ 0.25` 才为正样本的边界测试；未来函数测试（修改 `t_exit` 之后的数据不影响该样本标签）；三重障碍模式的现有测试继续通过。
  - 依赖：T0.15（数据段）；资金费在 T0.8 后接入。

---

## P0 工程骨架、服务器与网络

- [x] T0-1 目录、`pyproject.toml`、`requirements-{base,research,live}.lock`、README 国内镜像 —— 锁文件已按环境拆分（T0.10）
- [x] T0-2 `trade.common.config` 合并顺序与非法字段 —— `tests/common/test_config.py`
- [x] T0-3 `trade.common.logging` 脱敏 —— `tests/common/test_logging.py`
- [x] T0-4 `scripts/check_env.py`
- [x] T0-5 东京服务器开通与 SSH —— P1 报告 §9.1（腾讯云，Ubuntu 26.04，2 核 / 3.6 GB / 51 GB 可用 / 30 Mbps / 每月 1.5 TB）
- [~] T0-6 `latency_probe.py` —— REST P95 ≈ 16 ms 已测；WS `aggTrade`/`kline_1m` 订阅无数据、WS P95 未测，需按最新文档核对流名称
- [~] T0-7 同步通道 —— 脚本存在；缺推代码、拉文件实测（依赖 T0.9）；需增加 `data/universe/` 的同步与按阶段增量同步（T1-9）
- [~] T0-8 质量命令全部通过 —— 报告称通过；本机未复验

## P1 数据管道与选币（服务器）

- [ ] T1-0 已下架合约数据验证 —— 见 T0.14（最先做）
- [x] T1-1 exchangeInfo 解析与可交易性校验 —— `trade/data/exchange.py`（第 5 步复用；`check_history` 按 T0.13 改写）
- [~] T1-2 exchangeInfo 与 leverageBracket 真实拉取与带时间戳快照 —— 缺：拉取与保存快照的脚本（同时生成 `scripts/backtest.py --rules` 需要的 `rules.json`）；已下架合约规则按 03 §4.3 推断
- [ ] T1-3a 全市场日线下载（含已下架合约，窗口 25 个月）
- [↺] T1-3 选币快照 —— 原有的 `--validate`/`--draw-blind` 作废；T0.13 实现后 `--build --all` 生成 M1~M24 快照与 `aliases.json`、`--report`；使用者审阅后设 `reviewed: true`（规则参数按默认值：`top_n=60`、上线 ≥ 60 天、2000 万 USDT 下限、`new_entry_delay_days=2`；**只审参数，不按名单调参，审阅后不再改**）
- [~] T1-4 下载器与聚合 —— 已有：校验和、重试、续传、跨块合并、执行类原子量、表头/微秒兼容、逐笔缺失识别；缺：bars 组装（T0.16）、聚合器提速（T1-8）、分阶段全量下载（T1-9）
- [~] T1-5 质量报告 —— 已有核心指标；缺：按窗口计算与设计口径（T0.17）、汇总表、实时/历史成交口径比对（03 §1.1 第 8 条，需连续录制 1 天）
- [ ] T1-6 分阶段同步到本地，`data/bars/`、`data/universe/` 与服务器 SHA256 一致
- [ ] **T1-7 服务器基准测试**（03 §3、08 P1）
  - 内容：实测下载速度；1 个热门币（如 1000PEPEUSDT、DOGEUSDT）与 1 个冷门币各 1 天的解析耗时与进程峰值内存；在腾讯云控制台确认流量包是否计入入方向。
  - 验收：P1 报告中有实测表与按实测推算的全量耗时，据此修正 03 §3；T1-8 前后各测一次，记录提速倍数。
- [ ] **T1-8 聚合器提速改造**（03 §2.2）
  - 内容：两遍改为按日顺序的单遍处理（`whale_thr(D) = p99(D-1)` 不变；每段首日多下载 1 天只算 p99；**删除现有脚本“首日使用当日 p99”的回退**；缺日沿用最近 p99 并记入质量报告）；`pyarrow.csv.open_csv` 流式读取（32 MB 块、4 列、显式类型）；numba 分钟汇总替换现有逐行 `itertuples` 循环，保留纯 Python 参考实现；p99 精确计算（不用近似）；月文件先写临时文件再改名。
  - 验收（**全量下载的硬性前提**）：等价测试——新旧实现在 fixture 与 1 个真实热门币 × 3 天上逐字段对比，浮点误差 < 1e-9、其余完全一致（数据完整的情况；旧实现首日的当日 p99 回退不参与对比）；缺日情况单独测试；numba 与 Python 参考一致；单进程峰值内存 < 1.5 GB；现有跨块合并、方向、执行类原子量、表头/微秒兼容测试继续通过。
- [ ] **T1-9 任务清单、优先级调度与分阶段同步**（03 §2.1、§2.3）
  - 内容：由 M1~M24 快照生成 `(币, 日)` 任务清单 `_jobs.parquet`（每段入选期 + 前 30 天 + 1 天）；按 调参期 → 测试段 → 封存集 顺序，阶段内入选月数多的币优先；2 进程 + 每进程 1 个预取线程（预取 2 天）、在途 zip ≤ 2 GB；失败清单不中断批次、批末重试；`_done.parquet` 断点续传；S8 所需 3 币 × 1 测试月保留原始逐笔（`keep_raw`）；`sync.ps1`/`sync.sh` 支持按阶段增量同步 `data/bars/` 与 `data/universe/`。
  - 前置：删除服务器旧冒烟产物（`~/p1-smoke`、`data/bars/C01/`，使用者已同意；执行前先列出路径）。
  - 验收：2 进程运行时服务器内存 < 3 GB；中途 `kill` 后重跑从断点继续且结果不变；同一输入重复运行输出 SHA256 一致；每阶段同步后两端 SHA256 一致。
  - 依赖：T0.13（快照）、T0.16（组装）、T0.17（质量）、T1-8（等价测试通过）、使用者已审阅快照。

> **G1 闸门未通过**：缺 T0.14 结论、M1~M24 快照与 `reviewed: true`、全量合格数据，以及真实数据上的一致性测试与未来函数测试。

## P2 特征与标签（本地）

- [x] T2-1 特征注册表与 37 个特征，执行类原子量禁入 —— `tests/features/`
- [x] T2-2 EWM 标准化无未来 —— `test_normalize.py`
- [x] T2-3 批量与增量一致性（fixture 2 币 × 3 天）
- [x] T2-4 预过滤、三重障碍边界、独特性权重（三重障碍为对照模式，代码保留）
- [x] T2-5 未来函数测试（fixture）
- [~] T2-6 `build_dataset.py` 与 manifest —— 缺：按月范围与数据段（T0.15）、`exit_sim` 标签（T0.19）、fp16 落盘、`universe_rule_hash`；真实 bars 上的保留率与正样本率
- [~] T2-7 合约特征 `oi`/`funding_rate` 列名与真实数据对齐 —— 依赖 T0.16
- [ ] T2-8 真实数据上复跑一致性测试与未来函数测试（G1 要求）

## P3 回测引擎（R0 驱动）

- [x] T3-1 R0 信号输出 `signals.parquet` —— `test_r0.py`（脚本侧的封存集与选币改动见 T0.1、T0.15）
- [x] T3-2 出场策略 `exits`（numba + Python 参考版一致）—— `test_exits.py` 14 项
- [x] T3-3 成本模型与 4 个情景、盈亏平衡滑点二分 —— `test_costs.py`
- [x] T3-4 仓位 `sizing` —— `test_sizing.py` 12 项（仓位基准为钱包余额，与现有实现一致）
- [~] T3-5 逐笔预计算 + 组合模拟 —— 缺：交易范围过滤（T0.15）、资金费（T0.8）、入场价与成本抽为共享函数（T0.19）、06 §5.4 速查表数值复现测试（08 P3 验收项，代码中不存在）；盯市权益曲线已由 T0.18 完成
- [!] T3-6 `event_sim` 与 S9 —— 见 T0.2、T0.3
- [ ] T3-7 S8 逐笔重放（服务器，3 币 × 1 测试月），含改单 1 秒旧止损窗口（原始数据由 T1-9 保留）
- [~] T3-8 指标与报告、`gate.json`、`EXPERIMENTS.log` —— 缺：S11 端到端数据流（T0.5 遗留）、新分组（T0.15）、`sizing_sweep`（06 §5.5）、PNG 图表；T0.4、T0.6、T0.11、T0.12、T0.18 已完成
- [ ] **T3-9a 调参期 R0 毛期望早期检查**（M1~M12 数据到本地后立即做）
  - 内容：在 M1~M12 上跑 R0（阈值事先固定），统计成本前后的单笔期望与笔数，与往返成本约 15 bp 比较。
  - 约束：只用调参期，不碰测试段与封存集；结果**只用于决定是否继续投入**，不得据此修改 R0 阈值或任何参数。
- [ ] T3-9 真实数据上跑一次 R0 全流程回测（依赖 G1、T0.1、T0.15）

## P4 模型训练与正式回测（本地，依赖 G1；T4-3 第 1 步可在调参期数据到位后提前开始）

- [ ] T4-0 安装 CUDA 版 torch —— 用 T0.10 的 `requirements-research.lock`
- [ ] T4-1 数据集：fp16 memmap 加载入选月份、GPU 常驻（不足时 `pinned_cpu`，05 §5.1）、索引切片、币种均衡；窗口不跨数据段；GPU/CPU 一致单测；实测显存写入报告
- [ ] T4-2 TCN 因果性单测
- [ ] T4-3 调参期 M1~M12 两步搜索（05 §6.1）
  - 第 1 步：6 组出场与止损参数，每组重建 `exit_sim` 标签 → 训练 M0 → 验证段选阈值 → `base` 情景 PF 与净收益，选定后冻结 `exits.yaml` 与 `k_sl`；
  - 第 2 步：固定第 1 步结果，模型超参最多 24 组；
  - 两步合计 ≤ 30 组，全部登记 `EXPERIMENTS.log`（含失败组）；`y_min_r` 不参与搜索。
- [ ] T4-4 M0 / M1 / M2 训练、早停、bf16、种子集成；同种子两次 PR-AUC 差 < 0.002
- [ ] T4-5 校准与阈值（只在各折验证集选），`model_meta.json` 字段齐全（含 `label.mode`、`y_min_r`、`exit_policy`、`universe_rule_hash`）
- [ ] T4-6 5 折 Walk-Forward + F_final，测试段 `signals.parquet` 含当月范围内全部币
- [ ] T4-7 产物加载校验：特征哈希、出场策略不一致时报错
- [ ] T4-8 正式回测 S1~S11，G2 自动判定（含统计显著性）
- [ ] T4-9 G3 封存集，`--final` 只运行一次（**需使用者在场确认**）

## P5 实盘引擎（测试网，服务器）

> 可与 P3/P4 并行，用 R0 或随机信号联调；上线必须等 G3 通过。

- [ ] T5-1 通知模块（07 §8）—— **不依赖其他模块，可最先做**
- [ ] T5-2 币安客户端 `ExchangeClient`（官方 SDK 封装）、签名对照官方示例、`api_smoke.py --env testnet`；SDK 版本写入 `requirements-live.lock`
- [ ] T5-3 启动流程：费率核对、杠杆分层、5x、30 天预热
- [ ] T5-4 行情：多连接（按订阅上限拆分）、23 小时轮换、缺口回补、延迟监控、原子量录制
- [ ] T5-5 月度再平衡（07 §3.5）：调用同一个 `build_universe()`；新进入币按 `universe.yaml: rule.new_entry_delay_days` 只监控 + 订单流预热；退出币持仓保留至平仓；失败沿用上月；全市场日线拉取权重实测；60 币满负荷下 CPU / 内存 / 延迟实测
- [ ] T5-6 交易引擎时序、推理线程池、状态库记录
- [ ] T5-7 订单：IOC 开仓、先止损后止盈、OCO 模拟、追踪改单先挂新后撤旧、幂等 clientOrderId（alias 取 `aliases.json`）；仓位基准取 `walletBalance`（07 R1、06 §5.1）
- [ ] T5-8 对账 6 种情况
- [ ] T5-9 风控 R1~R14、安全模式、Kill Switch
- [ ] T5-10 `trade.live.control` 与 `set_sizing.py` 白名单与校验
- [ ] **T5-12 看板所需的状态库数据**（07 §3.4、§6.1）
  - 内容：新增 `scan_cycles`、`scan_state` 表，主循环每轮决策结束后一次事务写入交易范围内全部币的处理结果（含 `seq` 处理顺序与 `status`）；`events.notified` 字段；`orders`/`fills`/`positions` 永久保留，其余表按 07 §6.1 清理；对账时用 `userTrades`/`income` 补齐断线缺口（7 天内）。
  - 验收：写入失败只记日志、不影响交易；决策路径延迟不因写入增加（`latency` 表对比）；补齐逻辑在模拟断线 10 分钟的测试中补全缺失成交。
- [ ] T5-11 G4 测试网 ≥ 7 天演练（含一次月度再平衡演练；Kill Switch 用电脑命令行、电脑看板、手机 SSH 各一次）；**G4 之前完成 T0.3**

## P6 可视化看板（FastAPI + 静态前端，深色 iOS 风格，SSH 隧道访问）

> 规格：docs/10（整体重写）；布局以 `docs/assets/dashboard-overview.svg` 为准；只有深色主题，不做浅色与切换。
> 现有 `src/trade/dashboard/__init__.py` 只是 Streamlit 时期的空包，按新设计重写，无其他代码需要迁移。

- [ ] **T6-1 后端骨架与安全**（10 §2、§3、§7、§9、§11）
  - 内容：`python -m trade.dashboard`；FastAPI + uvicorn；`configs/dashboard.yaml`（按 10 §11）与 pydantic 模型；`server.address` 非 127.0.0.1 拒绝启动；所有 POST 与 `/ws` 校验 `Host`/`Origin`；状态库只读 URI 连接；`/api/status` 与 `/ws` 骨架；无 `state.db` 时只开放研究接口。
  - 验收：配置为 `0.0.0.0` 拒绝启动；非法 `Host`/`Origin` 被拒；`TestClient` 冒烟通过；`mypy src/trade/dashboard` 通过。
- [ ] **T6-2 前端框架与视觉规范**（10 §3、§4、§5.0）
  - 内容：`static/index.html`、CSS 设计令牌（10 §4 的颜色、圆角、毛玻璃、字体、动效曲线）、左侧栏与顶栏、页面路由（Alpine.js）；把 Alpine.js、lightweight-charts、Plotly.js 下载到 `static/vendor/`，`VERSIONS.txt` 记录版本与 SHA256；`prefers-reduced-motion` 支持；所有文本转义。
  - 验收：断网可打开；布局与草图一致；SHA256 校验测试通过；事件文本中的 HTML 被转义。
- [ ] **T6-3 回测与研究页**（10 §5.5；只依赖 P1~P4 产物，可在 P3 完成后先做）
  - 验收：用真实报告打开，数值与 `summary.json`、`gate.json` 一致；交易范围快照与质量页可浏览。
- [ ] **T6-4 总览与全市场扫描网格**（10 §5.1、§6；依赖 T5-12）
  - 内容：4 张指标卡、扫描网格（按 `seq` 点亮、进度条时长取实际耗时、状态样式、悬停、点击弹出 K 线抽屉、本轮统计、再平衡淡入淡出）、权益曲线、持仓卡、挂单、事件。
  - 验收：测试网运行时点亮顺序与 `scan_state.seq` 一致；统计数与 `scan_cycles` 一致；断线重连直接显示最新状态；60 格动画不触发重排（只用 `transform`/`opacity`）。
- [ ] **T6-5 持仓与挂单、交易记录、绩效页**（10 §5.2~§5.4）
  - 验收：订单或持仓变化后 1 秒内推送刷新；K 线上开平仓与止损阶梯与 `positions`/`orders` 一致；止损单非“有效”时红色告警；交易记录筛选、分页、CSV 导出；绩效统计与手算一致。
- [ ] **T6-6 控制页**（10 §5.6、§8）
  - 验收：调参实时预览；校验不通过按钮禁用；PIN 常量时间比较、5 次错误锁定 15 分钟；一键停机调用 `kill_switch` 同一函数；修改后交易进程下一分钟生效并收到微信通知。
- [ ] **T6-7 SSH 隧道与性能**（02 §5.6、10 §10）
  - 内容：`scripts/sync.ps1 tunnel`（建立隧道并打开浏览器）；服务器安全组只开放 22。
  - 验收：电脑经隧道可访问、公网 `curl` 8501 不通；看板常驻时交易进程延迟 P95 上升 ≤ 20 ms、看板 CPU < 5%。

## P7 影子盘 / P8 小资金实盘

- [ ] T7-1 systemd 两个服务（交易、看板）、`/etc/trade/env`、fail2ban、安全组只开放 22
- [ ] T7-2 每日一致性任务
- [ ] T7-3 G5 影子盘 ≥ 14 天
- [ ] T8-1 G6 小资金实盘 ≥ 30 天且 ≥ 50 笔

---

## 代码与设计书不一致汇总

| #   | 位置                                           | 设计书                                                   | 代码现状                                                  | 处理                                        |
| :-- | :--------------------------------------------- | :------------------------------------------------------- | :-------------------------------------------------------- | :------------------------------------------ |
| 1   | `scripts/backtest.py`、`scripts/r0_signals.py` | 03 §6、AGENTS §6 红线 2                                  | 默认窗口含封存集且未启用守卫                              | T0.1                                        |
| 2   | `event_sim.py`                                 | 06 §3.4（含阶段性限制）                                  | 复用 `precompute_trades`                                  | 符合阶段性限制；报告已标注“仅覆盖组合层”（T0.6），P5 后 T0.3 |
| 3   | `sanity.s9_compare`                            | 06 §7 S9                                                 | 只比较部分字段                                            | T0.2                                        |
| 4   | `report`/`backtest.py`                         | 06 §8                                                    | 每次运行一份日志，哈希取 run_id                           | T0.4                                        |
| 5   | `sanity.deflated_sharpe`                       | 06 §7 S11                                                | 公式已按论文重写；日志未记 Sharpe，S11 未端到端接入       | T0.5                                        |
| 6   | `sanity.random_entry_baseline`                 | 06 §7 S1                                                 | 已按预过滤样本选点、保持每币每月笔数、复用出场逻辑、固定种子 | **已一致，无需修改**                        |
| 7   | `engine`                                       | 06 §3.3 资金费                                           | 计为 0                                                    | T0.8                                        |
| 8   | `engine` 仓位基准                              | 06 §5.1：钱包余额                                        | 用已实现权益                                              | **已一致，无需修改**                        |
| 9   | `sizing` 滑点                                  | 06 §4.2 / §5.2                                           | 用参考名义估算滑点                                        | 建议写入 06 §5.2                            |
| 10  | `exits`                                        | 06 §3.3                                                  | 新增 `eod` 出场类型                                       | 建议写入 06 §3.3 与 §6                      |
| 11  | 服务器                                         | 02：Ubuntu 24.04 基线                                    | 实际 Ubuntu 26.04 + uv Python 3.13                        | 已部分同步，部署验收时复核                  |
| 12  | 依赖                                           | 02 §5.2、AGENTS §3.2                                     | 单一锁文件；torch 不分 CPU/CUDA                           | 已按环境拆分（T0.10）                       |
| 13  | 选币                                           | 03 §4                                                    | 固定名单、`--validate`/`--draw-blind`、旧 `universe.yaml` | T0.13、T0.15                                |
| 14  | `report.write_report`                          | 06 §8                                                    | 已保留真实 run/scenario                                   | **已一致，无需修改**                        |
| 15  | `scripts/aggregate.py`                         | 03 §2.2：只用前一日 p99；单遍                            | 两遍；首日用当日 p99（未来信息）；逐行 Python 循环        | T1-8                                        |
| 16  | bars 组装                                      | 03 §2.3：K 线 + 原子量 + `is_filled` + metrics + funding | 无组装步骤，只写原子量                                    | T0.16                                       |
| 17  | `quality.compute_quality`                      | 03 §2.4：三项各 1%/1%/2%，按 30 天窗口                   | 要求逐笔缺失为 0；只给整段结论                            | T0.17                                       |
| 18  | 特征流水线                                     | 03 §6、04 §5：数据段独立计算                             | 在拼接后的整段数据上计算                                  | T0.15                                       |
| 19  | 组合模拟权益曲线                               | 06 §3.4、§5.1：回撤按盯市权益                            | 已按 `c_t` 逐分钟盯市（钱包余额 + 未实现盈亏）            | **已一致，无需修改**                        |
| 20  | 标签                                           | 04 §7.5：默认 `exit_sim`                                 | 只有三重障碍                                              | T0.19                                       |
| 21  | 看板                                           | 10：FastAPI + 静态前端、SSH 隧道                         | 只有 Streamlit 时期的空包；依赖仍含 streamlit、plotly     | T6-1~T6-7、T0.10                            |

## 需要使用者操作的事项（无待决策项）

1. T0.9：初始化或接回 git 仓库（使用者自行处理）。
2. T1-3：快照生成后审阅 `universe_report.md` 并把 `universe.yaml` 的 `reviewed` 改为 `true`（参数按默认值，审阅后不再改）。
3. T4-9：G3 封存集运行时在场确认。

## 已确定的事项（2026-10-30）

- G2 增加统计显著性；代码见 T0.12。
- 不使用 BNB 抵扣手续费；不加大行情（BTC 日线趋势）过滤。
- 规则化月度选币，默认参数 `top_n=60`、上线 ≥ 60 天、2000 万 USDT 下限、`new_entry_delay_days=2`；代码见 T0.13、T0.15。
- 数据管道不升配：单遍 + pyarrow + numba + 分阶段调度；代码见 T1-7~T1-9。
- 选币方案补充：`new_entry_delay_days` 只在 `universe.yaml` 配置一次；`universe.yaml` 不重复配置数据窗口；新增“数据段”规则；快照加载接口命名为 `load_universe_snapshot`。
- 使用者确认的四项决定：
  1. event_sim 推迟到 P5 之后改造，G4 之前完成（T0.3）；
  2. 仓位基准与权益下限用钱包余额，回测与实盘同口径；回撤按盯市权益（T0.18）；
  3. 质量判定：K 线缺失 / 逐笔缺失 / 一致性误差分别以 1% / 1% / 2% 为限，按 30 天窗口（T0.17）；
  4. 标签默认 `exit_sim`、`y_min_r = 0.25`，调参先出场后模型（T0.19、T4-3）。
- 旧 `requirements.lock` 删除（T0.10）；服务器旧冒烟产物删除（T1-9 前置）；已下架合约数据若缺失则接受残余偏差并在报告中注明（T0.14）。
- 看板方案：不买域名、去掉 Caddy/HTTPS，服务器只开放 22，电脑经 SSH 隧道访问；手机只收微信推送，无手机页；FastAPI + 静态前端，只有深色 iOS 风格；保留 SQLite 状态库，看板不调用币安 API；新增全市场扫描网格。代码见 T5-12、T6-1~T6-7，依赖调整并入 T0.10。
