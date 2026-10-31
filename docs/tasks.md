# tasks.md —— 开发进度与待办

> 依据：docs/ 设计书（2026-10-30 定版：G2 统计显著性、规则化选币、数据管道提速、使用者确认的四项决定、看板方案）、src/ 与 scripts/ 现有代码（2026-10-30 静态审阅）。
> 测试是否通过取自各完成报告，本次**未重新运行**测试。
> 状态：`[x]` 已完成且仍符合当前设计 `[~]` 部分完成（写明缺什么） `[ ]` 未开始 `[!]` 已发现问题，需修复 `[↺]` 已完成但因设计变更需要重做
> 执行规则：每次只做一个任务；先读仓库规范 `AGENTS.md` 和任务引用的设计章节。代码任务完成后运行 `AGENTS.md` §8 质量命令，通过且验收证据齐全才标 `[x]`，然后停下等使用者确认。真实数据、服务器或使用者审阅等外部条件未满足时，保留 `[~]` 并写清唯一阻塞条件及解除步骤；不得用 fixture 结果代替真实数据验收。
> 编号说明：`T0.x`（点号）为审阅发现的修复与设计对齐任务；`T0-x`、`T1-x`……（横杠）为 08 各阶段的原有任务。

---

## 执行顺序（先看这里）

| 顺序 | 任务                                                            | 说明                                                                                      |
| :--- | :-------------------------------------------------------------- | :---------------------------------------------------------------------------------------- |
| 1    | T0.9                                                            | 已由使用者完成；仅供历史记录，不是当前阻塞项                                               |
| 2    | T0.1 → T0.2 → T0.4 → T0.5 → T0.6 + T0.7 + T0.11 + T0.12 → T0.18 | 回测正确性修复，T0.1（封存集泄露）最优先；只用 fixture，不依赖真实数据                    |
| 3    | T0.10                                                           | 依赖拆分，服务器全量跑数据前完成，两端环境一致                                            |
| 4    | T0.14                                                           | 服务器验证已下架合约数据（只读、约半天），结论影响 T0.13                                  |
| 5    | T1-7（前测）→ T0.16 → T1-8 → T0.17 → T1-7（后测）               | 基准、组装、提速、质量串行集成；真实服务器依赖未满足时保留阻塞状态                       |
| 6    | T0.13                                                           | 依赖 T0.14 与 T0.17 的质量缓存，之后实施                                                  |
| 7    | T1-3a → T1-3（快照）→ 使用者审阅 `reviewed: true`               | 快照决定下载清单，**必须在全量下载之前**                                                  |
| 8    | T0.15 → T0.19                                                   | 回测与数据集按月交易范围和数据段运行；`exit_sim` 标签（可与 7 并行开发，用 fixture 验证） |
| 9    | 清理服务器旧冒烟产物 → T1-9 分阶段全量下载                      | 前提：T1-8 等价测试通过、快照已审阅                                                       |
| 10   | 调参期数据到本地后：T3-9a、P2 数据集、T4-3 第 1 步              | 服务器同时继续处理测试段                                                                  |
| 11   | 全部数据完成 → G1                                               | 关键路径；期间可并行做 T5-1 通知模块、T6-1~T6-3 看板骨架与研究页                          |
| —    | T0.3                                                            | P5 实盘引擎完成后、G4 之前                                                                |
| —    | T0.8                                                            | T0.16（fundingRate 合入 bars）之后                                                        |

### 阶段执行顺序与新窗口指令

阶段按依赖和闸门推进，不是所有工作都必须严格串行执行 P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8：P0 是基础；P1 与 P2 合并满足 G1 后进入正式 P3/P4；P5 可与 P3/P4 并行开发，但上线仍须等 G3；P6 的研究页可在 P3 后、P4 产物尚未齐备时先开发，实盘页面依赖 P5 状态库；P7 必须等 G4，P8 必须等 G5。T5-1 通知模块可提前做。具体执行顺序以本文件顶部的任务顺序、各任务依赖和闸门为准，不能为了按阶段推进而跳过阻塞条件。

每个阶段使用对应 `.github/prompts/` 指令，并在**全新聊天窗口**执行。每个窗口只实施一个最早可执行的未完成任务；验证、文档同步、更新任务状态和中文报告完成后停止。若阶段仍有任务，在另一个新窗口再次运行同一阶段指令。指令会要求新窗口重新读取规范、设计、任务状态和现有代码，不依赖历史聊天记录。

| 阶段 | 新窗口指令 |
| :--- | :--- |
| P0 工程骨架与环境 | ✅ 已完成（指令已删除） |
| P1 数据管道与选币 | ✅ 已完成（指令已删除；T1-9 调参期下载后台运行、测试段/封存集与 T1-6 同步见下方任务状态） |
| P2 特征与标签 | [.github/prompts/trade-p2.prompt.md](../.github/prompts/trade-p2.prompt.md) |
| P3 回测引擎 | [.github/prompts/trade-p3.prompt.md](../.github/prompts/trade-p3.prompt.md) |
| P4 模型训练与正式回测 | [.github/prompts/trade-p4.prompt.md](../.github/prompts/trade-p4.prompt.md) |
| P5 测试网实盘引擎 | [.github/prompts/trade-p5.prompt.md](../.github/prompts/trade-p5.prompt.md) |
| P6 可视化看板 | [.github/prompts/trade-p6.prompt.md](../.github/prompts/trade-p6.prompt.md) |
| P7 影子盘与部署 | [.github/prompts/trade-p7.prompt.md](../.github/prompts/trade-p7.prompt.md) |
| P8 小资金实盘 | [.github/prompts/trade-p8.prompt.md](../.github/prompts/trade-p8.prompt.md) |

### T0.x 当前状态与接手说明

截至 2026-10-31，T0.1~T0.19 共 19 项中，17 项为 `[x]`；T0.3 与 T0.17 尚未完全闭环，不能宣称 T0.x 全部完成。T0.9 已由使用者完成。`[x]` 仅表示该任务列出的验收已满足；fixture 通过不代表真实数据或服务器验收通过。

#### T0.3：P5 后完成事件模拟器与实盘决策对拍

- **目前已完成**：S9 报告已注明“仅覆盖组合层（06 §3.4）”，并有对应测试；此部分随 T0.6 完成，不要重复实现或删除提示。
- **仍未完成**：P5 实盘引擎完成后、G4 开始前，改造 `trade.backtest.event_sim`，使它逐分钟调用实盘引擎实际使用的开仓过滤、追价与入场价、出场状态更新和仓位/风险决策代码；模拟器不得发网络请求，也不得继续用 `precompute_trades` 代替实盘入场与逐步出场决策。
- **开始前置**：先核实 P5 决策函数的实际接口和状态输入输出；重用现有实现，不复制出第二套交易规则。若实盘接口无法被离线调用，先报告具体边界与最小接口建议，不得自行另写一套“等价”规则。
- **验收**：构造 2 币 × 1 月的确定性 fixture；在相同信号、bars 和配置下，对快速模拟器与 `event_sim` 逐笔比较入场/出场时间、出场类型、成交价、名义价值及净盈亏，达到 06 §7 S9 的一致性要求；增加或更新测试证明实盘决策路径被调用。完成后更新回测报告中的 S9 覆盖范围，并移除“仅覆盖组合层”的阶段性提示；运行相关测试及 `AGENTS.md` §8 质量命令，通过后再标 `[x]`。
- **安全边界**：仅使用 fixture 和离线决策代码，不连测试网或生产接口、不下单、不读取封存集；此任务必须在 G4 前完成。

#### T0.17：用真实 bars 完成质量窗口验证

- **目前已完成**：质量口径、`compute_quality_windows()`、原子 parquet 缓存 API 及 fixture 测试已完成。fixture 已证明 27/1440 = 1.875% 的单日窗口不合格，27/43200 = 0.0625% 的 30 天窗口合格。实现不需要因真实数据尚未生成而重做。
- **仍未完成**：使用 T1 数据管道生成的真实 SOLUSDT bars，复核同一缺失样例在 1 天与 30 天窗口的实际质量结论，并保存可追溯证据。现有 `scripts/select_coins.py --quality` 只读取质量缓存，不负责从 bars 生成缓存；计算入口是 `trade.data.quality.compute_quality_windows()`，写入入口是 `write_quality_cache()`。
- **前置条件**：服务器端数据流程已经产出目标日期的真实 SOLUSDT 月 bars，并同步到本机；文件须包含可区分 K 线缺失与逐笔缺失的字段（`kline_missing`/`aggtrades_missing`，或该模块明确支持的兼容字段）。不得在本机直连币安，也不得把手造数据或旧冒烟数据称为真实 P1 数据。
- **执行步骤**：确认 bars 文件路径、币种、UTC 覆盖区间和缺失字段来源；读取真实 bars 后，以同一个按分钟对齐的 UTC 窗口终点分别调用 `compute_quality_windows(symbol, bars, [window_end_ms], window_days=1)` 和 `window_days=30`；检查并记录 `missing_ratio`、`aggtrades_missing_ratio`、`consistency_error_median`、`qualified`，再按需用 `write_quality_cache()` 生成可供 T0.13 使用的 parquet，并用 `load_quality_cache()` 回读校验 schema 与窗口主键。
- **验收与完成判定**：目标样例确有 27 个逐笔缺失分钟时，1 天窗口的逐笔缺失占比应为 1.875% 且 `qualified=False`，同终点的 30 天窗口应为 0.0625% 且在其他两项指标也达标时 `qualified=True`。若真实数据不满足 27 分钟样例或其他指标导致结论不同，保留原始实测结果、核查数据来源及窗口边界，并将差异报告给使用者；不得改阈值、挑选窗口或伪造预期值来通过验收。把实际 bars 路径（相对仓库路径）、窗口终点、计算结果、缓存回读结果和执行环境写入 P1 完成报告后，才可将 T0.17 标为 `[x]`。真实 bars 尚不可用时继续标 `[~]`，不阻塞 fixture 代码验收。

---

## 已完成工作中需要作废或重做的部分（设计变更导致）

| 已完成的内容                                                         | 位置                                                                                                                                                                               | 原因                                                         | 处理                                                              |
| :------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------- | :---------------------------------------------------------------- |
| 固定名单校验 `--validate`、盲测 `--draw-blind`、`--suggest` 及其测试 | `scripts/select_coins.py`、`src/trade/data/universe.py`（`CoinsConfig`、`validate_coins`、`draw_blind`、`eligible_blind_candidates`、`dump_coins`）、`tests/data/test_universe.py` | 设计改为规则化月度选币                                       | 已删除并重写（T0.13 完成）；`exchange.py` 的可交易性校验函数保留复用     |
| `configs/coins.yaml`、`configs/universe.yaml`（旧字段）              | `configs/`                                                                                                                                                                         | 同上                                                         | 已按 03 §4.6 重写 `universe.yaml`，删除 `coins.yaml`（T0.13 完成） |
| `exchange.check_history` 的 train/holdout/blind 历史长度规则         | `src/trade/data/exchange.py` 约 160~195 行、`tests/data/test_exchange.py`                                                                                                          | 角色改为哈希分配，历史长度改为“上线 ≥ 60 天”                 | 第 3 步已由 `build_universe` 的 `min_listing_days` 实现（T0.13）；`check_history` 留待后续按需清理 |
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

- [~] **T0.3 event_sim 改为调用实盘引擎决策代码**（**已决定：P5 实盘引擎完成后、G4 之前**；06 §3.4 已写明阶段性限制）
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

- [x] **T0.8 资金费未计入**（依赖 T0.16 把 fundingRate 合入 bars）
  - 问题：bars 无 `funding_rate/funding_time` 列时按 0 计；06 §3.3 要求持仓跨结算时刻扣资金费，06 §6 要求报告资金费合计。
  - 验收：跨结算时刻的持仓扣 `funding_rate × 名义价值`（回测与 `exit_sim` 标签都扣）；`trades.parquet` 含资金费列；手工样例单测。
  - 完成：新增 `trade/common/funding.py`（结算事件提取 + 区间求和，结算时刻 `entry_ts < ft <= exit_ts`）；`precompute_trades` 记录 `funding_rate_sum`，`simulate_portfolio`/`event_sim`/`reference_trade_net_pnl` 扣 `funding_rate × 名义价值`；`trades.parquet` 含 `funding_fee` 列、报告与 `compute_metrics` 含合计；`exit_sim` 标签同样扣除。跨结算/边界/多期求和单测通过。

- [x] **T0.9 初始化 git 仓库并做首次提交**（**使用者已自行处理**）
  - 现状（已复核 2026-10-31）：`.git` 已存在并有多次提交（分支 `main`、`devlop`，当前在 `devlop`，含初始提交 `Initial trade project scaffold`）；`.gitignore` 已覆盖 `data/`、`artifacts/`、`*.pem`、`.venv/`。
  - 备注：当前分支名 `devlop` 疑似 `develop` 拼写，且不符合 AGENTS.md §9 分支命名规范（`feat/`/`fix/`/`docs/`），建议后续留意；本机 PATH 无 git 可执行文件，本次经读取 `.git` 目录确认，未运行 git 命令。

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

- [x] **T0.13 实现规则化月度选币**（03 §4）——已完成（2026-10-31）
  - 配置：按 03 §4.6 重写 `configs/universe.yaml`（含 `rule.new_entry_delay_days`；**不含数据窗口**，统一取 `base.yaml`）并同步 pydantic 模型；删除 `configs/coins.yaml`。两者已与代码一起改。
  - 代码：`trade.data.universe.build_universe(R, daily_bars, rules, cfg)`（03 §4.2 第 1~7 步，只用 R 之前数据，第 7 步读 T0.17 质量缓存、缺结论按不合格处理不找替补）；sha256 哈希角色；`data/universe/aliases.json` 只增不改、不复用；`load_universe_snapshot(data_root, month)`；`load_universe` 改名 `load_universe_config`；`select_coins.py --build --month/--all`、`--report`（删旧模式）；`exchange.py` 可交易性校验保留复用、第 5 步 `_tradable` 内联实现；`scripts/{download,aggregate,backtest,r0_signals,build_dataset}.py` 已从快照/别名取币。
  - 验收（08 P1）：未来函数测试、各排除规则、不找替补、哈希角色稳定、别名只增不改、同一输入两次结果一致、规则哈希不同 `--build` 报错不覆盖，均已落单测并通过。
  - 依赖：T0.14、T0.17 已完成（质量缓存 API 可用，fixture 验收）。

- [x] **T0.14 服务器验证已下架合约的历史数据**（03 §4.4）
  - 内容：抽查 data.binance.vision 中已下架 USDⓈ-M 永续的日线、1m K 线、aggTrades 是否仍可下载；确认上线/下架时间来源（exchangeInfo 是否列出，否则用首末 K 线时间）；列出全市场 USDT 永续（含已下架）symbol 清单。只读公开数据，不需要 API Key。
  - 验收：P1 报告中有可下载情况统计表与样例 URL。
  - 完成：东京服务器对 LUNAUSDT、FTTUSDT 的 1d、1m、aggTrades 样例均返回 HTTP 200；LUNAUSDT 首末日 K 线为 2021-01-28 / 2022-05-13。S3 月度 K 线前缀列出 900 个 USDT 后缀目录，当前 exchangeInfo 有 522 个 USDT 永续；目录清单与口径限制见报告及 JSONL 附件。FTTUSDT 归档延伸到 2026-09，未作为已下架时间样例。
  - **已决定**：若下架合约数据缺失，接受残余偏差继续推进；按 03 §4.4 在 06 §9 A14 与每份回测报告首页注明“交易范围不含已下架合约，结果偏乐观”，并统计缺失合约数量与其当月排名。

- [x] **T0.15 回测、信号与数据集按月交易范围和数据段运行**（06 §3.1 条件 8、03 §6、04 §5、05 §5）
  - 内容：
    - `engine.precompute_trades` 与 `event_sim`：信号月不在 `U(R)` 的币不开仓；新进入币自 `R` 起 `universe.yaml: rule.new_entry_delay_days` 天内不开仓；跌出范围的持仓正常出场；
    - 指标分组改为全部 / 训练组 / 留出组 / 新进入组（06 §6）；S10 改为新进入组（笔数 < 50 只报告）；删除默认分组 `"train"` 的回退，未知币直接报错；
    - **数据段**：`compute_features` 与 `build_dataset.py` 按连续数据段分别计算特征、EWM 与滚动窗口，段首 `warmup_days` 天不产生样本；
    - `build_dataset.py` 只对入选月份产生样本；特征以 fp16 落盘；manifest 记录 `universe_rule_hash`；
    - `scripts/backtest.py`、`scripts/r0_signals.py` 从快照取币，不再读 `coins.yaml`。
  - 验收：手工构造 3 币 × 3 月的快照与 bars：不在范围的月份零交易、新进入币前 2 天零交易、跌出范围的持仓正常出场；修改段 A 的数据，段 B 的特征不变；分组统计与手算一致；`tests/test_build_dataset.py` 改用快照。
  - 依赖：T0.13；建议与 T0.1 一起改 `scripts/`。
  - 完成：新增 `loader.universe_trade_mask`/`segment_ids`/`load_universe_membership`/`new_entry_symbols`；`precompute_trades`/`run_backtest`/`event_sim` 增加 `trade_mask` 与 `new_entry`；`compute_metrics` 支持 `new_entry` 分组、S10 改新进入组（笔数 < 50 只报告）；`compute_features`/`is_sample_mask` 按数据段独立计算 EWM 与滚动窗口；`build_dataset.py` 按月范围 + fp16 落盘 + `universe_rule_hash`；scripts 从快照取币并传掩码。引擎闸门、数据段隔离、新进入组单测通过。

- [x] **T0.16 补齐 bars 组装步骤（现有代码缺失）**（03 §2.3、§2.4）
  - 问题：`scripts/aggregate.py` 只输出 aggTrades 原子量，没有合并 1m K 线、补齐缺失分钟（`is_filled`）、metrics 与 fundingRate，就写入了 `data/bars/{alias}/{YYYYMM}.parquet`；而 `load_bars` 要求 K 线 11 列 + 原子量 + `is_filled`，读取会失败。P1 冒烟时的合并是临时手工做的。
  - 内容：实现“1m K 线 + 原子量 + metrics（5 分钟滞后对齐，04 §2 第 2 条）+ fundingRate（最近已结算一期）→ bars 月文件”，复用 `aggregate.py` 已有的补齐分钟函数；**逐笔缺失的分钟原子量按无成交填充（成交额 0，`p_*` 取上一分钟收盘价）并置 `is_filled = 1`**（03 §2.4）。
  - 验收：输出通过 `load_bars` 的 schema 校验；“K 线有、逐笔缺失”的分钟被置 `is_filled = 1` 且被质量报告计入逐笔缺失；metrics 对齐的未来函数测试（T 时刻的值只出现在 `close_time ≥ T + 300000` 的分钟）。
  - 完成：`assemble_bars`/`write_monthly_bars` 与 `scripts/aggregate.py` 接入 K 线、aggTrades、metrics、settled funding；写盘先临时文件再原子替换。合成 1 币 × 3 日 CLI 测试产出完整月文件并通过 `load_bars`，覆盖缺失 K 线/逐笔分列、价格回退、metrics 滞后和 funding 结算对齐。

- [~] **T0.17 质量报告按“币 × 30 天窗口”计算，并按设计口径判定**（03 §2.4、§4.2 第 7 步）
  - **口径（已决定，03 §2.4）**：以下任一条即不合格——K 线缺失分钟占比 > 1%；逐笔缺失分钟占比 > 1%；一致性误差中位数 > 2%。删除现有“`aggtrades_missing_count == 0`”的硬性要求。
  - 内容：`QualityReport` 增加逐笔缺失占比字段；按币、按 30 天窗口计算并缓存，供选币第 7 步与训练/评估使用。
  - 验收：P1 的 SOLUSDT 27 分钟缺失样例：单日窗口判不合格（约 1.9%），30 天窗口判合格（约 0.06%）；其余现有质量测试继续通过。
  - 完成：新增显式窗口 API、原子 parquet 缓存读写；合成 27 分钟缺失样例日窗口 1.875% 不合格、30 天窗口 0.0625% 合格，并验证逐笔缺失比例、2% 一致性边界和源文件/p99 缺失诊断。实际 P1 SOL 原始 bars 尚未生成，真实数据结论未运行。

- [x] **T0.19 实现 `exit_sim` 标签**（04 §7.0、§7.5，05 §1、§6.1）
  - 内容：
    - `configs/features.yaml` 增加 `label.mode`（`exit_sim` 默认 / `triple_barrier`）与 `label.y_min_r: 0.25`，同步 pydantic 模型；
    - 新增 `trade.labels.exit_sim`：入场价、初始止损、成本与 `engine.precompute_trades` 完全一致——**把入场价与成本计算抽成一个共享函数**（放在 `trade.common` 或 `trade.backtest.costs`），标签与回测都调用它，并调用同一个 `trade.common.exits.simulate_exit`，不另写出场逻辑；
    - 输出字段按 04 §7.5（`y`、`r_mult`、`exit_type`、`sl`、`ret_net`、`bars_held`、`t_exit`）；样本独特性权重与 Purge 使用 `t_exit`；
    - `build_dataset.py` 按 `label.mode` 选择标签；manifest 记录标签模式与 `exits.yaml` 哈希（出场参数改变即视为新数据集）。
  - 验收：同一组 bars 与信号，`exit_sim` 标签的出场时间、出场价、净收益与回测 `precompute_trades` 逐笔完全一致；`r_net ≥ 0.25` 才为正样本的边界测试；未来函数测试（修改 `t_exit` 之后的数据不影响该样本标签）；三重障碍模式的现有测试继续通过。
  - 依赖：T0.15（数据段）；资金费在 T0.8 后接入。
  - 完成：`LabelCfg` 增 `mode`/`y_min_r`；新增 `trade/common/entry.compute_entry` 共享入场价成本（回测与标签共用）；新增 `trade/labels/exit_sim.py` 复用 `simulate_exit` 与资金费；`build_dataset.py` 按 `label.mode` 分支；manifest 记录 `label.mode` 与 `universe_rule_hash`。逐笔一致、0.25R 边界、未来函数、三重障碍保留测试均通过。

---

## P0 工程骨架、服务器与网络

- [x] T0-1 目录、`pyproject.toml`、`requirements-{base,research,live}.lock`、README 国内镜像 —— 锁文件已按环境拆分（T0.10）
- [x] T0-2 `trade.common.config` 合并顺序与非法字段 —— `tests/common/test_config.py`
- [x] T0-3 `trade.common.logging` 脱敏 —— `tests/common/test_logging.py`
- [x] T0-4 `scripts/check_env.py`
- [x] T0-5 东京服务器开通与 SSH —— P1 报告 §9.1（腾讯云，Ubuntu 26.04，2 核 / 3.6 GB / 51 GB 可用 / 30 Mbps / 每月 1.5 TB）
- [x] T0-6 `latency_probe.py` —— 东京服务器实测（2026-10-31）：无 451/403；REST P95 ≈ 11 ms（20 次，< 50 ms）、WS `bookTicker` P95 ≈ 22 ms（60 秒 16396 样本，< 200 ms）通过；`aggTrade`/`kline_1m`/`markPrice` 连接后无数据推送，故 WS 延迟改用 `bookTicker` 流测量，其正确订阅方式留待 P5 `api_smoke.py` 核实（09 §3.3）
- [x] T0-7 同步通道 —— `pull-data` 已同时同步 `data/bars/` 与 `data/universe/`，`sync.sh` 已转 LF 可通过 `bash -n`；推代码/拉文件实测经使用者确认不再验；按阶段增量同步属 T1-9
- [x] T0-8 质量命令全部通过 —— 本机已复验（2026-10-31：`ruff format --check`、`ruff check`、`mypy --strict src/trade/{common,backtest,live}`、`mypy src/trade/dashboard`、`pytest -q` 237 通过）

## P1 数据管道与选币（服务器）

- [x] T1-0 已下架合约数据验证 —— 见 T0.14 完成报告（目录清单分类有明确口径限制）
- [x] T1-1 exchangeInfo 解析与可交易性校验 —— `trade/data/exchange.py`（第 5 步复用；`check_history` 按 T0.13 改写）
- [~] T1-2 exchangeInfo 与 leverageBracket 拉取与带时间戳快照 —— `trade/data/snapshot.py` 与 `scripts/snapshot_rules.py` 已实现；东京服务器真实拉取 exchangeInfo 成功并生成 `rules.json`（659 个 USDT 永续）；`leverageBracket` 无 API Key 返回 **401（需签名）**，已改为 401/403 时跳过、`max_leverage` 视为满足（03 §4.3，见 09 §9 核对记录）；正式拉取 leverageBracket 需 P5 带签名客户端
- [~] T1-3a 全市场日线下载（含已下架合约，窗口 25 个月）—— 已有 `loader.list_daily_symbols`/`load_daily_bars`（读 `data_root/daily/{symbol}/*.parquet|csv`，fixture 验收）与 `scripts/download_daily.py`（下载 1d K 线并按月原子转 `data/daily/{symbol}/{YYYY-MM}.parquet`，列 `open_time/close/quote_volume`；`build_vision_url` 支持 `daily` 类型；`read_klines_zip` 兼容表头/时间戳单位；fixture 验收）；缺：东京服务器真实全市场（含已下架）symbol 清单与日线下载
- [x] T1-3 选币快照 —— 真实全市场日线到位后，`select_coins.py --build --all` 已生成 M1~M24 真实快照与 `aliases.json`，`--report` 生成 `universe_report.md`；`reviewed: true`（使用者 2026-10-31 授权置 true，规则参数保持默认：`top_n=60`、上线 ≥ 60 天、2000 万 USDT 下限、`new_entry_delay_days=2`）。注意：2026 年月因流动性门槛（2000 万 USDT）入选数降至 28~48（规则按默认值正确执行，见完成报告）
- [~] T1-4 下载器与聚合 —— 已有：校验和、重试、续传、跨块合并、bars 组装（T0.16）、PyArrow/Numba 单遍实现（T1-8 fixture）；缺：真实热门币等价/内存验证、分阶段全量下载（T1-9）
- [~] T1-5 质量报告 —— 已有按币 × 30 天计算与缓存 API（T0.17 fixture）；缺：真实 bars 汇总表、实时/历史成交口径比对（03 §1.1 第 8 条，需连续录制 1 天）
- [ ] T1-6 分阶段同步到本地，`data/bars/`、`data/universe/` 与服务器 SHA256 一致
- [~] **T1-7 服务器基准测试**（03 §3、08 P1）
  - 内容：实测下载速度；1 个热门币（如 1000PEPEUSDT、DOGEUSDT）与 1 个冷门币各 1 天的解析耗时与进程峰值内存；在腾讯云控制台确认流量包是否计入入方向。
  - 验收：P1 报告中有实测表与按实测推算的全量耗时，据此修正 03 §3；T1-8 前后各测一次，记录提速倍数。
  - 部分完成：T1-8 前服务器下载实测 1000PEPEUSDT 38,202,242 B / 2.262 s、TLMUSDT 528,854 B / 0.043 s；服务器 `~/trade-venv`（uv Python 3.13.18）已含 numpy/pandas/pyarrow，本次补装 numba==0.68.0，关键栈齐全；仍缺当前项目 checkout（p1-smoke 为旧代码）与 aggTrades 样例，解析时间、聚合峰值内存及 T1-8 后复测仍阻塞；腾讯云入方向计费需控制台确认。本机合成数据测得 Numba 内核相对纯 Python 参考约 31.6×（仅本机，非服务器端到端加速倍数），不据此修正 03 §3 全量耗时估算。
- [~] **T1-8 聚合器提速改造**（03 §2.2）
  - 内容：两遍改为按日顺序的单遍处理（`whale_thr(D) = p99(D-1)` 不变；每段首日多下载 1 天只算 p99；**删除现有脚本“首日使用当日 p99”的回退**；缺日沿用最近 p99 并记入质量报告）；`pyarrow.csv.open_csv` 流式读取（32 MB 块、4 列、显式类型）；numba 分钟汇总替换现有逐行 `itertuples` 循环，保留纯 Python 参考实现；p99 精确计算（不用近似）；月文件先写临时文件再改名。
  - 验收（**全量下载的硬性前提**）：等价测试——新旧实现在 fixture 与 1 个真实热门币 × 3 天上逐字段对比，浮点误差 < 1e-9、其余完全一致（数据完整的情况；旧实现首日的当日 p99 回退不参与对比）；缺日情况单独测试；numba 与 Python 参考一致；单进程峰值内存 < 1.5 GB；现有跨块合并、方向、执行类原子量、表头/微秒兼容测试继续通过。
  - 部分完成：实现单遍日聚合、PyArrow 32 MiB/四列显式类型、Numba 与 Python 参考、精确 p99、缺日与阈值来源审计、月文件原子替换；18 项聚合测试通过。本机合成 50 万逐笔对拍 Numba 相对纯 Python 参考约 31.6×、逐字段一致（rtol=0/atol=1e-9），为本机结果。**服务器真实热门币对拍已做**：1000PEPEUSDT × 3 天（622,256 条逐笔，9.7 MB）下载 0.42s、Numba 聚合 4320 分钟 3.27s、峰值内存 52.4 MB（< 1.5 GB）；Numba 与 Python 参考逐字段一致到机器精度（`buy_quote`/`sell_quote` 最大绝对差 1.4e-9/2.4e-9，相对误差 ≈ 1.2e-15，源于大额求和的浮点累加次序，非逻辑差异）。**注意**：若按「浮点误差 < 1e-9」绝对口径，大额币的累加误差会略微超限，建议真实数据对拍用 `rtol=1e-12, atol=1e-9` 混合容差（见完成报告）。
- [ ] **T1-9 任务清单、优先级调度与分阶段同步**（03 §2.1、§2.3）
  - 内容：由 M1~M24 快照生成 `(币, 日)` 任务清单 `_jobs.parquet`（每段入选期 + 前 30 天 + 1 天）；按 调参期 → 测试段 → 封存集 顺序，阶段内入选月数多的币优先；2 进程 + 每进程 1 个预取线程（预取 2 天）、在途 zip ≤ 2 GB；失败清单不中断批次、批末重试；`_done.parquet` 断点续传；S8 所需 3 币 × 1 测试月保留原始逐笔（`keep_raw`）；`sync.ps1`/`sync.sh` 支持按阶段增量同步 `data/bars/` 与 `data/universe/`。
  - 前置：删除服务器旧冒烟产物（`~/p1-smoke`、`data/bars/C01/`，使用者已同意；执行前先列出路径）。
  - 验收：2 进程运行时服务器内存 < 3 GB；中途 `kill` 后重跑从断点继续且结果不变；同一输入重复运行输出 SHA256 一致；每阶段同步后两端 SHA256 一致。
  - 依赖：T0.13（快照）、T0.16（组装）、T0.17（质量）、T1-8（等价测试通过）、使用者已审阅快照。

> **G1 闸门未通过**：T0.13（规则化选币）、M1~M24 快照与 `reviewed: true` 已满足（2026-10-31）；真实热门币聚合等价/内存验证已通过（T1-8）。仍缺：全量合格分钟数据（T1-9 调参期 M1~M12 下载中，测试段/封存集未开始）、真实数据上的特征一致性与未来函数测试（T2-8，需真实 bars 同步回本地）。

## P2 特征与标签（本地）

- [x] T2-1 特征注册表与 37 个特征，执行类原子量禁入 —— `tests/features/`
- [x] T2-2 EWM 标准化无未来 —— `test_normalize.py`
- [x] T2-3 批量与增量一致性（fixture 2 币 × 3 天）
- [x] T2-4 预过滤、三重障碍边界、独特性权重（三重障碍为对照模式，代码保留）
- [x] T2-5 未来函数测试（fixture）
- [~] T2-6 `build_dataset.py` 与 manifest —— 已完成：按月范围与数据段（T0.15）、`exit_sim` 标签（T0.19）、fp16 落盘、`universe_rule_hash`、`exit_policy_hash`（exits.yaml 出场策略哈希，本窗口补齐）；缺：真实 bars 上的保留率与正样本率（阻塞于 P1 真实分钟数据）
- [~] T2-7 合约特征 `oi`/`funding_rate` 列名与对齐 —— 已完成：T0.16 `assemble_bars` 把 metrics `sum_open_interest`→`oi`（5 分钟滞后对齐）、fundingRate `last_funding_rate`→`funding_rate`（最近已结算一期向后对齐）合并进 bars，列名与 `trade.features` 的 `_FUTURES_COLUMNS`/`loader.OPTIONAL_BAR_COLUMNS` 一致并已测试；缺：与真实 metrics/fundingRate 数据核对（阻塞于 P1 真实分钟数据）
- [ ] T2-8 真实数据上复跑一致性测试与未来函数测试（G1 要求）

## P3 回测引擎（R0 驱动）

- [x] T3-1 R0 信号输出 `signals.parquet` —— `test_r0.py`（脚本侧的封存集与选币改动见 T0.1、T0.15）
- [x] T3-2 出场策略 `exits`（numba + Python 参考版一致）—— `test_exits.py` 14 项
- [x] T3-3 成本模型与 4 个情景、盈亏平衡滑点二分 —— `test_costs.py`
- [x] T3-4 仓位 `sizing` —— `test_sizing.py` 12 项（仓位基准为钱包余额，与现有实现一致）
- [~] T3-5 逐笔预计算 + 组合模拟 —— 缺：交易范围过滤（T0.15）、资金费（T0.8）、入场价与成本抽为共享函数（T0.19）、06 §5.4 速查表数值复现测试（08 P3 验收项，代码中不存在）；盯市权益曲线已由 T0.18 完成
- [~] T3-6 `event_sim` 与 S9 —— T0.2 全字段对拍已完成；S9 当前只覆盖组合层并有报告标注，实盘决策复用与完整 S9 验收等待 T0.3（P5 后、G4 前）
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

- [~] T5-1 通知模块（07 §8）—— **不依赖其他模块，可最先做**
  - 完成（2026-10-31）：新增 `trade.live.notifier` 包——`Notifier` 协议 + `WxPusherNotifier`/`WeComNotifier`/`FeishuNotifier`/`NullNotifier`；令牌桶限流 + 同类合并、4096 字节截断、重试 3 次（1s/3s/10s）、主通道连续失败 3 次→备份通道 `channel_down`、`redact_amounts`、同类告警 5 分钟合并、通知失败不影响交易（`send` 不抛异常）、key 不进日志；`NotifyConfig`/`ChannelConfig` 与 `configs/live.yaml` 的 `notify` 节对应；25 项单测通过。
  - 缺：真实测试群收到全部类型消息需 `WXPUSHER_*`/`WECOM_*` 密钥并在服务器实测（P5/G4）；各服务限额实测回填 07 §8.2/§8.3 待 P5。
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

- [x] **T6-1 后端骨架与安全**（10 §2、§3、§7、§9、§11）
  - 内容：`python -m trade.dashboard`；FastAPI + uvicorn；`configs/dashboard.yaml`（按 10 §11）与 pydantic 模型；`server.address` 非 127.0.0.1 拒绝启动；所有 POST 与 `/ws` 校验 `Host`/`Origin`；状态库只读 URI 连接；`/api/status` 与 `/ws` 骨架；无 `state.db` 时只开放研究接口。
  - 验收：配置为 `0.0.0.0` 拒绝启动；非法 `Host`/`Origin` 被拒；`TestClient` 冒烟通过；`mypy src/trade/dashboard` 通过。
  - 完成（2026-10-31 提前准备阶段）：`python -m trade.dashboard`、`configs/dashboard.yaml` 与 `DashboardConfig` pydantic 模型、`server.address` 回环校验（`0.0.0.0` 拒绝启动）、POST/`/ws` 的 `Host`/`Origin` 校验、`/api/status`、`/api/reports`、`/api/reports/{id}`、`/api/models`、`WS /ws`、状态库只读 URI（`mode=ro`）、无 `state.db` 时只开放研究接口均已实现；`tests/dashboard/` 冒烟与安全测试通过。
- [~] **T6-2 前端框架与视觉规范**（10 §3、§4、§5.0）
  - 内容：`static/index.html`、CSS 设计令牌（10 §4 的颜色、圆角、毛玻璃、字体、动效曲线）、左侧栏与顶栏、页面路由（Alpine.js）；把 Alpine.js、lightweight-charts、Plotly.js 下载到 `static/vendor/`，`VERSIONS.txt` 记录版本与 SHA256；`prefers-reduced-motion` 支持；所有文本转义。
  - 验收：断网可打开；布局与草图一致；SHA256 校验测试通过；事件文本中的 HTML 被转义。
  - 完成（2026-10-31 提前准备阶段）：`static/index.html` + `styles.css` 深色令牌 + Alpine.js 左侧栏 6 入口与顶栏骨架已实现；Alpine.js 3.14.8、lightweight-charts 4.2.3、Plotly.js 2.35.3 已本地化到 `static/vendor/`，`VERSIONS.txt` 记录版本与 SHA256，SHA256 校验测试通过；动态内容统一用 `x-text` 转义（不用 `x-html`）；`prefers-reduced-motion` 支持。
  - 缺：仓库无 `docs/assets/dashboard-overview.svg` 草图，"布局与草图一致"无法对照；该草图主要为 T6-4 总览页（P5 后），待补。
- [~] **T6-3 回测与研究页**（10 §5.5；只依赖 P1~P4 产物，可在 P3 完成后先做）
  - 验收：用真实报告打开，数值与 `summary.json`、`gate.json` 一致；交易范围快照与质量页可浏览。
  - 完成（2026-10-31 提前准备阶段）：回测报告列表与详情（`summary.json`/`gate.json`/`report.md`/权益曲线 Plotly/交易明细）与模型列表（`model_meta.json`）读取接口与页面已实现；空目录/缺报告有占位；fixture 报告读取测试通过。
  - 缺：真实报告数值一致性验收需 P4 产物；"数据与模型"标签页的交易范围快照与质量页（依赖 P1 快照与质量缓存）暂未实现。
- [ ] **T6-4 总览与全市场扫描网格**（10 §5.1、§6；依赖 T5-12）
  - 内容：4 张指标卡、扫描网格（按 `seq` 点亮、进度条时长取实际耗时、状态样式、悬停、点击弹出 K 线抽屉、本轮统计、再平衡淡入淡出）、权益曲线、持仓卡、挂单、事件。
  - 验收：测试网运行时点亮顺序与 `scan_state.seq` 一致；统计数与 `scan_cycles` 一致；断线重连直接显示最新状态；60 格动画不触发重排（只用 `transform`/`opacity`）。
- [ ] **T6-5 持仓与挂单、交易记录、绩效页**（10 §5.2~§5.4）
  - 验收：订单或持仓变化后 1 秒内推送刷新；K 线上开平仓与止损阶梯与 `positions`/`orders` 一致；止损单非“有效”时红色告警；交易记录筛选、分页、CSV 导出；绩效统计与手算一致。
- [ ] **T6-6 控制页**（10 §5.6、§8）
  - 验收：调参实时预览；校验不通过按钮禁用；PIN 常量时间比较、5 次错误锁定 15 分钟；一键停机调用 `kill_switch` 同一函数；修改后交易进程下一分钟生效并收到微信通知。
- [~] **T6-7 SSH 隧道与性能**（02 §5.6、10 §10）
  - 内容：`scripts/sync.ps1 tunnel`（建立隧道并打开浏览器）；服务器安全组只开放 22。
  - 验收：电脑经隧道可访问、公网 `curl` 8501 不通；看板常驻时交易进程延迟 P95 上升 ≤ 20 ms、看板 CPU < 5%。
  - 完成（2026-10-31 提前准备阶段）：`scripts/sync.ps1 tunnel` 子命令已实现（`ssh -N -L 8501:127.0.0.1:8501 tokyo` 并打开浏览器）。
  - 缺：公网 `curl` 8501 不通、看板常驻 CPU < 5% 与交易延迟 P95 上升 ≤ 20 ms 需服务器部署后实测（P7）。

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
| 15  | `scripts/aggregate.py`                         | 03 §2.2：只用前一日 p99；单遍                            | 已单遍 + PyArrow + Numba；真实数据等价/内存待服务器验证   | T1-8 部分完成                               |
| 16  | bars 组装                                      | 03 §2.3：K 线 + 原子量 + `is_filled` + metrics + funding | 已组装并通过 fixture `load_bars` 校验                     | T0.16 已完成                                |
| 17  | `quality.compute_quality`                      | 03 §2.4：三项各 1%/1%/2%，按 30 天窗口                   | 已支持分窗/缓存；真实 bars 结论未运行                      | T0.17 部分完成                              |
| 18  | 特征流水线                                     | 03 §6、04 §5：数据段独立计算                             | 在拼接后的整段数据上计算                                  | T0.15                                       |
| 19  | 组合模拟权益曲线                               | 06 §3.4、§5.1：回撤按盯市权益                            | 已按 `c_t` 逐分钟盯市（钱包余额 + 未实现盈亏）            | **已一致，无需修改**                        |
| 20  | 标签                                           | 04 §7.5：默认 `exit_sim`                                 | 只有三重障碍                                              | T0.19                                       |
| 21  | 看板                                           | 10：FastAPI + 静态前端、SSH 隧道                         | 只有 Streamlit 时期的空包；依赖仍含 streamlit、plotly     | T6-1~T6-7、T0.10                            |

## 需要使用者操作的事项（无待决策项）

1. T0.9：git 仓库已初始化并提交（已复核 2026-10-31）；如仍需修正分支名/远端请自行处理。
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
