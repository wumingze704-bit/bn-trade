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

### 阶段执行顺序

阶段按依赖和闸门推进，不是所有工作都必须严格串行执行 P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8：P0 是基础；P1 与 P2 合并满足 G1 后进入正式 P3/P4；P5 可与 P3/P4 并行开发，但上线仍须等 G3；P6 的研究页可在 P3 后、P4 产物尚未齐备时先开发，实盘页面依赖 P5 状态库；P7 必须等 G4，P8 必须等 G5。T5-1 通知模块可提前做。具体执行顺序以本文件顶部的任务顺序、各任务依赖和闸门为准，不能为了按阶段推进而跳过阻塞条件。

不再维护独立的阶段 prompt 指令，本文件（`docs/tasks.md`）是唯一任务清单。执行方式：每个**全新聊天窗口**只实施一个最早可执行的未完成任务；先读仓库规范 `AGENTS.md` 和该任务引用的设计章节，实施完成后运行 `AGENTS.md` §8 质量命令、同步文档、更新本文件任务状态，并在对话中给出中文完成报告后停止。若阶段仍有任务，在下一个新窗口按顶部「执行顺序」继续。每个新窗口都要重新读取规范、设计、任务状态和现有代码，不依赖历史聊天记录。

### T0.x 当前状态与接手说明

截至 2026-11-02，T0.1~T0.19 共 19 项中，18 项为 `[x]`；仅 T0.3（P5 后改造事件模拟器并与实盘决策对拍）尚未完成，不能宣称 T0.x 全部完成。T0.9 已由使用者完成。`[x]` 仅表示该任务列出的验收已满足；fixture 通过不代表真实数据或服务器验收通过。

#### T0.3：P5 后完成事件模拟器与实盘决策对拍

- **目前已完成**：S9 报告已注明“仅覆盖组合层（06 §3.4）”，并有对应测试；此部分随 T0.6 完成，不要重复实现或删除提示。
- **仍未完成**：P5 实盘引擎完成后、G4 开始前，改造 `trade.backtest.event_sim`，使它逐分钟调用实盘引擎实际使用的开仓过滤、追价与入场价、出场状态更新和仓位/风险决策代码；模拟器不得发网络请求，也不得继续用 `precompute_trades` 代替实盘入场与逐步出场决策。
- **开始前置**：先核实 P5 决策函数的实际接口和状态输入输出；重用现有实现，不复制出第二套交易规则。若实盘接口无法被离线调用，先报告具体边界与最小接口建议，不得自行另写一套“等价”规则。
- **验收**：构造 2 币 × 1 月的确定性 fixture；在相同信号、bars 和配置下，对快速模拟器与 `event_sim` 逐笔比较入场/出场时间、出场类型、成交价、名义价值及净盈亏，达到 06 §7 S9 的一致性要求；增加或更新测试证明实盘决策路径被调用。完成后更新回测报告中的 S9 覆盖范围，并移除“仅覆盖组合层”的阶段性提示；运行相关测试及 `AGENTS.md` §8 质量命令，通过后再标 `[x]`。
- **安全边界**：仅使用 fixture 和离线决策代码，不连测试网或生产接口、不下单、不读取封存集；此任务必须在 G4 前完成。

#### T0.17：用真实 bars 完成质量窗口验证（已完成）

- **口径与实现**：任一满足即不合格——K 线缺失分钟占比 > 1%、逐笔缺失分钟占比 > 1%、一致性误差中位数 > 2%（03 §2.4）；`compute_quality_windows()` 按「币 × 窗口」计算，`write_quality_cache()`/`load_quality_cache()` 提供原子 parquet 缓存。fixture 已证明 27/1440 = 1.875% 的单日窗口不合格、27/43200 = 0.0625% 的 30 天窗口合格。
- **真实 bars 实测（2026-11-05，本地 Windows / Python 3.13.7 / pandas 3.1.0；只读 M1~M22，未加载封存集）**：
  - `data/bars/C066`，窗口终点 `1752710400000`（2025-07-17T00:00Z）：1 天窗口（1440 分钟）逐笔缺失 4 分钟 = 0.2778%，`qualified=True`；同终点 30 天窗口（43200 分钟）= 0.0093%，`qualified=True`；两个窗口分别写缓存后再 `load_quality_cache()` 回读，逐字段一致。
  - `data/bars/C012`，窗口终点 `1736899200000`（2025-01-15T00:00Z）：1 天窗口缺失 1437 分钟 = 99.79%，`qualified=False`；同终点 30 天窗口 = 46.66%，`qualified=False`；缓存回读一致。
- **与验收样例的差异（已核查，未改阈值、未挑窗口）**：全量扫描 1803 个真实月文件后，逐笔部分缺失的币日只有两档：每币每日 4 分钟（2025-07-16 有 41 个币同时缺 4 分钟，属源归档缺口；4/1440 = 0.28%，单日窗口合格）与整日级缺口（1423~1437 分钟，单日与 30 天窗口都不合格）。真实数据里不存在 15~70 分钟的中间档，因此 27 分钟样例没有对应实例；按本任务验收条目「保留原始实测结果、核查来源与窗口边界并报告差异」处理，未伪造预期值。
- **结论**：口径、窗口 API 与缓存已在真实数据上验证；T0.17 标 `[x]` 的依据是上述真实 bars 实测与缓存回读，不是 fixture 结果。

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
  - S11 端到端已接入：`EXPERIMENTS.log` 记录 `sharpe`/`skew`/`kurtosis`/`n_obs`（`metrics.skew`/`metrics.kurtosis` 基于日收益总体矩）；`sanity.s11_deflated_sharpe` 读取日志计算 DSR；`backtest.py` 报告 N 与 DSR，DSR < 0.9 时报告首页标红警示（06 §7 S11）。

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

- [x] **T0.17 质量报告按“币 × 30 天窗口”计算，并按设计口径判定**（03 §2.4、§4.2 第 7 步）
  - **口径（已决定，03 §2.4）**：以下任一条即不合格——K 线缺失分钟占比 > 1%；逐笔缺失分钟占比 > 1%；一致性误差中位数 > 2%。删除现有“`aggtrades_missing_count == 0`”的硬性要求。
  - 内容：`QualityReport` 增加逐笔缺失占比字段；按币、按 30 天窗口计算并缓存，供选币第 7 步与训练/评估使用。
  - 验收：P1 的 SOLUSDT 27 分钟缺失样例：单日窗口判不合格（约 1.9%），30 天窗口判合格（约 0.06%）；其余现有质量测试继续通过。
  - 完成：新增显式窗口 API、原子 parquet 缓存读写；合成 27 分钟缺失样例日窗口 1.875% 不合格、30 天窗口 0.0625% 合格，并验证逐笔缺失比例、2% 一致性边界和源文件/p99 缺失诊断。真实数据抽样验证（测试段 DOGEUSDT/TONUSDT/ARBUSDT × 11 月度窗口）已运行：31/33 窗口合格，2 个边缘不合格（DOGEUSDT 首窗口逐笔缺失 13.3%、TONUSDT 下架月一致性 NaN），符合下架合约/数据源缺失的残余偏差预期（T0.14）。全量真实 bars 质量缓存已生成（`scripts/compute_quality_cache.py` → `artifacts/quality/quality_cache.parquet`）：M1~M22 入选币 237 币 × 入选月份 = 1158 窗口，合格 1105（95.4%）；53 个不合格窗口为 K 线缺失 16、逐笔缺失 37（新币数据段短 + 热门币早期数据源缺失），一致性误差 0 超限。

- [x] **T0.19 实现 `exit_sim` 标签**（04 §7.1、§7.3，05 §1、§6.1）
  - 内容：
    - `configs/features.yaml` 增加 `label.mode`（`exit_sim` 默认 / `triple_barrier`）与 `label.y_min_r: 0.25`，同步 pydantic 模型；
    - 新增 `trade.labels.exit_sim`：入场价、初始止损、成本与 `engine.precompute_trades` 完全一致——**把入场价与成本计算抽成一个共享函数**（放在 `trade.common` 或 `trade.backtest.costs`），标签与回测都调用它，并调用同一个 `trade.common.exits.simulate_exit`，不另写出场逻辑；
    - 输出字段按 04 §7.3（`y`、`r_mult`、`exit_type`、`sl`、`ret_net`、`bars_held`、`t_exit`）；样本独特性权重与 Purge 使用 `t_exit`；
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
- [x] T1-3a 全市场日线下载（含已下架合约，窗口 25 个月）—— 已有 `loader.list_daily_symbols`/`load_daily_bars`（读 `data_root/daily/{symbol}/*.parquet|csv`，fixture 验收）与 `scripts/download_daily.py`（下载 1d K 线并按月原子转 `data/daily/{symbol}/{YYYY-MM}.parquet`，列 `open_time/close/quote_volume`；`build_vision_url` 支持 `daily` 类型；`read_klines_zip` 兼容表头/时间戳单位；fixture 验收）；东京服务器真实全市场（含已下架）symbol 清单已由 T0.14 列出、日线下载已到位并据此生成 M1~M24 快照（T1-3）
- [x] T1-3 选币快照 —— 真实全市场日线到位后，`select_coins.py --build --all` 已生成 M1~M24 真实快照与 `aliases.json`，`--report` 生成 `universe_report.md`；`reviewed: true`（使用者 2026-10-31 授权置 true，规则参数保持默认：`top_n=60`、上线 ≥ 60 天、2000 万 USDT 下限、`new_entry_delay_days=2`）。注意：2026 年月因流动性门槛（2000 万 USDT）入选数降至 28~48（规则按默认值正确执行，见完成报告）
- [x] T1-4 下载器与聚合 —— 校验和、重试、续传、跨块合并、bars 组装（T0.16）、PyArrow/Numba 单遍实现（T1-8 fixture）；真实热门币等价/内存验证由 T1-8 完成（1000PEPEUSDT × 3 天，峰值内存 52.4 MB）；分阶段全量下载的服务器端验收由 T1-9 完成（2 并发内存 < 3 GB、断点续传、输出 SHA256 一致）
- [~] T1-5 质量报告 —— 已有按币 × 30 天计算与缓存 API（T0.17，真实 bars 验证）；全量真实 bars 汇总表 `artifacts/quality/quality_cache.parquet`（M1~M22 入选币 237 币 × 1158 窗口，合格 1105 = 95.4%）。**阻塞**：缺 03 §1.1 第 8 条的实时/历史成交口径比对。2026-11-05 东京服务器复测：`wss://fstream.binance.com/ws/btcusdt@aggTrade` 与 `/stream?streams=btcusdt@aggTrade` 各 15 秒均 0 条消息，`/ws/` + SUBSCRIBE 返回 HTTP 400；同期 `@trade` 15 秒 381 条、REST `/fapi/v1/aggTrades` 返回 200 且有数据。因此现在无法连续录制 1 天实时 aggTrade，正确订阅方式仍待 P5 `api_smoke.py` 实测确定。两个候选方案见「需使用者决定的事项」。
- [x] T1-6 分阶段同步到本地，`data/bars/`、`data/universe/` 与服务器 SHA256 一致 —— 2026-11-05 两端逐文件对比：本地 2099 个文件、服务器 2174 个；无“仅本地”文件；2062 个共有文件中 2025 个完全一致；37 个 `whale_thr.parquet` 原始哈希不同，唯一原因是服务器另有封存集日期行（M23~M24），按日期剔除封存集行后 246 个 alias 的非封存集内容哈希全部一致（C247~C251 五个只在封存集出现的 alias 剔除后为 0 行）。服务器多出的 75 个文件全部属于封存集（70 个月 bars + 5 个封存集专属 alias 的 `whale_thr.parquet`），本地不含任何封存集内容（红线 2）。封存集 SHA256 只在服务器侧计算，未同步、未读取内容。
- [x] T1-7 服务器基准测试（03 §3、08 P1）—— 实测表已直接写入 03 §3：热门 1000PEPEUSDT 单日 4.44 MiB / 30.1 万条，下载 0.098~0.174 s（25~45 MiB/s），解析+聚合 0.82 s，进程峰值 RSS 336 MB；冷门 VELVETUSDT 单日 0.27 MiB / 1.93 万条，下载 0.089~0.112 s（2.4~3.0 MiB/s），解析+聚合 0.67~0.69 s，峰值 RSS 255 MB（导入 pandas/pyarrow/numba 基线 155 MB）。端到端「币·月」任务 2 并发 89~94 s 完成 3 个热门币·月，进程树峰值 RSS 0.76~1.24 GB，据此外推全量 1873 个「币·月」约 16 小时。T1-8 前测只记录了下载耗时、没有解析耗时基线，故无法给出服务器端到端提速倍数（T1-8 的本机合成数据 Numba/Python 参考 31.6× 已单独记录）。**待使用者操作**：腾讯云控制台确认流量包是否计入入方向。
- [x] **T1-8 聚合器提速改造**（03 §2.2）
  - 内容：两遍改为按日顺序的单遍处理（`whale_thr(D) = p99(D-1)` 不变；每段首日多下载 1 天只算 p99；**删除现有脚本“首日使用当日 p99”的回退**；缺日沿用最近 p99 并记入质量报告）；`pyarrow.csv.open_csv` 流式读取（32 MB 块、4 列、显式类型）；numba 分钟汇总替换现有逐行 `itertuples` 循环，保留纯 Python 参考实现；p99 精确计算（不用近似）；月文件先写临时文件再改名。
  - 验收（**全量下载的硬性前提**）：等价测试——新旧实现在 fixture 与 1 个真实热门币 × 3 天上逐字段对比，浮点字段用 `rtol=1e-12, atol=1e-9` 混合容差、其余完全一致（数据完整的情况；旧实现首日的当日 p99 回退不参与对比）；缺日情况单独测试；numba 与 Python 参考一致；单进程峰值内存 < 1.5 GB；现有跨块合并、方向、执行类原子量、表头/微秒兼容测试继续通过。
  - 完成：实现单遍日聚合、PyArrow 32 MiB/四列显式类型、Numba 与 Python 参考、精确 p99、缺日与阈值来源审计、月文件原子替换；18 项聚合测试通过。本机合成 50 万逐笔对拍 Numba 相对纯 Python 参考约 31.6×、逐字段一致（rtol=0/atol=1e-9），为本机结果。**服务器真实热门币对拍已做**：1000PEPEUSDT × 3 天（622,256 条逐笔，9.7 MB）下载 0.42s、Numba 聚合 4320 分钟 3.27s、峰值内存 52.4 MB（< 1.5 GB）；Numba 与 Python 参考逐字段一致到机器精度（`buy_quote`/`sell_quote` 最大绝对差 1.4e-9/2.4e-9，相对误差 ≈ 1.2e-15，源于大额求和的浮点累加次序，非逻辑差异）。等价测试验收口径已由绝对 `1e-9` 改为 `rtol=1e-12, atol=1e-9` 混合容差（大额求和项用相对容差），据此真实数据对拍通过。
- [x] **T1-9 任务清单、优先级调度与分阶段同步**（03 §2.1、§2.3）
  - 内容：由 M1~M24 快照生成 `(币, 月)` 任务清单（每段入选期 + 前 30 天预热 + 首日前一日 p99）；按 调参期 → 测试段 → 封存集 顺序，阶段内入选月数多的币优先；线程池 `--workers`（默认 1，服务器 2）+ 每任务独立 raw 工作区，在途 zip = 2 × 单任务工作区（实测 605 MB ≤ 2 GB）；失败清单不中断批次、批末重试一轮；以目标月 bars 是否存在断点续传；S8 所需 3 币 × 1 测试月保留原始逐笔（`keep_raw`）；`sync.ps1`/`sync.sh` 支持按阶段增量同步 `data/bars/` 与 `data/universe/`。
  - 前置：删除服务器旧冒烟产物（`~/p1-smoke`、`data/bars/C01/`，使用者已同意；执行前先列出路径）——已在使用者同意后完成。
  - 验收（2026-11-05，东京服务器实测；样本为 1000PEPEUSDT 2025-10/11/12 三个「币·月」、`--phase test`、临时目录 `/tmp/t1_9`）：
    1. **内存**：`--workers 2` 跑 3 个热门币·月，进程树（父进程 + 聚合子进程）峰值 RSS **1240 MB**（另一轮 757 MB），服务器总内存 3.6 GB → < 3 GB；任务工作区峰值 **605 MB** → < 2 GB。
    2. **断点续传**：`--workers 1` 跑到 60 s 时 `kill -9` 整个进程组（此时已产出 `C002/202510.parquet`）；重跑同一命令只处理剩余任务，最终三个输出文件（含 `whale_thr.parquet`）的 SHA256 与一次性跑完的干净运行**逐字节相同**。
    3. **可重复性**：两次干净运行（各 3 个热门币·月、`--workers 2`）全部输出 SHA256 **完全一致**。
    4. **批末重试**：`_run_batch` 汇总失败任务，批次结束时对失败清单重跑一轮，仍失败才以返回码 1 结束；新增 3 个单测（失败不中断、只重试失败项、空清单）。
  - 为通过验收所做修复：
    - 每个「币·月」任务改用独立 raw 工作区 `data/raw/work/{alias}-{YYYY-MM}/`，并显式下载上一自然月 1m K 线与本月首日前一日 aggTrades。此前任务依赖同币其他月份任务残留的归档，输出随调度顺序变化（实测同一币 2025-10~12 两次运行中 `202511` 有 12~348 分钟的 `whale_buy_quote` 不同）。
    - `whale_thr.parquet` 的读改写加 `fcntl.flock` 排他锁（`data/raw/locks/{alias}.lock`），避免同币相邻月份并发覆盖。
  - **遗留缺陷已修复（方案 A，2026-11-05）**：修复前生成的 bars 中，多数月文件首日缺少大单阈值（服务器 1458 个「币·月」符合条件，含封存集月份）。一次性运维脚本 `scripts/repair_first_day_whale.py`只重下「首日前一日 + 首日」两天逐笔，用同一套 `aggregate_aggtrades_day()` / `assemble_bars()` 重算首日并原子替换月文件，写盘前校验首日以外的行完全不变；脚本按 `base.yaml` 固定只处理 M1~M22。服务器实跑：M1~M22 完成 1347 个「币·月」、失败 0，事后复扫剩余待修复 0。验证：C001 2024-09、C066 2025-07（后者首日以外还有 4 个逐笔缺失分钟）两个样例的修复结果与修复后流水线重新生成的文件 **SHA256 逐字节相同**；首日 `whale_thr` 缺失分钟降为 0、`whale_buy_quote` 恢复非零。
  - **过程问题（如实记录）**：首次全量运行时脚本尚未排除封存集月份，SUIUSDT 2026-08、2026-09 两个月文件在发现问题前被处理（只做聚合与写盘，未查看任何封存集结果、未用于调参）；已立即中止并给脚本加上M1~M{months-holdout_months} 硬限制后重跑。若需严格保证封存集新鲜度，可在 G3 `--final` 前按 08 §3 重新生成。
  - **待办**：修复后的服务器月文件需同步到本地（本地仍为修复前版本），跨境链路实测约 22 KB/s，约 5 GB 需数十小时，建议夜间执行 `powershell -File scripts/sync.ps1 pull-data`（2026-11-06 已修复：`pull-data` 现会排除封存集 M23~M24 的 bars，不拉到本地，红线 2）；同步完成后需重建 P2 数据集 `ds_20261102`（特征 #23~#25、#27 的取值会变化）。使用者已决定（2026-11-06）跳过重跑 T4-3 第 1 步（「三个月六组」），冻结的出场/止损参数保持不变，重建后直接从 T4-3 第 2 步继续。
  - 依赖：T0.13（快照）、T0.16（组装）、T0.17（质量）、T1-8（等价测试通过）、使用者已审阅快照（均已满足）。

> **G1 闸门状态（2026-11-05）**：T0.13（规则化选币）、M1~M24 快照与 `reviewed: true`、T1-8（真实热门币聚合等价/内存）、T2-8（真实数据一致性与未来函数测试）、T0.17（真实 bars 质量窗口验证 + 全量质量缓存 237 币 × 1158 窗口、合格 95.4%）、T1-6（两端逐文件 SHA256 一致，封存集除外）、T1-7（服务器基准并据此修正 03 §3）、T1-9（2 并发内存、断点续传、SHA256 一致、批末重试）均已满足。**仍不满足、G1 未通过**：(1) T1-5 的实时/历史成交口径比对因 aggTrade 行情流无数据而阻塞，需在 P5 完成或改用简化比对，待使用者选择；(2) 现有 bars 首日大单阈值缺失已按方案 A 在服务器修复（M1~M22 共 1347 个「币·月」、样例与重新生成结果逐字节相同），但修复后的文件尚未同步到本地（跨境链路约 22 KB/s），本地数据与 P2 数据集仍是修复前版本。G1 是否通过由使用者确认；封存集 M23~M24 只在服务器保留，未加载（红线 2）。

## P2 特征与标签（本地）

- [x] T2-1 特征注册表与 37 个特征，执行类原子量禁入 —— `tests/features/`
- [x] T2-2 EWM 标准化无未来 —— `test_normalize.py`
- [x] T2-3 批量与增量一致性（fixture 2 币 × 3 天）
- [x] T2-4 预过滤、三重障碍边界、独特性权重（三重障碍为对照模式，代码保留）
- [x] T2-5 未来函数测试（fixture）
- [x] T2-6 `build_dataset.py` 与 manifest —— 已完成：按月范围与数据段（T0.15）、`exit_sim` 标签（T0.19）、fp16 落盘、`universe_rule_hash`、`exit_policy_hash`（exits.yaml 出场策略哈希，本窗口补齐）；真实 bars 保留率/正样本率已运行：`ds_20261102` 237 币，平均保留率 0.191（0.0022~0.2152）、平均正样本率 0.2363（0.0702~0.2873）、总样本 9,484,415；仅 C170（保留率 0.0022，数据段短、样本仅 91）与 C027（正样本率 0.0702，低波动稳定币）两个别币超区间并已告警
- [x] T2-7 合约特征 `oi`/`funding_rate` 列名与对齐 —— 已完成：T0.16 `assemble_bars` 把 metrics `sum_open_interest`→`oi`（5 分钟滞后对齐）、fundingRate `last_funding_rate`→`funding_rate`（最近已结算一期向后对齐）合并进 bars，列名与 `trade.features` 的 `_FUTURES_COLUMNS`/`loader.OPTIONAL_BAR_COLUMNS` 一致；特征 #34/#35/#36 分别读 `oi`/`oi`/`funding_rate`。**逐值核对（2026-11-05，东京服务器，调参期 2025-03-10~12，2 币 × 3 天 = 每币 4320 分钟）**：`scripts/verify_futures_alignment.py` 用 `numpy.searchsorted` 独立重算（不复用 `_align_metrics`/`_align_funding`），1000PEPEUSDT（C002）与 ARBUSDT（C023）的 `oi` 与 `funding_rate` **不一致分钟均为 0**、`oi` 无 NaN。样例（C002）：metrics T=1741564800000（原始 `sum_open_interest`=15,142,809,658）→ 可用时刻 1741565100000（T+300000）→ 首次出现在 `close_time=1741565159999` 的分钟，bars 值与原始一致；T=1741694100000（12,884,566,644）→ 1741694400000 → 首分钟 1741694459999。资金费（C002）：结算 1741593600000 的费率 -2.3e-07，结算前最后一分钟仍为上一期 -0.00012236，结算后首分钟才变为 -2.3e-07；C023 同样（-0.00020894 → -9.906e-05）。未发现提前出现或未来值，无需改代码、无需重建数据集。
- [x] T2-8 真实数据上复跑一致性测试与未来函数测试（G1 要求）—— `scripts/verify_features.py`：调参期真实数据 3 币 × 3 天，批量 vs 增量最大误差 9.3e-13~3.9e-12（< 1e-6）、未来函数测试通过；前置补齐 BTCUSDT 1m bars（`scripts/download_btc.py`，G1「BTCUSDT 数据齐全」）

## P3 回测引擎（R0 驱动）

- [x] T3-1 R0 信号输出 `signals.parquet` —— `test_r0.py`（脚本侧的封存集与选币改动见 T0.1、T0.15）
- [x] T3-2 出场策略 `exits`（numba + Python 参考版一致）—— `test_exits.py` 14 项
- [x] T3-3 成本模型与 4 个情景、盈亏平衡滑点二分 —— `test_costs.py`
- [x] T3-4 仓位 `sizing` —— `test_sizing.py` 12 项（仓位基准为钱包余额，与现有实现一致）
- [x] T3-5 逐笔预计算 + 组合模拟 —— 交易范围过滤（T0.15）、资金费（T0.8）、入场价与成本抽为共享函数（T0.19）、盯市权益曲线（T0.18）已完成；06 §5.4 速查表数值复现测试由 `tests/backtest/test_sizing_checkup.py` 覆盖（6 种情形误差 < 5%），T3-5 完成。
- [~] T3-6 `event_sim` 与 S9 —— T0.2 全字段对拍已完成；S9 当前只覆盖组合层并有报告标注，实盘决策复用与完整 S9 验收等待 T0.3（P5 后、G4 前）
- [x] T3-7 S8 逐笔重放（服务器，3 币 × 1 测试月），含改单 1 秒旧止损窗口（原始数据由 T1-9 保留）—— **代码与 fixture 单测完成，真实测试段运行留待正式回测**：
  - 实现：`trade.backtest.replay`（`replay_trade` / `replay_month` / `compare_s8` / `s8_payload`，逐笔重放复用 `trade.common.exits` 的 `floor_tick` / `ceil_tick` / `initial_stop` / `take_profit_price` / `update_stop_after_bar`，收盘新止损 1 秒后才生效，窗口内旧止损仍有效）；`scripts/s8_replay.py`（服务器入口，写 `s8_replay.json` / `s8_replay.parquet` 并回填 `gate.json`）；`scripts/backtest.py` 的 S8 改为读 `s8_replay.json`（`--s8-result`），不再是硬编码 `not_run`；`scripts/download.py` 新增 `--symbols`。
  - 单测：`tests/backtest/test_replay.py` 14 项（同根 K 线逐笔顺序、1 秒旧止损窗口、跳空与补齐分钟、逐笔级不比 K 线级乐观、净收益差异分母、判定阈值与状态文本、日度归档读取、按月装载窗口、`gate.json` 回填、`scripts/backtest.py` 状态读取、`scripts/s8_replay.py` 端到端 fixture 运行）。
  - 原始数据：服务器保留 2026-02（测试段 M17）XRPUSDT / ARBUSDT / STRKUSDT 的日度 aggTrades 共 84 个（约 224 MB，`data/raw/aggtrades/{alias}/`）；选取规则与重下命令见 03 §2.2，选定后不得更改。
  - **未运行**：真实测试段的 S8（按任务要求不在本次运行），正式回测后执行 `python scripts/s8_replay.py --run-id <run_id> --signals <signals.parquet> --rules <rules.json>`。
- [x] T3-8 指标与报告、`gate.json`、`EXPERIMENTS.log` —— 新分组（T0.15）已随 T0.15 完成；T0.4、T0.5（S11 端到端）、T0.6、T0.11、T0.12、T0.18 已完成；`sizing_sweep`（06 §5.5，`trade.backtest.sweep`，写 `sizing_sweep.parquet/md/png`）与 `equity.png` / `monthly.png` / `by_symbol.png` / `p_calibration.png`（`trade.backtest.charts`）已实现；`gate.json` 新增 `signals_kind` 与 S2/S3 回填（`--s2-result` / `--s3-result`）；fixture 测试通过。
- [x] **T3-9a 调参期 R0 毛期望早期检查**（M1~M12 数据到本地后立即做）
  - 内容：在 M1~M12 上跑 R0（阈值事先固定），统计成本前后的单笔期望与笔数，与往返成本约 15 bp 比较。
  - 约束：只用调参期，不碰测试段与封存集；结果**只用于决定是否继续投入**，不得据此修改 R0 阈值或任何参数。
  - 完成（`scripts/tuning_r0_check.py`，抽样前 40 币、控制内存）：调参期 11434 笔，成本前单笔期望 **−7.92 bp**、手续费 9.92 bp、成本后 **−17.85 bp**（往返成本参考约 15 bp）。结论：R0 规则策略在调参期成本前后均为负期望，属预期（01 §1 风险），模型 M0/M1 需显著优于 R0 才有意义。
- [ ] T3-9 真实数据上跑一次 R0 全流程回测（依赖 G1、T0.1、T0.15）

## P4 模型训练与正式回测（本地，依赖 G1；T4-3 第 1 步可在调参期数据到位后提前开始）

> **P4 就绪性结论（2026-11-06 复审）**：T4-0~T4-7 的代码、脚本、配置与测试均已实现并通过（T4-3 第 2 步、T4-4、T4-6 的**正式运行**因数据/GPU 未到位保留 `[~]`，代码本身已就绪）；**P4 正式回测（T4-8）尚不具备启动条件**，阻塞项与 G1 相同：(1) T1-5 实时/历史成交口径比对阻塞；(2) 服务器修复后的 bars 未同步到本地、P2 数据集 `ds_20261102` 仍是修复前版本。使用者已决定（2026-11-06）跳过「三个月六组」（T4-3 第 1 步 6 组 × 验证段 M10~M12 的重跑），冻结的出场/止损参数（`H_max=120`、`k_trail=1.5`、`k_sl=2.5`、`sl_min=1.0%`、`sl_max=2.5%`）保持不变；数据同步并重建 `ds_20261102` 后，直接从 T4-3 第 2 步（12 组超参搜索）继续，无需重跑第 1 步。

> **P4 诊断结论（2026-11-07，调参期）**：`ds_20261107` 上 M0 与 TCN 的验证集表现几乎相同（ROC-AUC ≈ 0.61、PR-AUC ≈ 0.22、Top5% 精确率 ≈ 0.27），而单特征 `atr_pct` 的 ROC-AUC 已达 0.60——即模型只学到了「波动率/振幅」这一个弱信号，且该信号在扣掉手续费/滑点/追踪止损后不足以让净收益为正（第 2 步第 1 组 PF=0.8925、净收益 −1218.01）。这不是训练或对齐 bug（特征↔标签按 `open_time` 对齐已核对无误），而是当前「分钟级 `exit_sim` 标签 + 现特征集」的可学 alpha 很弱。下一步仍按既定流程跑完 12 组 → 5 折 → 正式回测，G2 很可能会给出负结论；是否回头改特征/标签（04、05 级）属设计决策，需使用者拍板，不在本任务范围内擅自改动。

> **P4 正式运行进展（2026-11-07）**：服务器修复后的 bars 已同步到本地（逐文件大小与服务器一致；本地已无 M23~M24 任何文件，`BTCUSDT` 的 202608/202609 已删除，红线 2 结构性成立），并用冻结参数重建数据集。为跑通正式流程，修正了五处此前只在 fixture 上验证、真实数据下必然出错的缺陷：(1) 搜索/5 折训练/非 `--final` 回测只加载窗口内有入选月份的币（此前会把只在封存集首次入选的 C247~C251 纳入训练集，因缺文件崩溃）；(2) 搜索启动前校验数据集文件齐全，缺文件时在任何试验登记之前失败；(3) 温度缩放改用 5 种子集成后的平均 logit（此前拼接各种子 logit 与单份 `val_y` 形状不匹配）；(4) 出场分钟缺 aggTrades（`p_1s` 为空）时按该根 K 线收盘价扣成本出场（此前逐笔净盈亏为 NaN，见 06 §3.3）；(5) 同一缺口会使标签 `r_mult` 为 NaN，进而让训练权重整片变 NaN、所有组的 PF/净收益完全相同且单组空转数小时；`trade.labels.exit_sim` 已加同一回退，`trade.models.train` 增加「训练前校验 `r_mult` 无 NaN」与「损失非有限立即报错」两道守卫。最终数据集为 `ds_20261107`（237 币、9,484,415 样本、标签零 NaN、特征哈希 `6437b53b…`；加权正样本率 0.1787）。`EXPERIMENTS.log` 中 11 行由上述缺陷产生的无效记录已移除（备份 `..._guard_fix`、`..._nan_fix`、`..._label_nan_fix`），不计入 30 组预算；当前登记 19 行（18 组第 1 步 + 第 2 步第 1 组），第 2 步剩余 11 组。搜索已支持中断续跑（按 `run_id` 跳过已登记前缀并把指标并入最终选择，05 §6.1）。第 2 步第 1 组实测：`t4_3b_L30_h32_bce` PF=0.8925、净收益 −1218.01 USDT、1350 笔、θ=0.40（验证段 M10~M12），数值正常但与第 1 步的 M0 结果相比无优势，最终选优按 05 §6.1 规则（无正收益组时取 PF 最高者并在报告中说明）。

- [x] T4-0 安装 CUDA 版 torch —— 用 T0.10 的 `requirements-research.lock`；已安装 torch 2.14.1+cu126，`cuda_available: true`（RTX 3060 Laptop 6GB，驱动 581.32）
- [x] T4-1 数据集：fp16 memmap 加载入选月份、GPU 常驻（不足时 pinned_cpu，05 §5.1）、索引切片、币种均衡；窗口不跨数据段；GPU/CPU 一致单测；实测显存写入报告 —— 已完成 `trade.models.dataset.WindowDataset`：索引切片（样本只存 `(sym_id, row_idx, y, r_mult, w, open_time)`、不预先展开）、币种均衡（1/该币样本数 归一化）、窗口不跨数据段、补齐分钟占比 > 20% 丢弃；新增入选月份过滤（`months`，按月份裁剪读取，含 30 天预热）、`.npy` fp16 memmap 缓存（`cache_dir` + `np.load(mmap_mode="r")`）、`data_placement=pinned_cpu` 退路与 `collate` 批量 gather、`peak_memory_mb()` 显存实测；`tests/models/test_dataset.py` 13 项含 GPU/CPU 一致与 memmap 回读。**真实数据显存实测（2026-11-05，RTX 3060 Laptop 6GB，`ds_20261102`，4 个训练组币 × 2025-01~12）**：225,213 个样本，`torch.cuda.max_memory_allocated()` 峰值 **92.5 MB**（reserved 104 MB），按 48 币 × 21 个月外推约 2.0 GB，低于 05 §5.1 的 5 GB 目标。
- [x] T4-2 TCN 因果性单测 —— `trade.models.tcn.TCNModel` 已实现（因果 TCN 残差块 × 4 + 注意力池化 + 双头），`tests/models/test_tcn.py` 3 项通过（前向形状、因果性：改 t+1 之后不变、感受野 ≥ L）
- [~] T4-3 调参期 M1~M12 两步搜索（05 §6.1）—— 第 1 步完成：诊断发现止损过紧（sl_min=0.5% 假突破扫损 41.8%、每笔亏 62bp），放宽止损后第 3 轮（验证段 M10~M12、sl_max=2.5%）6 组全部 PF>1 且净收益>0（组 1~6 PF：1.473/1.033/1.026/1.001/1.039/1.087，净收益 +1322/+104/+262/+16/+117/+946）；按「PF 最高」选组 1（H_max=120、k_trail=1.5、k_sl=2.5、sl_min=1.0%、sl_max=2.5%）并冻结进 features.yaml。第 2 步脚本 `scripts/tuning_models.py` 已实现：12 组网格（L ∈ {30,60,120} × hidden ∈ {32,64} × loss ∈ {bce,focal}，dropout=0.2、time_decay 关闭）、预算校验（30 − 已登记 = 12）、逐组登记 `EXPERIMENTS.log`（含失败组）、按 05 §6.1 自动选择并写回 `train.yaml`；`tests/test_tuning_models.py` 9 项与 `--dry-run`（显示 12 组）通过。**第 2 步正式搜索进行中**：第 1 组 `t4_3b_L30_h32_bce` 已完成（PF=0.8925、净收益 −1218.01、1350 笔、θ=0.40），其余 11 组在 `ds_20261107` 上续跑；脚本在真实数据上暴露的五处缺陷已修正（见上方「P4 正式运行进展」）。`EXPERIMENTS.log` 的 `n_trades`/`threshold` 字段由 `append_experiments_log` 固定 schema 决定，当前只落盘 PF/净收益/笔数（θ 与失败标记不落盘）。
  - 第 1 步：6 组出场与止损参数，每组重建 `exit_sim` 标签 → 训练 M0 → 验证段选阈值 → `base` 情景 PF 与净收益，选定后冻结 `exits.yaml` 与 `k_sl`；
  - 试验登记：第 1 步实际运行 18 组（3 轮 × 6 组，含失败的组），已逐组补登 `artifacts/reports/EXPERIMENTS.log`（`--backfill-log`，可重复执行不重复登记）；18 组计入 05 §6.1 的 30 组预算，剩余 12 组留给第 2 步。历史轮次当时未打印每组的笔数，`n_trades` 记 null（run_id 带 `_n_missing`），不估算。
  - 组 1 体检（只报告、不参与选择）：`scripts/tuning_exits.py --checkup --checkup-group 1 --theta 0.34` 用冻结参数复现组 1 的 M0，在验证段 M10~M12 跑 `base`/`stress`/`hell` 与盈亏平衡滑点，并把「复现」行写入 `EXPERIMENTS.log`；本次真实运行在标签重建阶段被中止（使用者要求取消），未产生结果，不写未运行的数字。
  - 第 2 步：固定第 1 步结果，模型超参按 05 §6.1 的候选范围；
  - 两步合计 ≤ 30 组，全部登记 `EXPERIMENTS.log`（含失败组）；`y_min_r` 不参与搜索。
- [x] T4-4 M0 / M1 / M2 训练、早停、bf16、种子集成；同种子两次 PR-AUC 差 < 0.002 —— `trade.models.train`（`train_ensemble` / `train_lgbm_baseline`）、`trade.models.lstm.LSTMModel`；AdamW + 1 epoch 预热 + 余弦、bf16 autocast（CUDA）、梯度裁剪、验证 PR-AUC 早停与最优权重、5 种子集成、`train_log.csv` 全字段（含显存峰值）；fixture 同种子两次 PR-AUC 差 < 0.002（`tests/models/test_train.py` 8 项）。**正式训练未运行**。
- [x] T4-5 校准与阈值（只在各折验证集选），`model_meta.json` 字段齐全（含 `label.mode`、`y_min_r`、`exit_policy`、`universe_rule_hash`）—— `trade.models.calibration`（温度缩放、ECE、θ 网格 0.30~0.80、日均 ≥ 3 笔约束、PF 保留 0.9 的最小平移阈值、`write_model_meta` 全字段）；`tests/models/test_calibration.py` 7 项。
- [x] T4-6 5 折 Walk-Forward + F_final，测试段 `signals.parquet` 含当月范围内全部币 —— `trade.models.walkforward`（`build_folds` 按 03 §6、Purge/Embargo、`run_fold`、`run_final`）+ `scripts/train_walkforward.py`（`--dry-run` 已验证折划分 F1~F5 与 F_final）；fixture 2 币训练 / 3 币测试信号字段与目录结构测试通过。**真实 5 折未运行**；`scripts/train_walkforward.py` 已同步「只加载窗口内有入选月份的币」与「温度缩放用集成平均 logit」两处修正。
- [x] T4-7 产物加载校验：特征哈希、出场策略不一致时报错 —— `trade.models.artifact.load`：特征列表、`feature_hash`、`exit_policy` 哈希、`label` 参数、`market`、`universe_rule_hash` 逐项校验，不一致抛带上下文的 `ArtifactError`；`tests/models/test_artifact.py` 6 项（一致加载、特征哈希/列表/出场策略/market 不一致、缺文件）。
- [ ] T4-8 正式回测 S1~S11，G2 自动判定（含统计显著性）
  - 实现方案（已定，待 5 折训练完成后执行）：`signals.parquet` 增加可选列 `theta`（每折用自己验证月选出的阈值），`precompute_trades` 按行阈值判断、缺列时回退 `backtest_cfg.threshold`；`run_backtest` 增加 `threshold_delta`，S4 阈值扰动改为在每行 θ 上加/减 0.04，而不是用单一全局 θ 覆盖 5 折。5 折信号按月份拼接为一份文件后一次回测覆盖 M13~M22（03 §6），`--model` 仅用于 `model_id` 与元数据。
- [ ] T4-9 G3 封存集，`--final` 只运行一次（**需使用者在场确认**）

## P5 实盘引擎（测试网，服务器）

> 可与 P3/P4 并行，用 R0 或随机信号联调；上线必须等 G3 通过。

- [~] T5-1 通知模块（07 §8）—— **不依赖其他模块，可最先做**
  - 完成（2026-10-31）：新增 `trade.live.notifier` 包——`Notifier` 协议 + `WxPusherNotifier`/`WeComNotifier`/`FeishuNotifier`/`NullNotifier`；令牌桶限流 + 同类合并、4096 字节截断、重试 3 次（1s/3s/10s）、主通道连续失败 3 次→备份通道 `channel_down`、`redact_amounts`、同类告警 5 分钟合并、通知失败不影响交易（`send` 不抛异常）、key 不进日志；`NotifyConfig`/`ChannelConfig` 与 `configs/live.yaml` 的 `notify` 节对应；25 项单测通过。
  - 缺：真实测试群收到全部类型消息需 `WXPUSHER_*`/`WECOM_*` 密钥并在服务器实测（P5/G4）；各服务限额实测回填 07 §8.2/§8.3 待 P5。
  - 进展（2026-11-05，东京服务器实测）：新增 `scripts/notify_smoke.py`（逐类事件真实发送 + 突发限流观察，密钥只从环境变量读）。WxPusher 主通道**真实送达 10 类事件（start/open/stop_move/close/sizing/alert/halt/heartbeat/daily/stop）+ 突发 6 条，全部 `code=1000`，失败 0**；单条耗时 170 ms ~ 6.4 s；16 条在约 30 秒内未见服务端限流拒绝（已回填 07 §8.2）。未配置密钥的通道降级为 `NullNotifier`（实测输出 `WeCom 缺少 webhook key，通知降级为 no-op`），已写入 07 §8.4。
  - 缺：**企业微信备份通道未运行**——使用者决定不配置 `WECOM_WEBHOOK_KEY`，因此「双发」「主通道连续失败 3 次→备份通道告警」「心跳只发企业微信」三项无真实演练（代码与单测已覆盖，缺真实链路）；07 §8.3 的限额实测同样未运行。

- [~] T5-2 币安客户端 `ExchangeClient`、签名对照官方示例、`api_smoke.py --env testnet`；SDK 版本锁定
  - 完成：`trade.live.exchange`（`endpoints`/`models`/`signing`/`client`/`ws`）。HMAC-SHA256 用官方文档示例 Key+载荷单测校验一致；Ed25519 用 `cryptography` 往返单测（本地无 `cryptography` 时 skip）；`ExchangeClient` 以 httpx 实现 REST 封装（ping/time/exchangeInfo/klines/aggTrades/bookTicker/commissionRate/leverageBracket/账户/订单/algoOrder/userTrades/income/listenKey）；`scripts/api_smoke.py --env {testnet|mainnet}`；SDK 版本 `binance-sdk-derivatives-trading-usds-futures==18.2.0` 已在 `requirements-live.lock`。
  - 缺：官方 SDK 实际封装未启用（本地无 SDK，服务器联调时接入）；`api_smoke.py` 真实跑通、算法条件单是否被测试网支持需在服务器实测（09 §7）。
  - 进展（2026-11-05，东京服务器实测）：`scripts/api_smoke.py --env testnet` 公开部分真实通过——`ping`/`time`/`exchangeInfo`/`klines`/`aggTrades`/`bookTicker` 全部 `[OK]`；缺 `BINANCE_TESTNET_API_KEY` 时签名与下单部分打印 `[SKIP]`（脚本改为不直接 `exit`，并修复了公开检查调用不存在的 `client.time()` 的缺陷）。新增免密钥的 `--ws-check` 行情冒烟（见 T5-4）。
  - 缺：签名只读接口（`account`/`positionRisk`/`commissionRate`/`leverageBracket`/`listenKey`）与测试网订单生命周期**未运行**——需要使用者把测试网密钥写入服务器 `/etc/trade/env`（权限 600，见 02 §7）后重跑；算法条件单支持性同随该次运行核实（09 §7）。
  - 阻塞原因（2026-11-05 查明）：使用者最初在**现货测试网** `testnet.binance.vision` 申请 key，合约接口返回 `-2015`；判别证据：同一把 key 打 `/api/v3/account`（现货测试网）`HTTP 200`、打 `/fapi/v2/account`（合约测试网）`401 -2015`。需改用合约测试环境的 key（09 §5.1 已补充说明）。
  - 完成（2026-11-05，东京服务器实测）：使用者改用 `demo.binance.com` 的 **RSA key** 后，`testnet.binancefuture.com` 全部签名接口通过。实现 **RSA 签名**（`rsa_signature`，用已锁定的 pycryptodome，`private_key_kind` 按 PEM 头判别 RSA/Ed25519；`ExchangeClient` 改为 api_secret/rsa_private_key_pem/private_key_hex 三选一互斥）。`api_smoke.py --env testnet` **全绿**：公开 6 项 + 签名 5 项（account/positionRisk/commissionRate/leverageBracket/listenKey）+ 订单生命周期（IOC 开仓 FILLED → 挂止损 → 挂止盈 → 撤止盈 → 市价平仓 → 确认仓位 0 → 撤止损）。**算法条件单真实参数实测**：`POST /fapi/v1/algoOrder` 用 `algoType=CONDITIONAL` + `type=STOP_MARKET`（`algoType=STOP_MARKET` 返回 `-4500`）；查询 `GET /fapi/v1/openAlgoOrders`（`/fapi/v1/algoOpenOrders` 不存在）；撤单 `DELETE /fapi/v1/algoOrder`。测试网**支持**算法条件单（09 §7）。修复 3 处真实缺陷：公开检查调用不存在的 `client.time()`、`book_ticker` 返回 list、tick 取整浮点尾差 `-1111`（新增 `floor_to_tick`）。
  - 缺：官方 SDK 实际封装仍未启用（`binance-sdk-derivatives-trading-usds-futures==18.2.0` 在 lock 中，客户端用 httpx 自研，后续按 09 §5.2 决定是否切换）。

- [~] T5-3 启动流程：费率核对、杠杆分层、5x、30 天预热
  - 完成：`trade.live.engine` 启动校验纯函数（时钟 >500ms 退出、`fee_reject_live` 实际吃单费率 > 配置 × 1.2 拒绝 live）、`LiveEngine` 组装入口、`trade.live.config`（LiveConfig/load_sizing_config/load_exits_config）。
  - 缺：完整 10 步启动串联、模型加载接入（`trade.models.artifact.load` 已由 T4-7 实现，尚未接入 `LiveEngine` 启动流程）、`paper` 模式真实启动到订阅行情需服务器。
- [~] T5-4 行情：多连接、23 小时轮换、缺口回补、延迟监控、原子量录制
  - 完成：`trade.live.exchange.ws`（WsConnection + kline/aggTrade/bookTicker 解析单测）；`trade.live.bar_builder`（缺口判定、分钟完整判定、原子量聚合单测）；`trade.live.health`（LatencyMonitor R10 持续 10 秒、OrderErrorMonitor R11、WeightUsage 09 §6）；`trade.live.market_data`（订阅币种计算、23h 轮换、listenKey 30min 续期）。
  - 完成（2026-11-05，东京服务器实测）：`MarketDataFeed`（组合流消费、断线指数退避重连 1s→30s、23 小时先建新连再关旧连）、流名称构造（`kline_streams`/`agg_trade_streams`/`book_ticker_streams`）、`KlineGapFiller`（REST `klines?startTime&endTime` 回补已收盘缺口）、`missing_minutes`/`reconnect_delay_ms`；`scripts/api_smoke.py --ws-check` 免密钥订阅冒烟。实测：测试网三流组合流 60 秒 1278 条、`@kline_1m` 单流 65 秒 120 条；`iptables` DROP 断网 30 秒**未断链**（TCP 重传 + ping/pong 未超时，重连 0 次）、断网 100 秒**断链后自动重连 1 次并回补 1 根已收盘 K 线**；同一演练还暴露并修复了「重连握手失败会逃出 `run()`」的缺陷（新增单测 `test_feed_reconnects_after_handshake_failure_and_reports`）。
  - 缺：原子量 parquet 每小时落盘未接；单连接订阅数上限的拆分（09 §6）未实测；**正式网 `@kline_1m`/`@aggTrade` 无推送**（09 §3.3、§9），`shadow`/`live` 的行情来源需使用者决定。

- [~] T5-5 月度再平衡（07 §3.5）
  - 完成：`trade.live.market_data.Rebalancer` 复用 `trade.data.universe.build_universe()`+`assign_aliases`；`new_entrants`/`exited_symbols`/`rebalance_due` 纯函数单测。
  - 缺：全市场日线拉取权重实测、60 币满负荷容量实测需服务器；**已解决（2026-11-04 使用者决定）**：交易范围 = 月度选币 `U(R)` ∪ 动态候选池（涨幅榜前 20，`candidate_pool`），取并集、范围最大；`market_data.trading_scope`/`top_gainers` 已实现并单测。
- [~] T5-6 交易引擎时序、推理线程池、状态库记录
  - 完成：`trade.live.engine.LiveEngine` 主循环骨架、`trade.live.store` 的 `latency`/`equity_snapshots` 表写入。
  - 缺：特征/推理/预过滤/排序下单的分钟事件流串联依赖 `trade.features` 增量接口与模型产物接入（`trade.models.artifact` 已由 T4-7 实现，尚未接入）；`scan_cycles`/`scan_state` 每轮决策事务写入已由 `Store.write_scan` 提供（T5-12）。
- [~] T5-7 订单：IOC 开仓、先止损后止盈、追踪改单先挂新后撤旧、幂等 clientOrderId
  - 完成：`trade.live.orders.OrderManager`——`build_order_id`（≤36 字符）、`compute_limit_price`（tickSize 向下取整）、`round_step_qty`；开仓 LIMIT IOC；`place_protective_orders` 止损先于止盈（红线 7）；`move_stop` 先挂新单确认成功再撤旧单、撤旧失败保留两张（红线 7）；超时/5xx 先按 clientOrderId 查询再重发（幂等）。单测覆盖全部状态机路径。
  - 缺：算法条件单真实参数（`algoType`/`triggerPriceType` 等）需服务器实测确认；仓位基准取 `walletBalance` 已在 sizing（回测实盘共用）。
- [~] T5-8 对账 6 种情况
  - 完成：`trade.live.reconcile`——`reconcile_positions` 覆盖 07 §6.2 六种情况（接管/改单未完成/已平仓/补挂止损/孤儿挂单/数量不一致）+ `reconcile_fills` 补齐成交/资金费/手续费/撤单差异；单测覆盖。
  - 缺：7 天内断线缺口真实回补、`kill -9` 重启对账需服务器 G4。
- [~] T5-9 风控 R1~R14、安全模式、Kill Switch
  - 完成：`trade.live.risk.RiskEngine`（R2/R3/R4/R5/R6/R7/R8/R9/R11/R12/R13/R14 单测；R1 由 sizing 决定；R10 由 `LatencyMonitor`）；`scripts/kill_switch.py`（halt_flag + 撤 trd- 挂单 + MARKET reduceOnly 平仓 + REST 确认，不依赖主进程）。
  - 缺：kill_switch 三种入口真实演练、R6/R8/418 自动触发停机需服务器。
- [~] T5-10 `trade.live.control` 与 `set_sizing.py` 白名单与校验
  - 完成：`trade.live.control`（白名单 10 键、写入前用实时权益做 06 §5.4 校验、`effective_sizing` 合并）、`scripts/set_sizing.py --show/--reset/--position-frac`；单测覆盖白名单拒绝/非法值拒绝/合法覆盖。
  - 缺：`set_sizing.py` 真实冒烟、看板控制入口（依赖 dashboard 控制表）。
- [~] **T5-12 看板所需的状态库数据**（07 §3.4、§6.1）
  - 完成：`trade.live.store` 新增 `scan_cycles`/`scan_state` 表（`Store.write_scan` 一次事务写入全部币含 `seq`/`status`）；`events.notified` 字段 + `mark_event_notified`；`orders`/`fills`/`positions` 永久保留；`reconcile_fills` 用 `userTrades`/`income` 补断线缺口。
  - 缺：断线 10 分钟模拟补全缺失成交需在服务器验证；其余表按 `retention_days` 清理的定时任务未实现。
- [ ] T5-11 G4 测试网 ≥ 7 天演练（含一次月度再平衡演练；Kill Switch 用电脑命令行、电脑看板、手机 SSH 各一次）；**G4 之前完成 T0.3**

## P6 可视化看板（FastAPI + 静态前端，深色 iOS 风格，SSH 隧道访问）

> 规格：docs/10（整体重写）；布局以 `docs/dashboard-overview.svg` 为准；只有深色主题，不做浅色与切换。
> 看板代码 `src/trade/dashboard` 已按本设计实现：`__main__.py`、`app.py`（FastAPI）、`config.py`、`reports.py`、`security.py`、`state_db.py` 与 `static/`（index.html、styles.css、app.js、vendor/）。其余实盘页面按 T6-4~T6-6 补齐。

- [x] **T6-1 后端骨架与安全**（10 §2、§3、§7、§9、§11）
  - 内容：`python -m trade.dashboard`；FastAPI + uvicorn；`configs/dashboard.yaml`（按 10 §11）与 pydantic 模型；`server.address` 非 127.0.0.1 拒绝启动；所有 POST 与 `/ws` 校验 `Host`/`Origin`；状态库只读 URI 连接；`/api/status` 与 `/ws` 骨架；无 `state.db` 时只开放研究接口。
  - 验收：配置为 `0.0.0.0` 拒绝启动；非法 `Host`/`Origin` 被拒；`TestClient` 冒烟通过；`mypy src/trade/dashboard` 通过。
  - 完成（2026-10-31 提前准备阶段）：`python -m trade.dashboard`、`configs/dashboard.yaml` 与 `DashboardConfig` pydantic 模型、`server.address` 回环校验（`0.0.0.0` 拒绝启动）、POST/`/ws` 的 `Host`/`Origin` 校验、`/api/status`、`/api/reports`、`/api/reports/{id}`、`/api/models`、`WS /ws`、状态库只读 URI（`mode=ro`）、无 `state.db` 时只开放研究接口均已实现；`tests/dashboard/` 冒烟与安全测试通过。
- [x] **T6-2 前端框架与视觉规范**（10 §3、§4、§5.0）
  - 内容：`static/index.html`、CSS 设计令牌（10 §4 的颜色、圆角、毛玻璃、字体、动效曲线）、左侧栏与顶栏、页面路由（Alpine.js）；把 Alpine.js、lightweight-charts、Plotly.js 下载到 `static/vendor/`，`VERSIONS.txt` 记录版本与 SHA256；`prefers-reduced-motion` 支持；所有文本转义。
  - 验收：断网可打开；布局与草图一致；SHA256 校验测试通过；事件文本中的 HTML 被转义。
  - 完成（2026-11-05，按 `docs/dashboard-overview.svg` 逐区域对照后重写）：220 px 固定侧边栏（30 px 渐变 logo + `bn-trade` + 6 个带圆点的入口 + 底部“连接状态”卡与 4 行连接/权重/微信/SSH 隧道）；顶栏页面标题 + 模式标签 + 心跳/模型/仓位/停机 chip + 北京/UTC 双时钟与下一根收盘倒计时；4 张 104 px 指标卡；扫描 7/12、权益 5/12、持仓 5/12、挂单 4/12、事件 3/12 的 12 栅格布局；深色令牌、`x-text` 转义与 transform/opacity 动画；`tests/dashboard/test_static_layout.py` 覆盖。对照中修正的不一致：品牌文字（trade·看板 → bn-trade）、导航无图标（加圆点）、缺少总览 4 卡/扫描网格/持仓/挂单/事件区、扫描网格非 10 列、下排卡片比例缺失、顶栏缺页面标题与心跳/模型/仓位 chip、侧栏缺连接状态卡；导航“回测报告”按 10 §5.0 统一为“回测与研究”，SSH 隧道文案按 10 §5.0 使用 `127.0.0.1:8501`。
- [~] **T6-3 回测与研究页**（10 §5.5；只依赖 P1~P4 产物，可在 P3 完成后先做）
  - 验收：用真实报告打开，数值与 `summary.json`、`gate.json` 一致；交易范围快照与质量页可浏览。
  - 完成：回测报告列表与详情（`summary.json`/`gate.json`/`report.md`/权益曲线 Plotly/交易明细）与模型列表（`model_meta.json`）读取接口与页面；“数据与模型”标签页新增交易范围快照（按月份、排名、角色、成交额中位数、各月进出、规则参数与 `reviewed`、`universe_report.md`）、质量缓存页（30 天窗口、K 线/逐笔缺失、一致性误差、合格状态）与数据集 manifest 摘要（保留率、正样本率、特征哈希）。新增 `/api/universe`、`/api/quality`、`/api/datasets` 与 `tests/dashboard/test_overview_api.py` fixture 测试；真实报告数值一致性仍待 P4 真实产物（当前只验证结构与字段）。
- [~] **T6-4 总览与全市场扫描网格**（10 §5.1、§6；依赖 T5-12）
  - 内容：4 张指标卡、扫描网格（按 `seq` 点亮、进度条时长取实际耗时、状态样式、悬停、点击弹出 K 线抽屉、本轮统计、再平衡淡入淡出）、权益曲线、持仓卡、挂单、事件。
  - 完成（2026-11-05，静态部分 + fixture 状态库驱动）：`/api/overview`（权益/今日盈亏/持仓/今日交易 4 卡、持仓卡、挂单有效性与事件）、`/api/scan`（按 `seq` 排序的 `scan_state` 与 `scan_cycles` 统计）、`/api/equity?range=1D|1W|1M|all`、`/api/positions`、`/api/orders`、`/api/events`；前端按 `seq` 点亮、进度条 `transform: scaleX` 且时长取 `finished_ms-started_ms`、本轮统计数字直接来自 `scan_cycles`、断线重连初次加载直接显示全部已处理状态；`tests/dashboard/test_state_db.py` 与 `test_overview_api.py` 用 fixture `state.db` 验证。
  - 保留 [~]：测试网运行时的真实点亮顺序、扫描统计随 `scan_cycles` 实时变化、悬停提示与点击 K 线抽屉、再平衡淡入淡出需 T5-12 与测试网运行后验收；权益距权益下限提示需交易进程提供 `minNotional` 口径后补。
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
| 5   | `sanity.deflated_sharpe`                       | 06 §7 S11                                                | 公式已按论文重写；S11 端到端已接入（日志记录 Sharpe/skew/kurtosis/n_obs） | **已一致，无需修改**                        |
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
| 17  | `quality.compute_quality`                      | 03 §2.4：三项各 1%/1%/2%，按 30 天窗口                   | 已支持分窗/缓存；真实 bars 已验证（无 27 分钟样例，差异已报告）                      | T0.17 已完成                              |
| 18  | 特征流水线                                     | 03 §6、04 §5：数据段独立计算                             | 在拼接后的整段数据上计算                                  | T0.15                                       |
| 19  | 组合模拟权益曲线                               | 06 §3.4、§5.1：回撤按盯市权益                            | 已按 `c_t` 逐分钟盯市（钱包余额 + 未实现盈亏）            | **已一致，无需修改**                        |
| 20  | 标签                                           | 04 §7.3：默认 `exit_sim`                                 | 只有三重障碍                                              | T0.19                                       |
| 21  | 看板                                           | 10：FastAPI + 静态前端、SSH 隧道                         | 只有 Streamlit 时期的空包；依赖仍含 streamlit、plotly     | T6-1~T6-7、T0.10                            |

## 需要使用者操作与决定的事项

**需使用者操作（无需决策）：**

1. T0.9：git 仓库已初始化并提交（已复核 2026-10-31）；如仍需修正分支名/远端请自行处理。
2. T1-3：快照已审阅并置 `reviewed: true`（已完成，保留备查）。
3. T4-9：G3 封存集运行时在场确认。
4. T1-7：在腾讯云控制台确认流量包是否计入**入方向**（影响 03 §3 的流量与费用估算；不影响数据正确性）。

**待使用者决定（不得由 Agent 自行选择）：**

1. **T1-5 实时/历史成交口径比对**（03 §1.1 第 8 条，aggTrade 行情流当前无数据，详见 T1-5 条目）：
   - 方案 A：推迟到 P5 行情模块完成后做。由 `scripts/api_smoke.py --env mainnet` 用最新文档确定 `@aggTrade` 的正确订阅方式，再连续录制 1 天并与次日历史归档逐条比对，差异写入质量报告。
   - 方案 B：现在先用 `bookTicker` 做简化比对。只能验证行情连通性、事件时间与价格一致性，**不能**验证逐笔成交口径（是否包含特殊订单类型），03 §1.1 第 8 条的原始目标仍未满足，需在 P5 补齐。
2. **现有 bars 首日大单阈值缺失（已按方案 A 执行，2026-11-05）**：服务器端 M1~M22 已修复并通过样例对拍；仍需你安排本地同步（约 5 GB，跨境链路慢）并重建 P2 数据集 `ds_20261102`。T4-3 第 1 步（「三个月六组」）重跑已由使用者决定跳过（2026-11-06），冻结参数保持不变。
3. **`paper` 模式的合约测试网端点**（09 §2、§3）：`https://testnet.binancefuture.com` 的网页路径已 301 跳转到 `https://demo.binance.com/...`，而 `https://demo-fapi.binance.com`（REST）与 `wss://fstream-demo.binance.com`（WS）可用。需确认 `paper` 模式是否切换为 demo 域名，确认后再改 `trade.live.exchange.endpoints` 并同步 09 §2。
4. **`shadow` / `live` 的行情来源**（09 §3.3，详见 T5-7 条目）：正式网 `@kline_1m` / `@aggTrade` 在 `/ws/`、`/stream?streams=`、`/ws`+`SUBSCRIBE` 三种写法下均无消息，K 线与逐笔的正式网来源尚未确定。
5. **G1 是否通过**：由使用者依据本文件与完成报告中的逐项判定表确认，Agent 不自行宣布通过。

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

## 规范维护（AGENTS.md）

- [x] 2026-11-11 AGENTS.md 红线重构：13 条合并为 15 条（旧 4+5 → 新 4、旧 6+7 → 新 5、旧 13 → 新 7），新增“设计书与代码一致、只写规格”“不得篡改测试”“禁止静默回退”“危险操作先确认”“提速必须等价”五条；`src/`、`scripts/`、`tests/`、`docs/` 中对旧编号的引用同步替换为新编号；消除 §1、§1.1、§3.1、§5、§10 与红线之间的重复和矛盾表述。来源：使用者下达的规范优化指令。
- [x] 2026-11-11 设计书交叉引用修复：`scripts/check_docs_sections.py` 的“省略文档号”引用不再跨文字猜文档号（`AGENTS §3.2`、`按 §2.1` 这类本文件或非设计书引用不再被误判），并修正 3 处真实失效引用（AGENTS.md §5.1 表中两处指向看板旧章节号的引用改为 `10 §5.6`；`src/trade/backtest/engine.py` 的预计算注释改为 `06 §7 S4`）。全仓库该检查的失效引用由 13 条降为 0 条；`tests/test_check_docs_sections.py` 覆盖基线路径与引用解析两种情形。


- [x] 2026-11-11 Cline 规则文件：新增 `.clinerules/00-core.md`（无 frontmatter，每个任务生效）、`10-docs.md`（`paths: docs/**`）与 `20-quant.md`（`paths: src/trade/**`、`scripts/**`、`configs/**`、`tests/**`）以及 `.clineignore`；AGENTS.md §1 注明 Cline 使用 `.clinerules/` 且摘要冲突时以 AGENTS.md 为准；02 §6 目录结构与 README.md 同步；新增 `tests/test_agent_rules.py` 校验摘要逐条等于 AGENTS.md §6 红线、§8 命令与 §5.1 对照表。来源：使用者下达的第 3 步指令。
- [x] 2026-11-11 回测性能基准：新增 `scripts/bench_backtest.py`（分阶段计时：加载 bars、R0 信号、每次预计算、每次组合模拟并拆出 `_close_lookup` 与 `_mark_to_market_curve`、盈亏平衡滑点、比例扫描、S1、S4；`--profile` 写 cProfile 前 30）与 `scripts/bench_compare.py`（红线 15 容差逐字段对拍，`timing.json`/`profile.txt` 为测量产物不参与）；`artifacts/bench/` 保存 4 组规模测试与 `baseline_5x1` 标准答案，命令见该目录 `README.md`；02 §6 目录结构同步。来源：使用者下达的第 4 步指令。
- [x] 2026-11-11 盯市权益曲线向量化（性能，红线 15）：`engine._close_lookup`（每币每分钟一个 dict）改为 `_close_arrays`（每币 `open_time`/`close` 数组，只取两列）；`_mark_to_market_curve` 改为 `np.searchsorted` 取持仓区间 + `np.unique` 合并采样时刻 + 离场排序 `cumsum` 求已实现 + `np.add.at` 累加未实现 + `np.maximum.accumulate` 求回撤，缺失分钟仍按 `p_fill` 计（贡献 0）；删除无调用方的 `_mark_equity`；`event_sim._event_combine` 同步。旧实现原样移到 `tests/backtest/_reference_mtm.py`，新增 3 个等价/手算测试。4 组基准（2x1、5x1、5x2、10x1，suite=full）与改造前快照逐字段对拍全部一致（每组 37 个文件）。来源：使用者下达的第 5 步指令。

