## 2026-11-18 数据集重建与测试段状态

- `scripts/build_dataset.py --run-id ds_r0_20261118` 在本地后台执行并**已完成**（237 币 + manifest，见上一条）；R1 全量诊断在同一天完成。
- **测试段 M13~M22 的正式回测未运行（0 / 3 次）**：调参期没有出现满足第 4 步门槛的候选（R1-4 顶端组最佳 PF 0.941 < 1、单笔净期望为负），按第 5 步“冻结全部配置后再跑正式 5 折”的前提未满足，因此不消耗测试段运行次数。
- **未运行且原因明确**：R2 的入场方式改造（`entry.limit_pullback` 的保守成交模型 + 标签同源实现）未实现；R2-2 的 `k_sl`/`sl_min` 网格未跑；R2-5 的补特征未加；R2-6 的出场小网格、R2-7 的 θ 规则改动、R2-8 的 TCN 对照均未跑。R3 的 80 组搜索未开始（预算 0 / 80 已用）。
- **服务器**：未在东京服务器执行重聚合与训练；服务器上仍在跑 bookTicker 录制，未受影响。



## 2026-11-18 R2-5 多周期/流动性特征（已实现，模型顶端首次转正）

- **新增 7 个特征**（04 §4.8，特征总数 46 → 53，`feature_hash` `229d020f…` → **`9bf71897…`**）：`r_240`（4 小时动量）、`r_1440`（24 小时动量）、`dist_24h_high`（距 24 小时高点距离）、`log_qv_60`（60 分钟成交额水平）、`bars_since_brk_60`（距上次突破的分钟数）、`weekday_sin`/`weekday_cos`（UTC 星期周期编码）。全部只用 t 及以前的数据；单测 `tests/features/test_multitimeframe_features.py`（5 项，含手算与「相隔 7 天编码相同」）。
- **数据集重建**：`ds_r2_20261118`（237 币、7,108,730 样本、53 特征、`feature_hash 9bf71897aeb3…`）。**旧模型产物（46 特征）不可再用**，需重训。
- **R1 式诊断（全量 169/169 币，报告 `artifacts/reports/diagnose_r2.md`）**：

| 指标 | 46 特征（`ds_r0_20261118`） | 53 特征（`ds_r2_20261118`） |
| :-- | --: | --: |
| 评估集整体 平均净 R / PF | −0.1542 / 0.590 | −0.1536 / 0.591 |
| p 的 top 0.5% | −0.0356R / **PF 0.918** | **+0.0368R / PF 1.092** |
| p 的 top 1% | −0.0254R / 0.941 | −0.0082R / 0.981 |
| p 的 top 2% | −0.0440R / 0.902 | −0.0385R / 0.913 |
| p 的 top 5% | −0.0815R / 0.825 | −0.0741R / 0.838 |
| 净 R 回归 m 的 top 0.5% | −0.0641R / 0.807 | −0.0168R / 0.948 |

- **结论**：新增的多周期动量/流动性特征把**模型顶端从负翻正**（top 0.5%：−0.036R → +0.037R，PF 0.918 → 1.092），但顶端只有 0.5% 样本（约 5,700 笔 / 3 个月），属于「窄而正」；top 1% 仍为 −0.0082R。标签分布、出场结构、成本与前一轮完全一致（stall 30.5% / initial_stop 16.9% / 一边成本 7.5bp）。
- **叠加估计更新**：回踩挂单 −9.98bp（整样本）+ 新特征后的顶端选择（+0.19R ≈ +19bp）≈ **+9bp**；15%/月（月 400~800 笔）需 +21~42bp，**缺口仍 ~12~33bp**。不过这是第一次出现正向组合，值得进入下一步：用 `limit_pullback` 重建标签 + 4 折模型级评估。
- **试验预算**：仍为 **59 / 80**（本轮的诊断不计入 tuning 预算，属于只读诊断）。
- **并发长任务（按使用者要求）**：东京服务器 `r0-15-agg.scope` 正在执行 R0-15 的 bars 重聚合（`run_pipeline.py --phase tuning --workers 1 --out data/bars_r0_15 --raw data/raw_r0_15`，`MemoryMax=2500M`），**输出到新目录，不覆盖现有 `data/bars`**；预计 6~16 小时，完成后需逐币比对 `bars_r0_15` 与 `data/bars`（零成交分钟与月初缺口的填充差异）再决定是否替换。



- **实现**：新增 `trade.features.kline.timeframe_atr`（按 N 分钟聚合后算 ATR14，分钟 t 只用上一个已完成时间桶，无未来函数）、`prefilter.cadence_min`（信号节流，训练/标签/实盘同一份 `is_sample_mask`）、`label.atr_timeframe_min`；`engine.prepare_symbols(..., atr_timeframe_min=)`、`precompute_trades(..., atr_timeframe_min=)`、`exit_sim(..., atr_timeframe_min=)` 与 `build_dataset` 全部透传；`configs/features.yaml` 增加两个字段（默认 1，行为不变）。
- **实测**（`--stride 4`，43 币，调参期 M1~M12；基线 1 分钟 = −16.22bp / 净 R −0.1566 / PF 0.653）：

| 配置（节流/ATR 周期/sl_min/H_max） | 单笔净 bp | 净 R | PF | 正收益率 | 标签数 |
| :-- | --: | --: | --: | --: | --: |
| 5m / 5m / 2.0% / 240 | −17.81 | −0.0875 | 0.772 | 36.5% | 274,866 |
| 5m / 5m / 1.5% / 720 | −19.29 | −0.1193 | 0.808 | 35.8% | 274,310 |
| 5m / 5m / 2.0% / 720 | −20.17 | −0.0994 | 0.811 | 35.6% | 274,310 |
| 5m / 15m / 2.0% / 720 | −20.36 | −0.0863 | 0.821 | 35.7% | 274,310 |
| 5m / 5m / 3.0% / 720 | −21.08 | −0.0703 | 0.809 | 36.8% | 274,310 |
| 5m / 5m / 2.0% / 1440 | −21.74 | −0.1072 | 0.826 | 35.7% | 273,628 |
| 15m / 15m / 2.0% / 1440 | −21.78 | −0.0906 | 0.817 | 34.0% | 108,760 |
| **5m / 5m / 2.0% / 720 + 回踩 30bp/5 根** | **−15.64** | −0.0779 | 0.864 | 37.5% | 133,903 |

- **关键结论（这一轮最重要的发现）**：放大决策周期后 **PF 明显改善（0.65 → 0.81~0.86）、净 R 明显改善（−0.157 → −0.07~−0.09）**，但**单笔净 bp 反而更差（−16 → −18~−22）**。原因是仓位口径是 `fixed_fraction`（每笔名义价值 = 权益 × 0.9，与止损宽度无关）：净 R 乘以更大的止损（2%~3%）后，bp 期望几乎没有改善，而每次止损对权益的冲击按止损宽度等比放大（sl 2% × 0.9 ≈ 1.8% 权益/次 vs sl 1% 时 0.9%）。**所以“降频放大目标”在这套固定名义价值的仓位口径下不能提高收益**；只有在按风险定额（`fixed_risk`）下单时才可能改变，而 `sizing.yaml` 的仓位口径归使用者，本 Agent 不改。
- **代价**：5m 节流让标签数从 118 万降到 27 万（约 23%），15m 降到 11 万（9%），月笔数会一起下降；要达到 15%/月所需的单笔 bp 反而更高（月 300 笔时需 +56bp）。
- **试验预算**：**59 / 80**（33 出场 + 18 入场 + 8 周期）。
- **依据指令授权，自行决定**：`configs/features.yaml` 的 `cadence_min`、`atr_timeframe_min` 保持默认 1（不改变当前策略口径），只作为可选开关保留；多周期方向按上表证据判为「不提高收益」，不进入模型级验证。



- **实现（回测与标签共用一份，红线 4）**：新增 `trade.common.anchors.plan_limit_entry`（挂单价 = `floor_tick(c_t × (1 − offset_bp/1e4))`，只用信号时刻收盘价；其后 `expiry_bars` 根内出现 `low ≤ limit − 1 tick` 且 K 线不缺才成交，成交价 = 挂单价）与 `trade.common.batch.batch_exit_plan`（以成交根与挂单价为基准推进同一份出场状态机）。`BacktestConfig.entry_mode` 增加 `limit_pullback` 与 `entry_limit_offset_bp`（默认 30bp）、`entry_limit_expiry_bars`（默认 5）；`engine.precompute_trades` 与 `labels.exit_sim` 走同一路径（回测把未成交记 `limit_no_fill`，标签直接丢弃该样本），`build_dataset` 透传参数；逐笔预计算缓存键加入这三个字段。单测 `tests/common/test_limit_entry.py`：成交根/成交价/1 tick 穿越判定/挂单价不含未来信息/标签与回测成交价一致。
- **标签级实测**（`--stride 4`，43 币、调参期 M1~M12，基线 taker = **−16.22 bp** / PF 0.653）：
  - 只改入场：`offset/等 N 根` → 10bp/3 = −12.73、20bp/5 = −12.37、30bp/5 = **−11.98**（PF 0.757）、50bp/5 = −11.48、30bp/10 = −11.83、50bp/10 = −11.09。
  - 入场 + 止损放宽：30bp/5 + `sl_min 2.0%` = **−10.99**；50bp/5 + `sl_min 2.0%` = −10.43；50bp/10 + `sl_min 2.0%` = **−9.98（目前最好）**；80bp/5 + `sl_min 2.0%` = −10.00（但标签数降到 12 万，密度约基线的 1/10）；30bp/5 + `sl_min 3.0%` = −10.46。
  - 入场 + 出场微调：30bp/5 + `sl_min 2.0%` + `be_trigger 1.5` = −10.84；+ `trail_trigger 1.0/k_trail 1.0` = −10.90；+ `stall 40` = −11.91；+ `tp6/H_max240` = −11.42；+ `vol_z 2` = −12.33（密度腰斩）。
  - **结论：入场方式是迄今唯一有效的杠杆，把单笔从 −16.22 提到 −9.98bp（+6.2bp），但仍是负的**；成本（来回约 15bp）与 stall/initial_stop 结构没有改变。
- **与模型选择的叠加估计**：R1-4 的模型顶端（top 1%）相对全样本 **+12.9bp**。若与入场改善近似相加：−9.98 + 12.9 ≈ **+2.9bp**，而 15%/月在该密度（月 400~800 笔）需 **+21~42bp**，**缺口 ≥18bp**；且深回踩使密度下降（50bp/10 的标签数只有基线的 41%），叠加后仍不足。
- **配置状态**：`configs/backtest.yaml` 仍 `entry_mode: taker`（回踩挂单需要重建标签与重训才生效，未冻结为默认）；新增的两个参数以注释形式给出。试验预算：**已用 51 / 80**（33 + 18）。
- **依据指令授权，自行决定**：不把 `entry_mode` 改成 `limit_pullback` 作为默认——它需要重建标签 + 4 折重训 + 测试段各一次，而按上面的叠加估计仍达不到 15%；等使用者确认是否接受“为 +3bp 量级投入一轮完整重训”。



- **方法**：新增 `scripts/r2_screen.py`（按给定出场/止损/预过滤参数重建 `exit_sim` 标签并统计单笔净 bp、净 R、PF、出场分布）、`scripts/r2_grid.py`（一次只改一个维度的批跑）、`scripts/r2_summary.py`、`scripts/r2_log.py`（登记 `kind=tuning`）。全部只读 M1~M12，抽样 `--stride 4`（43 个币、约 118 万条标签/组），结果写 `artifacts/reports/r3/r2_*.json`。**已用试验预算 33 / 80**。
- **基线**（现行配置）：单笔净 **−16.22 bp**、净 R −0.1566、PF 0.653、正收益率 38.3%；出场分布 stall 39.8%（−0.302R）、initial_stop 27.1%（−1.123R）、trail 18.3%（+1.364R）、breakeven 10.7%（+0.018R）、timeout 3.9%（+0.214R）、tp 0.21%（+3.939R）。
- **单因素结果**（净 bp）：`sl_min=1.5%` −15.95、`sl_min=2.0%` −15.60、`sl_min=2.5%` **−15.38**（最好）；`k_sl=3.0` −16.06；`stall_bars=40` −16.77、`stall_bars=400` −17.16（PF 升到 0.70 但净 bp 更差）；`H_max=240` −16.25；`be_trigger=1.5` −16.24（PF 0.691）；`trail_trigger=2.0` −16.37；`tp_cap=6` −16.24；`max_chase` 0.1%/0.5% 均 −16.2 左右。
- **组合**：`sl_min2.0+k_sl3.0` −15.58；`sl_min2.0+k_sl3.0+tp6+H_max240` −15.92；`sl_min2.0+stall40` −16.42；`k_sl3.0+trail1.0/k1.0` −15.66；`k_sl3.0+be1.5` −16.04。
- **预过滤收紧**：`vol_z=2` −16.28、`vol_z=3` −16.41、`brk_60=0.5%` −16.11、`brk_60=1.0%` −16.11、`r1_sigma=3` −16.15、`vol_z2+brk0.5%` −15.89；`fixed` 出场 rr=2 −16.35、rr=3 −16.50。**全部落在 [−17.8, −15.4] bp，最好也只比基线改善 0.8bp。**
- **入场上限探测**（`scripts/entry_probe.py`，43 币、1,597,030 个预过滤样本，报告 `artifacts/reports/entry_probe.json`）：回踩挂单（限价 = c_t×(1−offset)，向下穿越 1 tick 才成交）的价格改善**上界**（不含逆向选择）：
  - offset 10bp / 等 5 根：成交率 70.7%、成交时价格改善 +10.0bp → 期望 +7.1bp
  - offset 20bp / 5 根：51.2%、+19.9bp → 期望 **+10.2bp**
  - offset 30bp / 5 根：36.6%、+29.8bp → 期望 **+10.9bp**
  - offset 50bp / 5 根：18.7%、+49.6bp → 期望 +9.3bp
  再加 maker 相对 taker 省下的 3bp 费率，**换入场方式的理论上限约 +14bp**。
- **结论（依据指令授权自行决定：不再做 R2-0 的完整实现）**：把三项可用杠杆的上限相加——入场 +14bp、模型选择（top 1% 相对全样本）+13.5bp、出场/预过滤网格 +0.8bp——最乐观为 **−16.2 + 28.3 ≈ +12bp**，仍低于月化 15% 所需的 **+28bp**，缺口约 16bp；且三项收益不可简单相加（入场改善与模型选择作用在同一批交易上，深回踩还会引入逆向选择，2026-11-17 的 maker 实验已实测把亏损放大约 7 倍）。**因此不再投入 R2-0 的完整成交模型实现**，也不进入测试段。
- **仓位口径更新**：使用者把 `configs/sizing.yaml` 改为 `position_frac=0.18`、`max_positions=5`（0.18×5=0.90 ≤ 0.95），Agent 未改该文件；`tests/dashboard/test_overview_api.py` 的 `max_positions` 断言改为读取 `configs/sizing.yaml`（原写死 3，跟随使用者配置）。
- **配置改动**：`configs/*.yaml` 的出场、止损、预过滤、仓位参数**全部保持现值**（网格没有任何一组带来统计上可用的改善，按“改善就保留、否则回退”处理）。`signal_rank_by` 保留 `p`。



- **数据集**：`data/features|labels/ds_r0_20261118`（R0 修复后重建：237 币、7,108,730 样本、feature_hash `229d020f…`、exit_policy_hash `bec34dcf…`、warnings 215 条、段尾丢弃 4,983）。报告 `artifacts/reports/diagnose_r1.md`，**覆盖 169 / 169 个调参期币**、预过滤样本 8,469,252 行；只读 M1~M12，不加载 M13~M22 与封存集。
- **R1-1 sl 截断**：89.78% 的样本 `sl` 被 `sl_min = 1.0%` 截断（净 R 均值 −0.1605、正样本率 17.40%）；未截断 10.22%（−0.1275、28.70%）。R 的尺度基本由 `sl_min` 决定。
- **出场类型**：stall 30.53%（−0.2875R）、initial_stop 16.88%（−1.1239R）、trail 11.55%（+1.3649R）、breakeven 6.79%（+0.0179R）、timeout 2.98%（+0.2214R）、tp 0.13%（+3.9396R）。**stall 是最大亏损来源**（30% 笔数、每笔 −0.29R）。
- **R1-2 特征饱和**：clip=5 时 `cvd_norm` 3.08%、`rng` 1.71%、`vol_ratio_1440` 1.20%、`btc_r_1` 1.05%，其余 ≤1.02%。饱和不是主要瓶颈。
- **R1-3 方向子集**：全部 −0.1571R / 18.55%；`r_1>0 且 body>0` −0.1595 / 18.56%；`brk_60>0` −0.1607 / 17.24%。**方向过滤没有改善**。
- **R1-4 排序能力**（M1~M9 训练 3,196,092 样本；M10~M12 评估 1,143,959；验证正样本率 0.1729；Platt a=1.2756、b=+0.1524）：
  - 评估集整体：平均净 R −0.1542、PF 0.590。
  - 按分类概率 p：top 0.5% −0.0356R / PF 0.918；top 1% −0.0254 / 0.941（最好）；top 2% −0.0440 / 0.902；top 5% −0.0815 / 0.825。
  - 按净 R 回归 m：top 0.5% −0.0641 / 0.807，各档都差于 p；p×m 最差（top 0.5% −0.1296 / 0.531）。
  - **结论：`signal_rank_by=m/ev` 不优于 p**（2 币冒烟时的相反结论在全量上不成立）。`configs/backtest.yaml` 保持 `signal_rank_by: p`，开关保留供复核。
- **R1-5 成本**：入场、出场各 **7.5~7.8 bp**（吃单 5bp + 半价差 ≥1bp + 滑点 ≥1bp，tick 相对价格高的币更高），来回约 **15bp**；此前口径按来回约 3bp，**成本被低估约 5 倍**。
- **R2 优先级（依据指令授权自行决定）**：先做「入场方式/成本」与「出场结构（stall 占 30%）」；`direction=up`、`rank_by=m/ev`、提高 `clip` 三项被本诊断否定或收益很小，按“被 R1 证明无效的候选跳过”处理。
- **离月化 15% 的差距**：月化 15% 在该密度下需单笔净期望约 +28bp（records 2026-11-14 口径），当前全量顶端组（p 的 top 1%）为 −2.5bp，差约 30bp；该差距大于模型排序能力（PR-AUC 0.23 量级）与成本结构的可调空间。



- **依据 AI_迭代指令.txt 授权，自行决定**：连续执行、不逐项停下、设计书同步延后到每个大阶段末统一做；本条目记录取舍与证据。
- **依据指令授权，自行决定**：仓位口径按 `configs/sizing.yaml` 现值执行（`position_frac=0.20`、`leverage=5`、`max_positions=3`）。指令正文括号写的是「18%×5x、最多 5 仓」，与仓库现值不一致；「仓位口径固定为现值」与「不得加仓位比例凑收益」两条同时成立时，取文件现值，不把并发仓数从 3 提到 5（提高总敞口等于加风险）。若使用者要改为 18%×5，请直接改 `configs/sizing.yaml`，代码无需变更。
- **依据指令授权，自行决定**：闸门 G2/G2-U 未放宽（阈值仍是月收益中位 > 0、回撤 ≤ 20%、亏损月 ≤ 4、最差月 ≥ −12%、stress > 0）；本次只修测量口径，不改判定标准。
- **R0-1 验证窗口**：`tuning_models` 验证段改为覆盖 M10~M12 整三个月（`val_end` 取最后验证月月末），bars 多读 2 天作出场预热，`n_days` 由整三个月计算。
- **R0-2 调参口径**：`tuning_models._threshold_metrics` 与 `tuning_exits._scan_theta` 同时输出 reference（单笔期望与 PF，与 `backtest.py` 一致）与 user（月收益中位数、最差月、回撤）两套口径，并在调参评估中关闭 R6/R8/R7 停机（正式回测仍 strict）。选 θ 用 reference 口径。
- **R0-3 阈值网格与校准**：`tuning_exits` 的 `_THETA_GRID` 改用 `calibration.threshold_grid()`（0.10~0.80）；模型概率改为 Platt 校准（拟合在早停月 M10，再用于 M11/M12）；`_precompute_all`/流式预计算都传 `trade_mask`。
- **R0-4 名义价值**：组合层（`simulate_portfolio`、`event_sim`）的毛利、手续费、资金费、保证金与盯市统一改用 `qty × P_fill`；`reference_trade_net_pnl` 同步。
- **R0-5 指标口径**：权益曲线不再写 `ts_ms=0` 标记行；`daily_returns` 按 UTC 自然日补齐零收益日；`monthly_returns` 支持 `months` 网格补零；S11 改用非年化日频夏普（`sharpe_daily`）并按 run_id 去重、只统计 tuning/test。
- **R0-6 试验日志**：`append_experiments_log` 增加 `kind`/`theta`/`failed`；`count_logged_trials` 只数 `kind=tuning`；预算检查由「严格相等」改为「≤ 剩余预算」。
- **R0-7 测试段裁剪与 S5**：`backtest.py` 非五折模式把信号裁到 M13~M22（`data_window` 未划分测试段时打印说明不裁剪）；S5 折号按测试段日历月映射。
- **R0-8 is_filled**：`build_dataset` 在特征 parquet 写入 `is_filled`；`WindowDataset` 缺列或缺 open_time 直接报错（此前静默返回全 0，`max_fill_ratio` 失效）。
- **R0-9 EWM 偏差修正**：`EwmState` 增加累计权重 `weight` 与先验衰减 `decay`，标准化用 `(μ/W, (σ²−decay)/W)`；三处实现（`ewm_step`、numba 内核、`IncrementalComputer`）逐位一致（单测 `test_EWM段首偏差修正让小量纲特征不再被压到0`、`test_ewm_step_hand_calculated`）。
- **R0-10 S1 随机基准**：`backtest.py` 新增 `--features-dir`，S1 候选改为 `is_sample=1` 的预过滤样本并套用 `U(R)` 掩码；未提供时 S1 记 not_run（不再用策略信号冒充）。
- **R0-11 模型实现**：TCN kernel 由配置决定（含 `project_norm` 对照开关）；`set_deterministic(seed)` 移到 `build_model` 之前；BCE 改为 `sum(w·l)/sum(w)`；`walkforward` 的 Predictor 统一 `.detach().cpu()`；`exit_sim` 的 r_mult 截断读 `label.exit_sim_r_mult_clip`；`artifact` 缺 `calibration` 直接报错；`warmup_cosine_factor` 在 `warmup_epochs=1` 时第 0 轮为 0.5。
- **R0-12 校准**：改为 Platt `p = σ(a·z + b)`（`fit_platt`/`platt_sigmoid`），吸收 `pos_weight=√(neg/pos)` 造成的 logit 平移；`model_meta.json` 写 `calibration={a,b}`。
- **R0-13 组合层前视**：风控门控改用 `_unrealized_before`（严格早于 ts 的最后收盘价）；`release_due` 要求 `exit_open_time + 60s`；`_risk_timeline` 增补「离场下一分钟」采样点，避免跨日漏判 R6。
- **R0-14 封存集硬限制**：`run_pipeline` 只处理 M1~M22，`--phase holdout` 直接退出并说明（红线 2）。
- **R0-15 数据层**：零成交分钟填上一收盘价并置 `p_*_filled=1`（不计 `aggtrades_missing`）；月初连续缺失的回退价作用于整段；缺大单阈值时 `whale_*_quote` 输出 NaN；`build_universe` 缺交易规则报错；`new_entry_symbols` 按每折训练月判断；`triple_barrier` 检查 `p_*_filled`。
- **R0-16 冷却条件**：`risk.cooldown_on`（默认 `loss_stop`：仅止损类出场且净亏损才冷却；`any_stop` 保持旧行为）。
- **测试改动说明（红线 12，使用者指令要求的规格变更所附带）**：`_reference_mtm`/`test_engine` 去掉 `ts_ms=0` 标记行期望；`test_normalize` 手算样例改为偏差修正口径；`test_triple_barrier` fixture 补 `p_1s_filled` 列；`test_membership` 改用 `train_months`；`test_universe` 的规则哈希测试补 `--rules`；`test_risk_rules` 熔断平仓时点改为 T0+4；`test_tuning_exits` 的 θ 网格改用 `threshold_grid()` 并补 `sizing_cfg`/`backtest_cfg`。断言意图未削弱。
- **质量检查**：`ruff format --check .`、`ruff check .`、`mypy --strict src/trade/common src/trade/backtest src/trade/live`、`mypy src/trade/dashboard`、`pytest -q` 全部通过（647 passed / 3 skipped）。
- **未完成**：R0-15 的 bars 重聚合（约 16 小时）未在服务器执行；R1 全量诊断依赖新数据集（`ds_r0_20261118`）重建完成，本地重建记录见下一条。



- **本轮结论**：把执行方式、选择规则、特征集三条路都试完后，最好结果仍是 v3 taker：测试段 user/base **−2.5751 USDT（−2.58%）、PF 0.961、112 笔、胜率 48.2%、最大回撤 19.14%**，stress −1.3716，G2-U 不通过。maker 完整执行模型虽让五折中三折通过验证，但测试段 −19.15%（逆向选择），已否决。
- **冻结的配置**（仓库当前默认）：`features.yaml` 46 个特征全开；`train.yaml` `min_validation_pf=1.0`、`min_trades_per_day=5.0`；`backtest.yaml` `entry_mode=taker`、情景 `[base, stress, hell, breakeven]`。阈值选择为「≥5 笔/天 + PF ≥1 + 净收益最大」，无候选则该折 `theta=1.0` 不交易。
- **不做的事**：不运行 G3（封存集 M23~M24，需使用者在场，且 G2 未通过时按 08 不允许进入）；不连接测试网/实盘；不基于封存集调参。
- **未完成项**：T4-3 第 2 步 12 组 TCN 搜索（已用 1 组）、T4-4 的 M1/M2 正式训练、T4-6 的 F_final（`run_final` 无 `arch=lgbm` 分支）、T4-8 的 S1/S4/S8/S9 与 `breakeven`/`sizing_sweep`；这些留待下一阶段按新方向重开。
- **下一步（已与使用者对齐）**：P5/P6 工程（paper 模式、看板、对账、状态库），P4 保持 `[~]` 不再追加参数级实验。

## 2026-11-17 完整 maker 执行模型：验证集变好、测试段更差 → 否决

- **实现**（06 §3.2）：`backtest.yaml: entry_mode: taker|maker`。maker 下挂单价 = `floor_tick(c_t×(1−half_spread), tick)`（买一近似），**下一分钟最低价触及才算成交**，未触及的信号不成交（回测记 `skipped.reason = maker_no_fill`，标签 `exit_sim` 直接丢弃该样本）；入场滑点 0、入场费率 maker、出场仍 taker；`loss_rate = sl + half_spread + fee_maker + fee_taker`。回测与标签共用 `anchor_series` + `_apply_maker_entry`（红线 4）。新增单测 `test_挂单入场锚点与费率`。
- **数据集**：`ds_20261118`（maker 标签）样本 5,918,358（taker 为 7,108,730）→ **成交率约 83%**，17% 的信号因挂单未触及而不成交。
- **五折（maker 口径）**：F1 θ=0.24 验证 PF **1.490**、F3 θ=0.30 PF **1.077**、F5 θ=0.26 PF **1.068**（三折通过闸门，taker 时只有一折），F2/F4 禁用（0.909/0.922）；PR-AUC 0.2173/0.2770/0.2376/0.2304/0.2430。
- **测试段全量回测（`artifacts/reports/wf_maker_test`）**：reference/base **−18.3396（−18.34%）、PF 0.631、239 笔**；user/base **−19.1536（−19.15%）、PF 0.730、157 笔**、胜率 43.9%、回撤 20.36%；stress −17.81%、hell −18.92%。G2-U 不通过。
- **对照**：同特征、taker 口径的 v3 为 user/base −2.58%、PF 0.961、112 笔。**maker 入场把亏损放大约 7 倍**。
- **原因**：挂单只在价格下探时成交，形成**逆向选择**——成交的多是即将下跌的信号，跑掉的正是好信号。省下的 3 bp 费率远不足以抵消这个偏差。**这是数据给出的结论，不是实现 bug**：成交判定、标签与回测一致（同一 `anchor_series`），成交率 83% 符合预期。
- **结论**：成本不是这条策略的救命稻草；taker 的 v3（−2.58%、PF 0.961）仍是全部实验里的最好结果，仍未转正。**不建议实盘。**

## 2026-11-16 成本对照（maker 情景）：3bp 费率优势不改变结论

- 新增 `maker` 成本情景（`Scenario.entry_maker`，06 §4.3）：价格、滑点、点差与 `base` 相同，**只把入场手续费按 maker**（2bp vs taker 5bp）。不模拟挂单未成交，属成本优势的**偏乐观上界**，只报告、不参与闸门。`simulate_portfolio(..., entry_maker=...)` 为新参数，默认 False，不影响既有口径；单测 `test_maker情景入场费率用maker`。
- 在 v3 信号（46 特征、只有 F1 交易）上同一份数据跑 4 个情景：

  | 运行 | base | maker | 差 |
  | :-- | --: | --: | --: |
  | reference 净收益 / PF / 笔数 | −15.7090 / 0.803 / 390 | −14.3427 / 0.865 / 531 | +1.37 USDT |
  | user 净收益 / PF / 笔数 | −2.5751 / 0.961 / 112 | −3.5335 / 0.955 / 130 | **−0.96 USDT（更差）** |

- 结论：**省 3bp 费率不足以把策略翻正**；user 口径反而因权益路径改变（复利 + 停机触发点变化、可成交集合不同）变得更差。要真正压低成本必须实现「挂单 + 未成交率 + 排队」的完整成交模型（另需重建标签），不是一次配置改动能解决。当前最优结果仍是 `wf_v3_test` 的 user/base −2.58%（PF 0.961），未通过 G2-U，**不具备上实盘条件**。

## 2026-11-16 跨市场特征 + 新规则：五折只有 1 折通过，测试段 −2.58%（仍不通过）

- **新增 3 个跨市场特征**（04 §4.7，特征 43 → 46，`feature_hash` → `229d020f…`）：`xs_breadth`（同分钟全市场 r_15 > 0 占比）、`xs_mean_r15`（横截面均值）、`xs_disp_r15`（横截面离散度）。实现按分钟网格累加计数/和/平方和，内存与币数无关；逐币按 `open_time` 向后对齐；`IncrementalComputer.update(..., xs_bar=...)` 支持实盘喂入。数据集重建为 `ds_20261117`（237 币、约 40 分钟）。
- **调参期快筛（M10 验证）**：PR-AUC 0.2515（无 xs 时 0.2522），日均 ≥5 笔的 θ 上最高 PF **0.720**（无 xs 时 0.864）→ 该折禁用。**在同一验证月上 xs 特征没有帮助，反而更差。**
- **五折（`ds_20261117`）**：F1 通过（θ=0.28、验证 PF **1.141**、276 笔≈9.2 笔/天），F2~F5 被禁用（验证 PF 0.826/0.930/0.926/0.924）。**F1 通过、其余四折不交易**。
- **测试段全量回测（`artifacts/reports/wf_v3_test`，只有 F1 的 2 个月有交易）**：

  | 运行 | 情景 | 净收益 | 收益率 | PF | 笔数 | 胜率 | 最大回撤 |
  | :-- | :-- | --: | --: | --: | --: | --: | --: |
  | reference | base | −15.7090 | −15.71% | 0.803 | 390 | 46.2% | 20.22% |
  | reference | stress | −16.6297 | −16.63% | 0.791 | 379 | 39.3% | 20.33% |
  | user | base | **−2.5751** | **−2.58%** | **0.961** | 112 | 48.2% | 19.14% |
  | user | stress | −1.3716 | −1.37% | 0.984 | 139 | 41.0% | 21.57% |

  G2-U：中位月 −2.58%、最差月 −2.58%、亏损月 1、最大回撤 19.14%、stress −1.3716 → **不通过**。
- 与上一轮相比：同样口径下从「五折全禁用、0 笔」变为「1 折可交易、112 笔、−2.58%」，亏损幅度从 −19.86% 收窄到 −2.58%、PF 从 0.219 升到 0.961，但**仍未转正**（PF < 1、stress < 0）。

## 2026-11-16 五折复核（新特征 + ≥5 笔/天规则）：全部折无正期望 → 测试段 0 笔交易

- **选阈值口径修正**：`scripts/train_walkforward.make_evaluator` 原先把 `configs/sizing.yaml` 的停机设置带进阈值评估，验证月在亏钱时会触发 R8 停机，导致「任何 θ 都达不到日均笔数」或曲线失真。现改为评估时把 `daily_loss_halt`/`max_drawdown_halt` 置 0.99、`max_consecutive_losses` 置 1000（正式回测仍用原配置，05 §7.2 已写明）。
- **五折（`ds_20261116`，特征哈希 `f2a86153…`）**：F1~F5 的验证 PR-AUC 0.2096 / 0.2645 / 0.2258 / 0.2191 / 0.2343；在「日均 ≥5 笔」的候选 θ 上最高 PF 分别 **0.826 / 0.888 / 0.845 / 0.944 / 0.883**，全部 < 1 → 五折 `threshold=1.0`（`DISABLED_THETA`，不交易）。测试段信号 2,768,679 行仍照常产出，但没有任何行能通过 θ。
- **测试段全量回测（`artifacts/reports/wf_v2_test`）**：reference/user × base/stress/hell **全部 0 笔、净收益 0.0000、回撤 0.00%**；G2-U 判定不通过（无交易）。
- 结论：在 1 分钟决策、≥5 笔/天的约束下，43 个特征（含本轮新增的 6 个订单流/OI 特征）仍给不出正期望；新增特征只把 PR-AUC 从 0.2520 提到 0.2522（同一折），排序顶端依旧窄（`p≥0.29` 占 0.3%）。系统现在的行为是**没有边际就不下单**，不会再出现 −20% 的连续亏损，但也赚不到钱。

## 2026-11-15 特征扩展 + 选择性规则收紧：边际仍未出现，系统按规则拒绝交易

- **新增 6 个特征**（只用 `bars` 里已有的 1 秒级原子量与 K 线列，不重新下载逐笔）：`wick_ratio_5`、`close_pos_5`、`range_ratio_5`、`tbr_div`（K 线拒绝形态与主动买占比差分）、`whale_streak_5`（近 5 根大单方向一致性）、`oi_price_div_15`（`sign(r_15) × oi_chg_15`，持仓量与价格的配合/背离）。特征数 37 → 43，`feature_hash` `6437b53b…` → **`f2a86153…`**；手算单测 5 项，`tests/features` 59 项通过。数据集重建为 `ds_20261116`（237 币、7,108,730 样本，本地约 25 分钟，新特征用 pandas 滚动窗口使单币构建从 ~2.6 s 升到 ~10 s）。
- **规则收紧（05 §7.2）**：日均笔数下限 3 → **5**（`walk_forward.min_trades_per_day`）；选优目标从「PF 保留 0.9 的最低 θ」改为「满足 PF ≥ 1 的 θ 中净收益最大者」；无候选时该折置 `theta=1.0` 不交易（`disabled_reason`）。
- **调参期快筛（M1~M9 训练 / M10 选 θ / M11~M12 评估）**：新特征 PR-AUC **0.2522**（旧 0.2520），验证集在日均 ≥5 笔的 θ 上最高 PF **0.8639**（旧 0.8597）→ **无 θ 同时满足 ≥5 笔/天与 PF ≥ 1，该折按规则禁用（θ=1.0，不交易）**。
- 标签层按 p 分位（新特征集）：`p∈[0.292,0.438]`（占 0.3%）PF 1.292、均值 +0.106R；`θ=0.30` PF 1.722（2,448 个样本，约 40/天/全市场），但 `θ=0.35` 反而 PF 0.642（224 个样本）——顶端极窄且不稳；把这些信号放到组合层（≤3 持仓、冷却、风控）后，此前实测 θ=0.30 的 PF 只有 0.798。
- **结论**：在「1 分钟决策 + ≥5 笔/天」这两个前提下，现有信息（含新增订单流/OI 特征）没有可用的正期望；系统现在会在验证集无边际时**拒绝交易**（不再出现 −20% 的连续亏损）。要真正做出正期望，必须换数据源（历史盘口/深度、跨币横截面、其他交易所）或换目标定义，属 04/05 级设计变更，需使用者拍板。

## 2026-11-15 策略优化尝试：p 顶端有排序，但边际不能外推

- **诊断（只用调参期）**：训练 M1~M9（M0）、M10 选 θ、M11~M12 评估（`tune_eval.py`，资料 `artifacts/signals/tune/tune_probe_TUNE.parquet`）。标签层面按 p 分位统计（`edge_by_p.py`）：p∈[0.10,0.19] PF 0.459、[0.28,0.30] PF 0.914、**[0.30,0.45] 胜率 55.9%、均值 +0.145R、PF 1.424**、**≥0.35 胜率 64.1%、均值 +0.408R、PF 2.889（仅 287 个样本）**。排序单调，但有效区间只在最顶端 0.4% 以内。
- **调参期回测（M11~M12，为看全段关闭 R5/R8 阈值）**：

  | 变体 | reference/base | user/base |
  | :-- | :-- | :-- |
  | θ=0.30（该折自选） | PF 0.798、430 笔、−18.76、回撤 26.8% | PF 0.765、368 笔、−36.56 |
  | **θ=0.35** | **PF 1.294、50 笔、+2.23、回撤 3.7%** | PF 0.996、48 笔、−0.08、回撤 10.4% |
  | θ=0.35 + 放松出场（be 2R、trail 2.5R、k_trail 2.5、H_max 240） | PF 0.817、45 笔、−2.15 | PF 0.765、44 笔、−7.19 |

  即：**收紧阈值有效、放松出场无效**；但 θ=0.35 在 2 个月里只有 50 笔，统计意义很弱。
- **测试段一次性验证（规则先在调参期定死，再看测试段）**：

  | 规则 | reference/base | user/base | 结论 |
  | :-- | :-- | :-- | :-- |
  | A：只在验证 PF≥1 的折交易（F5，θ=0.16） | PF 0.515、159 笔、−18.63 | PF 0.541、53 笔、−14.37 | 验证段 PF 1.05 未外推 |
  | B：全折 θ=0.35 | PF 0.266、96 笔、−20.09 | PF 0.250、47 笔、−20.22 | 顶端边际未外推 |

  两个变体都在一个月内触发 20% 峰値回撤停机。**结论：现特征 + `exit_sim` 标签 + M0 在测试段没有可外推的正期望，策略层面的参数优化到此为止；不得进入 G3 或实盘。**
- **代码改动（安全闸门）**：`calibration.select_threshold` 新增 `min_profit_factor`，`WalkForwardConfig.min_validation_pf`（默认 1.0）：验证集最优 θ 的 PF 低于下限时返回 `theta=1.0`（`DISABLED_THETA`）并写 `threshold_selection.disabled_reason`，该折不交易。新增单测 `test_select_threshold_验证PF低于下限时禁用交易`。该闸门能挡住 F1~F4（验证 PF 0.954/0.892/0.779/0.851）这类「明知负期望仍下单」的情形，但挡不住「验证为正、测试为负」的选择偏差。


## 2026-11-15 P4 全量回测：M0（LightGBM）五折 walk-forward + 237 币测试段

- **数据集**：服务器 3.6 GB 内存跑不了 237 币训练，改在本地从 `data/bars` 重建 `ds_20261115`（`scripts/build_dataset.py --run-id ds_20261115`）。与服务器 `ds_20261114b` 逐项对齐：237 币、样本 **7,108,730**、`feature_hash=6437b53b5ce5…`、`label_dropped.segment_tail.total=4983`、215 条 warning，两端 bars 事前已核 SHA256 2077/2077 相同。数据集文件未做逐文件哈希比对（服务器侧流量约 0.7 MB/s，5.3 GB 传输不可行）。
- **内存路径**：`WindowDataset` 新增 `feature_mode="lazy"`（只建样本索引，特征由 `symbol_features(sym_id)` 按币读取后释放）；`walkforward._tabular_arrays` 与 `_predict_signals_lgbm` 改走该接口。M0 训练峰值常驻内存由逐币常驻（F5 需约 6 GB 特征）降到 2~5.5 GB，本地 16 GB 可跑。`tests/models/test_dataset.py::test_lazy_features_match_resident` 验证两种模式样本与单币特征矩阵逐值一致。
- **五折（`artifacts/models/wf_lgbm_full*`）**：

  | 折 | 训练月 | 验证月 | 温度 T | θ | 验证 PR-AUC | 验证笔数 | 验证 PF | 测试段 | 测试信号行 |
  | :-- | :-- | :-- | --: | --: | --: | --: | --: | :-- | --: |
  | F1 | M1~M11 | 2025-09 | 0.850 | 0.32 | 0.2082 | 123 | 0.954 | 2025-10~11 | 755,008 |
  | F2 | M1~M13 | 2025-11 | 1.083 | 0.24 | 0.2638 | 240 | 0.892 | 2025-12~2026-01 | 590,397 |
  | F3 | M1~M15 | 2026-01 | 0.906 | 0.20 | 0.2248 | 213 | 0.779 | 2026-02~03 | 520,102 |
  | F4 | M1~M17 | 2026-03 | 0.968 | 0.24 | 0.2161 | 206 | 0.851 | 2026-04~05 | 445,359 |
  | F5 | M1~M19 | 2026-05 | 0.929 | 0.16 | 0.2335 | 414 | 1.050 | 2026-06~07 | 457,813 |

  拼接后测试段信号 2,768,679 行（`artifacts/signals/wf_lgbm_full_test.parquet`，p 的 P50=0.160、P90=0.225、P99=0.272）。F1 探路运行与正式运行同种子信号文件字节数相同（7,951,670），可复现。
- **全量回测（闸门口径，`artifacts/reports/wf_lgbm_full_test`）**：237 币、评估段有信号 142 币；服务器 2.5 GB cgroup 上限在 80/237 币被 OOM 杀掉，改在本地运行（bars 与服务器相同，`trade.backtest.engine` 同一份实现）。

  | 运行 | 情景 | 净收益 USDT | 收益率 | PF | 笔数 | 胜率 | 最大回撤 | 单笔 |
  | :-- | :-- | --: | --: | --: | --: | --: | --: | --: |
  | reference | base | −20.5815 | −20.58% | 0.274 | 109 | 32.1% | 20.70% | −49.01 bp |
  | reference | stress | −20.7842 | −20.78% | 0.244 | 110 | 30.9% | 20.89% | −49.88 bp |
  | reference | hell | −20.2876 | −20.29% | 0.165 | 92 | 16.3% | 20.34% | −69.31 bp |
  | user | base | −19.8565 | −19.86% | 0.219 | 42 | 26.2% | 20.13% | −53.49 bp |
  | user | stress | −19.7661 | −19.77% | 0.189 | 40 | 30.0% | 20.02% | −55.93 bp |
  | user | hell | −20.5815 | −20.58% | 0.033 | 31 | 9.7% | 20.74% | −75.37 bp |

  G2-U：月收益中位 −19.86%、最差月 −19.86%、亏损月 1、最大回撤 20.13%、stress −19.7661 USDT → **不通过**。
- **停机截断**：`halt_mode=strict` 下 R8（峰值回撤 ≥20%）在 2025-10-08 14:53（user，42 笔）与 2025-10-26 22:04（reference，109 笔）触发后停止交易，剩余测试月没有成交；把 `halt_mode` 改为 `reset` 后结果逐项相同（峰值回撤未恢复前 R8 条件持续成立）。
- **诊断口径（非闸门，`wf_lgbm_full_nohalt`：`max_drawdown_halt`/`daily_loss_halt`=0.99、`halt_mode=reset`）**：全测试段实际成交 user/base 1,321 笔、净 −94.5361 USDT、PF 0.578、胜率 37.2%、单笔 −29.88 bp；reference/base 1,154 笔、PF 0.585、单笔 −26.22 bp。逐折（reference/base）：F1 358 笔 PF 0.556、F2 775 笔 PF 0.606、F3 21 笔 PF 0.311，F4/F5 因权益耗尽（名义价值低于最小下单量）无成交。出场类型（reference/base）：initial_stop 454、stall 272、trail 230、breakeven 151、timeout 39、tp 8。
- **R0 同口径对照（`r0_full_nohalt`，tbr_5 0.70 / vol_z_60 3.0）**：user/base 2,324 笔、净 −94.5496、PF 0.720、胜率 33.6%、单笔 −10.56 bp；reference/base 2,109 笔、PF 0.688、单笔 −11.87 bp。**M0 的单笔期望约为 R0 的 2.5 倍差**（−29.88 vs −10.56 bp），胜率更高但盈亏比更差。
- **缺陷修复**：`metrics.monthly_returns` 不再把 `ts_ms=0` 的初始权益标记行当作月份（此前每月度统计会多出 1970-01，把中位数稀释，例如被截断的 run 会把 −19.86% 摊成 −9.93%）。新增 `tests/backtest/test_metrics.py::test_月收益忽略初始权益标记行`。


## 2026-11-15 R0 策略调参实验（只在调参期 M1~M12 上选）

- 做法：`scripts/r0_signals.py` 新增 `--r0-tbr5/--r0-volz`（只在内存里覆盖阈值，不改 `configs/train.yaml`）；`scripts/backtest_stream.py` 新增 `--months START:END`，在调参期上做全量对比。测试段 M13~M22 不参与选择（红线 3）。
- 调参期全量结果（`reference` / `base`，M1~M12）：

| R0 阈值 | 净收益 USDT | PF | 笔数 | 胜率 | 最大回撤 |
| :-- | --: | --: | --: | --: | --: |
| tbr_5 0.60 / vol_z_60 2.0（现行） | −19.0046 | 0.409 | 162 | 33.3% | 20.22% |
| tbr_5 0.70 / vol_z_60 3.0（收紧） | −20.2254 | 0.531 | 234 | 33.8% | 20.23% |

- 结论：收紧预过滤能把 PF 从 0.409 提到 0.531，但净收益更差（−20.23 vs −19.00），两者都在数日内触发 R8（峰値回撤 20%）后按 `halt_mode=strict` 停机，笔数与月度指标被停机时点主导。**R0 阈值调整救不了负期望**，因此 `configs/train.yaml` 的 R0 保持原值（05 §2「事先固定」），R0 继续只作基线；提升只能来自模型（M0，`_run_fold_lgbm` 已实现待验证）。
- 顺带修正 `backtest_stream` 的 `kind` 判定（`fold_id` 全为 `R0` 时不再误标为 model）。


## 2026-11-15 全量回测（237 币）跑通：流式预计算 + 精简数组

- 新增 `scripts/backtest_stream.py`（全量回测专用编排）：逐币 `load_bars` → `prepare_symbols` → `precompute_trades`（每个成本情景一次）→ `slim_from_prepared` 落盘 `.npy` 后以 memmap 交给组合层 → 释放完整数组；组合模拟仍调用 `trade.backtest.engine.simulate_portfolio`（红线 4，同一份实现）。`trade.backtest.engine` 新增 `SlimSymbol` 与 `slim_from_prepared`，`_close_arrays`/`_entry_price_arrays`/`_funding_arrays` 接受精简结构。
- 等价性：20 币流式运行与 `scripts/backtest.py` 普通路径逐项相同（reference/base −18.3555、PF 0.499、206 笔；stress/hell 与 user 运行亦相同）。
- 内存：普通路径 20 币峰值 **3.08 GB**（`/usr/bin/time -v`），全量 237 币约需 30 GB；流式路径逐币预计算 + 精简数组 + 只预计算 `p ≥ min θ` 的候选，全程低于 2 GB。用 `sudo systemd-run --scope -p MemoryMax=2500M` 给重任务加了 cgroup 上限，避免再拖垮 sshd 与录制进程。
- **全量 R0 回测（237 币；232 币在测试段有信号；测试段 M13~M22；`reference`/`user` × base/stress/hell）**：

| 运行 | 情景 | 净收益 USDT | 收益率 | PF | 笔数 | 胜率 | 最大回撤 |
| :-- | :-- | --: | --: | --: | --: | --: | --: |
| reference | base | −18.3765 | −18.38% | 0.762 | 476 | 36.6% | 20.32% |
| reference | stress | −18.9753 | −18.98% | 0.659 | 341 | 29.9% | 20.08% |
| reference | hell | −20.1103 | −20.11% | 0.298 | 152 | 17.8% | 20.11% |
| user | base | −15.3070 | −15.31% | 0.817 | 271 | 36.9% | 20.39% |
| user | stress | −19.3642 | −19.36% | 0.648 | 178 | 29.2% | 20.21% |
| user | hell | −20.4656 | −20.47% | 0.217 | 55 | 16.4% | 20.47% |

  - G2-U（user/base）：月收益中位 **−7.65%**、最差月 −15.31%、亏损月 11、最大回撤 **20.39%**、stress −19.36 USDT → **不通过**。
  - 全部情景最大回撤都≈20%：`halt_mode=strict` 下 R8（峰値回撤 ≥20%）触发后停止交易，因此笔数远少于候选数。
  - 报告中标注为 not_run（流式模式）：`breakeven` 情景、`sizing_sweep`、S1 随机入场、S4 阈值扰动、S8 逐笔重放、S9 事件对拍（它们需要重复预计算或完整 bars）。
- 数据缺口：`r0_signals` 现在会把"窗口内没有任何分钟数据"的币**逐币列出**（本轮 C247~C251 共 5 个，需补下载），不再中途中断整轮。
- 两次全量尝试因内存被打断（选择器曾用 `groupby` 生成 232 个 DataFrame、且按 `p ≥ 0` 预计算全部候选，峰值超 3.6 GB）：改为 NumPy 分组切片 + `p ≥ min θ` 后峰值降到 2 GB 以内；期间录制进程两次被内核连带杀掉，均已重启（`book_recording4/5.log`）。


## 2026-11-15 M0（LightGBM）walk-forward 分支

- `trade.models.walkforward` 新增 `_tabular_arrays`、`_predict_signals_lgbm`、`_run_fold_lgbm`，并在 `run_fold` 顶部按 `cfg.arch == "lgbm"` 分派：折划分、purge/embargo、温度校准（`fit_temperature` via `calibration_metrics`）、`select_threshold` 与 `model_meta.json` 全部复用 PyTorch 路径的同一口径；表格特征用 `trade.models.lightgbm.tabular_features_matrix`（[X[t], X[t]−X[t−1], X[t]−X[t−5], X[t]−X[t−15]]），模型存 `lgbm.txt`，验证段 PR-AUC 用 `trade.models.train.average_precision`。
- 首次服务器运行报 `pinned_cpu` 需要 CUDA（`tensor.pin_memory()` 不可用），已改为 `data_placement=cfg.data_placement`（无 CUDA 时自动用 CPU）。
- **未验证**：修正后重跑命令已下发，但在等待结果期间本机终端故障（每条命令只返回首字符、退出码 1），五折训练结果与后续模型回测**均未观测**，不能当作已完成。恢复后需依次执行：`scripts/train_walkforward.py --limit-coins 20 --train tmp_train_lgbm.yaml`、核对每折 θ 与 `model_meta.json`、再用 `scripts/backtest.py --walk-forward-summary artifacts/signals/walkforward/walk_forward_summary.json` 跑测试段。
- R0 策略调参（用户要求）：**未开始**。计划严格限定在调参期 M1~M12 上做 `r0.{brk_60,tbr_5,vol_z_60}` 三参数网格（先看信号数与调参期 PF，不碰 M13~M22），选定后再跑一次测试段；R0 参数属 05 §2 的“事先固定”项，若最终修改须同步 05 §2 与 `configs/train.yaml`。


## 2026-11-15 R0 回测跑通、θ 候选下限调整与服务器约束

- **θ 候选下限（05 §7.2）**：候选集由 {0.30, 0.32, …, 0.80} 改为 {0.10, 0.12, …, 0.80}（36 个）。原因：M0 概率 P99 实测 0.24~0.30，原下限 0.30 在任何折都无法满足“验证段日均 ≥ 3 笔”，`select_threshold` 必然报错；下调下限只扩大候选集，PF 保留 0.9 与日均 3 笔的判定规则不变。`tests/models/test_calibration.py::test_threshold_grid_range` 同步改为断言 0.10/0.80/36 个（红线 12 要求记录：本次改测试是使用者授权的方法调整附带项）。
- **`--limit-coins N`**：`scripts/r0_signals.py`、`scripts/backtest.py`、`scripts/train_walkforward.py` 新增该开关（按 alias 升序、只取有首个调参月 bars 的币）。小内存机器跑通用，正式运行不给该参数；缺数据的币在正式路径仍按红线 13 报错。
- **服务器工具链**：venv 补装 `torch 2.15.0+cpu`（`trade.models.dataset` 顶层 import torch，LightGBM 路径也绕不开）、`matplotlib`（报告绘图必需，缺它回测会在最后一步失败）。
- **服务器内存天花板**：366 MB~3.6 GB 的机器上，全量 237 币的训练与回测都需要 10~20 GB（`WindowDataset` 常驻每币 (分钟数 × F) 的张量、`prepare_symbols` 常驻每币 15 个 float64 数组）。因此本轮全部用 `--limit-coins 20` 规模跑通；全量必须换 16 GB+ 机器或把这两处改成流式（与 `tuning_exits --low-memory` 同一思路）。
- **OOM 事故与恢复**：全量训练（237 币）启动后被内核 OOM 杀掉，连带把 bookTicker 录制进程带走；已重启录制（tmux `rec`，日志 `artifacts/live/book_recording4.log`，7 天窗口重新计时）。事故后 `free` 恢复正常，`ds_20261114b` 未受影响。
- **R0 回测（真实运行，reference / base）**：
  - 全窗口信号（M1 起）：979 笔，net −19.31 USDT（−19.31%）、PF 0.535；第 6 天即触发 R8（峰値回撤 ≥ 20%）并按 `halt_mode=strict` 停止交易 → 报告里 `halted` 跳过 19,845 条。
  - 测试段信号（M13~M22，20 币、389,671 行信号、6,525 条 p=1）：206 笔，net −18.36 USDT（−18.36%）、PF 0.499、胜率 27.2%、含返佣 −14.76 USDT（PF 0.567）；G2-U 月收益中位 −8.23%、最差月 −16.46%、11 个月为负、最大回撤 20.19%、stress −15.81 → **G2 不通过**；DSR 0.546（N=3）。
  - 结论：R0（规则基线）在测试段明确为负期望，模型必须显著优于它才有意义；这两份报告是 G2「必须优于 R0」的对照基线。
- **G2 回测的调用口径**：`scripts/backtest.py` 的权益曲线起点是数据窗口起点（M1），`test_months` 只影响报告/闸门的月份口径；要跑测试段必须把 signals.parquet 裁剪到 M13~M22（本轮用独立信号文件 `artifacts/signals/r0_scope20_test.parquet`）。
- **仍缺的一块**：`trade.models.walkforward.run_fold` 只有 PyTorch 路径（`train_ensemble`），`arch=lgbm` 会抛 `build_model` 的 ValueError；M0 的 LightGBM 训练只存在于 `tuning_models`/分析脚本。要在服务器（无 GPU、3.6 GB）产出 M0 的 walk-forward signals，需要给 `run_fold` 加 lgbm 分支（表格特征 + `train_lgbm` + 温度校准），或在 GPU 大内存机器上跑 TCN。


## 2026-11-14 剩余步骤收尾（第 4、12、15、19、20、22 步）

- **第 15 步（bars 一致性）**：以本地 `data/bars` 的 2077 个文件为清单，两端各算一遍 SHA256（本地 `artifacts/reports/bars_sha256_local.txt`、服务器 `artifacts/reports/bars_sha256_remote.txt`）：**2077/2077 完全相同、0 处差异**。数据集在服务器重建为 `ds_20261114b`（237 币、7,108,730 样本、`notional_ref=100`）。本地不再拉取 4.9 GB 数据集，训练与回测统一在服务器执行。
- **第 12 步验收（只缺逐笔分钟的 A/B）**：以 `kline_missing := kline_missing | aggtrades_missing` 精确复现修复前的跳过口径，在 `precompute_trades`（`base` 情景）上逐笔对拍：
  - 2025-01~2025-03 的 C001/C002/C023（缺口 10,080~44,620 分钟）、2025-07 的 C150~C153（缺口 4 分钟）：**成交笔数、出场类型分布、`gross_return` 均值、`mae_r` 均值全部一致**。
  - 原因：调参期 237 币 × 12 个月的扫描显示，只缺逐笔的区域要么是整段（≥1 天），要么是 2025-07 的 4 分钟短缺口（47 个币月有短缺口，共 4 分钟/月），抽样币月中没有一个样本出现“缺口分钟落在持仓窗口内且价格触及止损/止盈”的情形。
  - 结论：该修复对当前数据集的可成交结果无影响，语义差异由单测（`tests/labels/test_exit_sim.py`、`tests/backtest/test_engine.py`）覆盖；C012 2025-01 整月只缺逐笔（44,620/44,640 分钟）导致该月没有任何信号，无法作为验收样本。
- **第 4 步（基准快照）**：`scripts/bench_backtest.py` 补 `theta` 列（R0 驱动无模型阈值时按 `backtest_cfg.threshold` 填充，与 `scripts/backtest.py`、`scripts/r0_signals.py` 一致；此前脚本因缺列直接报错）。重建基准 `artifacts/bench/baseline_5x1_20261114`（5 币 × 2025-07，`full` 套件，服务器总耗时 **22.57 s**，其中 `sizing_sweep` 2.97 s、`s1_candidates` 7.91 s 为最大项）；重复运行 `bench_compare.py` **7 个文件逐字段一致（rtol=1e-12, atol=1e-9）**，基准可用。
- **第 20 步（θ 决定）**：θ 网格维持 05 §7.2 的 0.30~0.80 不变，`select_threshold` 不改；6 组无法选出 θ（M0 P99 0.24~0.30）时保留冻结参数，失败组写入 `EXPERIMENTS.log`。05 §7.2 新增第 5 条固化该行为与 G2 披露要求，08 G2 表新增“报告披露”一行。概率尺度与 θ 网格不匹配的问题移交 T4-3 第 2 步（模型调参：校准/温度或特征与阈值复核）。
- **第 15 步第 5 项（封存月重生成）**：SUIUSDT = `C001`，其 2026-08/09 月文件在 T1-9 修复脚本运行时已重写（`2026-11-05T21:13`）；此后没有任何影响 bars 内容的改动（`loader` 的改动只影响读取顺序，`exit_sim`/`uniqueness_weights` 只影响标签）。原始逐笔目录 `data/raw/aggtrades/C001` 已清空，重新生成需先从 data.binance.vision 重新下载两个月聚合原始数据。当前 SHA256：`202608.parquet = 4b47537eb8cc5e3e6d346e9a220b69327fa0e0a2040bc329cb1252c15b6f85b7`、`202609.parquet = 9b5536f8b196e5b2d236f9c1d154899d222cdb24b11162b36896468e6eddbe54`（只算哈希，未读取内容）。G3 运行 `--final` 之前按 08 的检查单重跑一次并记录哈希变化。
- **第 19 步（点差）**：`record_book_ticker` 以 `--days 7` 持续录制（PID 1508638，已 223 MB，别名分组目录），预计 2026-11-21 结束；结束后执行 `spread_profile.py --book-root artifacts/live/book --out artifacts/costs --aliases-file data/universe/aliases.json` 出分位画像，并按同期 bars 计算“信号分钟”口径。


- **第 22 步补充（月化 15% 测算）**：`artifacts/reports/monthly_15pct.md`。真实数字：标签单笔 `r_mult` 均值 −0.1584 R、标准差 0.8295 R，`sl` 中位 1.000%，单笔波动 0.863%；θ=0.30 时 1.69 笔/日（50.7 笔/月）；月化 15% 需单笔净 **27.9 bp**（扣成本前约 37.9 bp），当前为 **−16.5 bp**，差约 44 bp；G2 的 3 bp 门槛在同密度下等价于 **+0.15%/月**，与 15%/月 相差约 9 倍。
- **服务器**：代码树按本地 `src/configs/scripts` 重新同步（此前 `~/trade` 与仓库有漂移，`scripts/bench_backtest.py` 等文件缺失）；清理临时数据集 `ds_verify_tmp`、`ds_20261114`、`ds_probe`、`ds_profile`、`ds_smoke_server`、`ds_smoke_server2`，磁盘可用 24 GB → **31 GB**，只保留 `ds_20261114b`。


## 2026-11-14 性能优化（红线 15 等价性验证）

单币基准（C001，568,800 根分钟、76,610 个样本，本地）：

| 改动 | 位置 | 改动前 | 改动后 | 等价性 |
| :-- | :-- | --: | --: | :-- |
| 按月交易范围改为月序号向量化（去掉逐分钟 `strftime`） | `trade.data.loader.universe_trade_mask` | 0.55 s | 0.02 s | mask 逐位一致（差异 0 个元素） |
| 标签装配去掉逐样本 Python 循环，资金费改用前缀和区间求和 | `trade.labels.exit_sim` | 4.03 s | 3.30 s | labels 逐字段一致：整数与字符串完全相同，浮点最大绝对差 4.4e-16 |
| 平均独特性权重改为逐根累加（去掉逐样本 numpy 切片） | `trade.labels.triple_barrier.uniqueness_weights` | 0.52 s | 0.06 s | 最大绝对差 1.3e-15、最大相对差 2.9e-15（rtol 1e-12 内） |
| `load_bars` 先判已升序无重复，命中时跳过 `sort_values`/`drop_duplicates` | `trade.data.loader.load_bars` | — | — | 返回值与改动前相同 |
| 单币 `build_dataset` 稳态耗时（服务器，5 币连续构建，各自独立进程） | `scripts/build_dataset.py` | 41.4 s / 5 币 | 23.5 s / 5 币（≈1.76×） | 6 币标签全列一致（整数与字符串完全相同，浮点最大绝对差 8.9e-16）、特征表逐列完全相同 |

- 全量重建对照：优化后用 `build_dataset.py --run-id ds_verify_tmp` 重建 237 币，从首个到最后一个特征文件写盘 **5.6 min**（页面缓存已热）；与 `ds_20261114b` 逐文件对拍，C001~C006 的 labels 全列一致、features 逐列完全相同（`ds_20261114b` 由优化前代码构建）。

- 尝试并回退：`_simulate_exit_batch_nb` 改 numba `parallel=True` + `prange`（逐笔独立、结果理论上与调度无关）。服务器实测该内核 1.58 s → 2.63 s（该内核内存受限、线程开销大于收益），已还原串行实现。
- 未采用的候选：特征文件 fp16 改 fp32（写入 1.46 s → 0.97 s，但改变 `data/features` 落盘契约）、EWM 内核 `math.pow(x, 2.0)` 改 `x*x`（会改变个别值的 1 ulp，与现有“批量与逐点逐位一致”的测试约定冲突）、跨币进程池并行（3.6 GB 机器上峰值内存不够，需先评估）。


# 过程记录

本文件保存从设计书（`docs/design.md` 与 `docs/01`~`docs/10`）移出的实测流程、原始结论与接口核对记录，按“文档名 §章节号”分组。**本文件不是规格**：规格以设计书对应章节为准，这里的数字与原始输出仅供追溯。

## design.md §5.2

- 官方 SDK 跟进接口变更（如 2025-12 条件单迁移到 Algo 接口）、签名实现；使用方式为直接使用官方 SDK，锁定版本，封装在 `ExchangeClient` 后面；WebSocket 行情自研。

## 02 §5.4

- 端点延迟实测结果见 09 §9 核对记录。
- WS 订阅方式已由 `scripts/api_smoke.py --ws-check` 实测核实（09 §3.3）：测试网 `@kline_1m`/`@aggTrade`/`@bookTicker` 及组合流均正常；正式网仅 `@bookTicker`/`@trade`/`@depth` 有数据，`@kline_1m`/`@aggTrade` 的正确写法仍未确定（不影响 `paper` 模式）。

## 03 §2.4

- 质量判定实例：SOLUSDT 2026-10-27 源文件缺 27 分钟，单日缺失占比约 1.9%，放在 30 天窗口中约 0.06%，该窗口判定合格。

## 04 §7.2

- 止损下限 `sl_min` 的调参期对比（决定取 1.0% 的过程记录）：`sl_min = 0.5%` 时山寨币 1 分钟级噪音在入场后约 8 分钟（中位数）内就会扫掉止损，41.8% 的交易被假突破打损、每笔亏约 62bp；`sl_min = 1.0%` 时假突破扫损显著下降，调参期验证段 PF 为 1.001~1.473，成本约占止损的 15%。
- 止损上限 `sl_max` 的调参期扫描（决定取 2.5% 的过程记录）：`sl_max = 2.5%` 时调参期 6 组参数全部 PF > 1 且净收益为正（组 6 PF = 1.087）。
- 盈亏比 2:1 的调参期观察：止损放宽后含成本盈亏平衡胜率降至约 33%~35%，实际胜率反而提升。

## 05 §6.1

- 第 1 步（出场与止损）实际运行 18 组：3 轮 × 6 组，含失败的组，逐组登记在 `artifacts/reports/EXPERIMENTS.log`；已计入 30 组预算，第 2 步可用预算为 12 组。

## 07 §3.1

- 实测（2026-11-05，东京服务器，`api_smoke.py --ws-check`，测试网）：三流组合流 60 秒 1278 条消息（`bookTicker` 713 / `aggTrade` 458 / `kline` 107）。人工用 `iptables` DROP 断网 30 秒：连接未断开、无消息缺口（TCP 重传 + WS ping/pong 未超时），重连 0 次；断网 100 秒（超过 ping 超时窗口）：连接断开、自动重连 1 次并回补 1 根已收盘 K 线。
- 同次核对时正式网 `@kline_1m` / `@aggTrade` 无推送，`shadow` / `live` 的 K 线与逐笔来源当时待使用者决定（对应事项见 docs/tasks.md）。

## 07 §5.2

- 实测参数（2026-11-05，测试网）：`POST /fapi/v1/algoOrder` 的 `algoType=CONDITIONAL` + `type=STOP_MARKET`，查询走 `GET /fapi/v1/openAlgoOrders`，撤单 `DELETE /fapi/v1/algoOrder`（`algoId` 或 `clientAlgoId`）。

## 07 §8.2

- 实测（2026-11-05，东京服务器，`scripts/notify_smoke.py`）：10 类事件各 1 条 + 突发 6 条，共 16 条在约 30 秒内全部返回 `code = 1000`（成功），未见服务端限流拒绝；单条送达耗时 170 ms ~ 6.4 s（其中一次 6.4 s 应为服务端排队）。

## 09 §2 / §3.3（WebSocket 路由）

- 2026-11-12 东京服务器（腾讯云 NRT）用 `websockets` 直连探测正式网，单连接 12 秒消息数：
  - `/public` + `btcusdt@bookTicker`：11576 条（首条 `{"stream":"btcusdt@bookTicker","data":{...}}`）
  - `/market` + `btcusdt@aggTrade`：836 条；`@kline_1m`：41 条；`@markPrice@1s`：11 条；`@miniTicker`：4 条；`@ticker`：4 条；`!markPrice@arr`：6 条
  - `/public` + `@depth5@100ms`：97 条；`@depth10@100ms`：78 条；`@ticker` 在 `/public`：0 条
  - 旧入口 `/stream?streams=btcusdt@aggTrade`：0 条
  - `/private/ws?listenKey=invalid_key_probe`：握手成功、无消息
  - 测试网：`/public` + `@bookTicker` 24 条；`/market` + `@aggTrade` 14 条；`/public` + `@depth5@100ms` 18 条；`/private/ws?listenKey=invalid_key_probe` 握手成功、无消息；旧入口 `/stream?streams=btcusdt@aggTrade` 仍 22 条
  - 官方文档站 `developers.binance.com` 在服务器侧返回 `HTTP/2 202`、`x-amzn-waf-action: challenge`（CloudFront WAF 挑战页，正文 2110 字节、无可用内容），未能取到 HTML 正文，路由结论以上述实测为准。
- 2026-11-12 代码上线后服务器 `api_smoke.py --env mainnet --ws-check --symbol BTCUSDT --ws-seconds 60`：
  - 连接地址 `wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker` 与 `wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m/btcusdt@aggTrade`
  - 消息 66978 条：`bookTicker` 64967、`kline` 180、`aggTrade` 1831；已收盘 1m K 线 1 根（open_time 1794496680000）；断线重连 0 次，缺口回补 0 根；退出码 0
- T1-5（实时 aggTrade 与次日 data.binance.vision 逐条比对）未运行：需连续录制 1 个 UTC 日后再下载同日历史文件。

## 09 §9

| 核对日期   | 核对人                     | 范围                    | 结论 / 变更                                                                                                                                                                                                                                                                                                  |
| :--------- | :------------------------- | :---------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-10-28 | 设计方（网络检索，未实测） | 条件单迁移、官方 SDK    | 条件单需走 `/fapi/v1/algoOrder`（2025-12-09 起旧接口返回 -4120）；官方模块化 Python SDK 已提供 USDⓈ-M 包。**均需在 P5 用 `api_smoke.py --env testnet` 实测确认**                                                                                                                                             |
| 2026-10-28 | Cline（东京服务器实测）    | exchangeInfo、ping 延迟 | `GET /fapi/v1/exchangeInfo` 返回 200，`futuresType=U_MARGINED`，`rateLimits.REQUEST_WEIGHT` 2400/min；**`contractType` 存在 `TRADIFI_PERPETUAL`**（TradFi，`underlyingType=EQUITY`、`underlyingSubType=["TradFi"]`），`check_contract` 按 `!= PERPETUAL` 即可阻断；REST ping 20 次 P95 ≈ 16 ms（P50 ≈ 8 ms） |
| 2026-10-28 | Cline（东京服务器实测）    | WebSocket 行情流        | `wss://fstream.binance.com/ws/`：`btcusdt@bookTicker` 有数据；`btcusdt@aggTrade`、`btcusdt@kline_1m`、`!miniTicker@arr` 均无数据（连接成功但收不到消息）。疑似行情流命名/订阅机制变更，**待 P5 `api_smoke.py` 以最新文档实测核实**，WS 事件延迟暂记为待测                                                    |
| 2026-10-31 | Cline（东京服务器复测）    | WebSocket 行情流        | 复测确认：`btcusdt@trade`（30 秒 P95≈14ms）与 `btcusdt@bookTicker`（60 秒 P95≈22ms）有数据；`btcusdt@aggTrade`、`btcusdt@kline_1m`、`btcusdt@markPrice`、`!miniTicker@arr` 连接成功但无数据（组合流 `SUBSCRIBE` 收到 ACK 后仍无数据）。REST P95≈11ms（20 次）。官方文档经 Cloudflare（HTTP 202）无法抓取正文，**正确订阅方式仍待 P5 `api_smoke.py` 以最新文档实测核实**；`latency_probe.py` 暂改用 `bookTicker` 测 WS 延迟                                                    |
| 2026-10-31 | Cline（东京服务器实测）    | leverageBracket         | `GET /fapi/v1/leverageBracket` 无 API Key 返回 **401（需签名）**，响应为数组而非对象（与 §3.2「需签名」一致）。P1 `snapshot_rules.py` 在 401/403 时跳过、`max_leverage` 视为满足（03 §4.3）；正式拉取需在 P5 用带签名客户端并解析数组 |
| 2026-11-04 | Cline（本地开发，未实测）  | §3 全部接口            | `trade.live.exchange` 已按 §3 实现 REST 封装（`endpoints`/`models`/`signing`/`client`）与 WS 解析（`ws`）。签名：HMAC-SHA256 用官方文档示例 Key+载荷单测核对一致；Ed25519 用 `cryptography` 往返核对（本地无 `cryptography` 时 skip）。**所有接口真实行为、算法条件单 `algoOrder` 参数、测试网是否支持算法条件单、WS `aggTrade`/`kline_1m` 订阅方式均待 `scripts/api_smoke.py --env testnet` 在服务器实测后回填本表** |
| 2026-11-05 | Cline（东京服务器复测）    | WebSocket 行情流、REST aggTrades | `btcusdt@aggTrade` 三种写法（`/ws/<stream>`、`/stream?streams=`、`/ws/`+`SUBSCRIBE`）各测 15 秒均 0 条消息（`/ws/`+`SUBSCRIBE` 返回 HTTP 400）；同期 `btcusdt@trade` 15 秒 381 条；`GET /fapi/v1/aggTrades?symbol=BTCUSDT&limit=3` 返回 200 且有数据。`@aggTrade` 正确订阅方式仍未确定，**这是 T1-5 实时/历史成交口径比对的唯一阻塞项**，待 P5 `api_smoke.py` 核实 |
| 2026-11-05 | Cline（东京服务器实测，T5-2/T5-7） | RSA 签名、算法条件单、订单生命周期 | RSA：`demo.binance.com` 申请 RSA key 后，`testnet.binancefuture.com` 全部签名接口（account/positionRisk/commissionRate/leverageBracket/listenKey）返回 200；`trade.live.exchange.signing.rsa_signature` 用 pycryptodome 实现并往返验证通过。算法条件单：`algoType=STOP_MARKET` 返回 `-4500 Invalid algoType`；正确为 `algoType=CONDITIONAL` + `type=STOP_MARKET`（挂单/撤单/查询 `GET /fapi/v1/openAlgoOrders` 均 OK）；`GET /fapi/v1/algoOpenOrders` 路径不存在（-5000）。订单生命周期（`api_smoke.py --env testnet`）全绿：IOC 开仓 FILLED → 挂止损 → 挂止盈 → 撤止盈 → 市价平仓 → 确认仓位 0 → 撤止损；中途 `round(x/tick)*tick` 的浮点尾差触发 `-1111 Precision is over the maximum`，已用 `floor_to_tick` 修复 |
| 2026-11-05 | Cline（东京服务器实测，T5-2 阻塞排查） | 测试网域名迁移、密钥归属 | `https://testnet.binancefuture.com` 的**网页路径**（`/`、`/en/futures/BTCUSDT`、`/en/login`、`/login`、`/sapi/...`）全部 **301 → `https://demo.binance.com/en/futures/BTCUSDT`**；REST `GET /fapi/v1/ping` 在本域名仍 **200**。新发现：`demo-fapi.binance.com/fapi/v1/{ping,time,exchangeInfo}` **全部 200**（合约 Demo 的 REST），`fstream-demo.binance.com` DNS 存在（合约 Demo 的 WS），`demo-api.binance.com` 只有现货风格 `/api/v1`、`/api/v3`（`/fapi/*` 为 404），`fapi.demo.binance.com` 证书域名不匹配不可用。另：使用者在 `testnet.binance.vision`（**现货**测试网）申请的 key 打 `/api/v3/account` 返回 200、打合约 `/fapi/v2/account` 返回 `401 -2015`。**待决：`paper` 模式的端点是否切换到 demo 域名（`https://demo-fapi.binance.com` / `wss://fstream-demo.binance.com`），需使用者确认后改 `trade.live.exchange.endpoints` 并同步 09 §2** |
| 2026-11-05 | Cline（东京服务器实测，T5-4） | WebSocket 订阅矩阵 | 12 种写法各测 8~65 秒：**测试网** `wss://stream.binancefuture.com` 的 `/ws/btcusdt@kline_1m`（65 秒 120 条）、`/ws/btcusdt@aggTrade`、`/ws/btcusdt@bookTicker` 与 `/stream?streams=btcusdt@aggTrade/btcusdt@kline_1m` 组合流（三流组合 60 秒 1278 条）均正常。**正式网** `wss://fstream.binance.com` 仅 `@bookTicker`（8 秒 14948 条）、`@trade`（8 秒 2308 条）、`@depth@100ms`（12 秒 117 条）有数据；`@kline_1m`、`@aggTrade`、`@markPrice`、`@ticker`、`@miniTicker` 在 `/ws/`、`/stream?streams=`、`/ws`+`SUBSCRIBE`（ACK `{"result":null}` 成功）三种写法下 65 秒均 0 条。`wss://fstream.binance.com/ws/`（带尾斜杠）发 SUBSCRIBE 返回 HTTP 400。官方文档站为 Cloudflare 挑战页（202、0 字节），无法取得正文。结论：`paper` 模式（测试网）行情可用；正式网 K 线/逐笔流的可用写法未确定，**需使用者决定 shadow/live 的行情来源** |

## AGENTS.md §6

- 2026-11-11 红线重构：13 条合并为 15 条。编号映射：旧 1/2/3 → 新 1/2/3、旧 4+5 → 新 4、旧 6+7 → 新 5、旧 8 → 新 6、旧 9+13 → 新 7、旧 10 → 新 8、旧 11 → 新 9、旧 12 → 新 10；新 11~15 为新增。起因：同一约束在多条重复（4 与 5、6 与 7、9 与 13）、看板设计细节混入原红线 12、§1/§1.1/§10 重复红线内容；且缺五类已真实出现的问题——缺交易规则时 `tickSize` 静默回退 0.01、服务器修复脚本处理到封存月、改测试断言让测试通过、危险删除操作无确认、性能改动改变结果。
- 2026-11-11 全仓库旧编号引用按上表替换（`src/`、`scripts/`、`tests/`、`docs/`、`README.md`）：匹配 46 行，其中 14 行编号发生变化，其余 32 行旧编号本就等于新编号。`docs/review.txt` 的审查结论保留原编号并加“原”字，避免与现行编号混淆。


## AGENTS.md §1

- 2026-11-11 Cline 规则文件：新增 `.clinerules/00-core.md`（无 frontmatter，始终生效）、`10-docs.md`（`docs/**`）、`20-quant.md`（`src/trade/**`、`scripts/**`、`configs/**`、`tests/**`）与 `.clineignore`（排除 `data/`、`artifacts/`、密钥类文件）。三个摘要中的红线、§8 命令与 §5.1 对照表逐条照抄 AGENTS.md，由 `tests/test_agent_rules.py` 校验；手工把 00-core.md 的一条红线改一个字后该测试失败（已改回）。规则注入依赖工作区根目录为 AGENTS.md 所在目录，否则 Cline 找不到规则文件。

## 06 §3.3 / §3.4（锚点数组与 micro-batch）

- 2026-11-12 优化前 profiling（`scripts/bench_backtest.py --coins 2 --months 2025-07 --suite base --profile`）：jitted 编译约 6.0 s，`prepare_symbols` 3.46 s，`precompute_trades` 12 次调用合计 6.2 s。逐笔 `simulate_exit` 19666 次调用累计 2.74 s（每次约 139 µs，其中 numba 30 个位置参数的调度开销占绝大部分）。
- 2026-11-12 改动：新增 `trade.common.anchors`（按币一次算 `down`/`up`/`p_fill`/`sl`/`r_dist`/`min_move`/`slip`/`slip_stop`/`half_spread`/`loss_rate` 数组），`exits` 拆出 `_exit_machine_nb` 并新增 `simulate_exit_batch`（一次 numba 调用处理同一币一批交易），`trade.common.batch.batch_exit` 作为回测与标签共用的批量入口（信号位置 → 锚点切片 → 批量状态机）；`precompute_trades` 与 `labels.exit_sim` 改为锚点数组 + 批量调用。
- 等价性：`scripts/bench_compare.py artifacts/bench/baseline_5x1_v3 artifacts/bench/step7_5x1` → “对拍通过：37 个文件逐字段一致（rtol=1e-12, atol=1e-09）”。
- 提速：同一台本地机器、5 币 × 2025-07、`--suite full`，改动前 `artifacts/bench/baseline_5x1_v3` 合计 316.43 s（reference/base 预计算 16.50 s、sizing_sweep 77.08 s），改动后 `artifacts/bench/step7_5x1` 合计 41.47 s（reference/base 预计算 3.73 s、sizing_sweep 4.32 s）。两次运行都包含 jitted 编译时间。



## 2026-11-13 第 13/15/16/18/19/20/22/23 步实测与接口核对

- 入场口径（第 13 步）：`p_1s_filled`/`p_3s_filled` 由 `trade.data.aggregate` 在首秒无成交（沿用上一分钟收盘价）或整分钟缺逐笔时置 1；回测与 `exit_sim` 现在把这两种情况都视为陈旧价并放弃信号。追价判断改为卖一近似 `p_raw × (1 + half_spread)`，与 `trade.live.orders.compute_limit_price` 的 `min(ask × (1 + 2 tick_rel), c_t × (1 + max_chase))` 同口径。
- 本地回归：`py -3.13 -m pytest -q` 全绿（见完成报告）；回测层 115 个测试、标签层 19 个测试。
- 信号密度（第 22 步，真实运行，本地调参期 M1~M12，169 个币）：2024-10 因预热只覆盖部分月份，预过滤样本 13914（9.28/币/日）；2024-11~2025-09 每月 447866~534252 条（248.81~296.81/币/日），每月入选币数 50~60。明细见 `artifacts/reports/signal_density.md`。θ = 0.30/0.50/0.70 的日均信号数需 M0 信号，本地无信号文件，未运行。
- 未运行（依赖服务器或重建后的数据集）：第 13 步 IOC 未成交率统计（需 M10~M12 原始逐笔）；第 15 步 pull-data 后的两端 SHA256 对比与数据集重建；第 19 步实测点差分位数（需 bookTicker 录制 ≥ 7 天）；第 20 步嵌套确认 6 组真实结果。
- 风控停机模式（第 18 步）：`risk.halt_mode` 默认 `strict`（严格停机）；另一选项 `reset`（停 `halt_reset_days` 天后按当时权益继续）已实现，选哪一个与闸门口径需使用者确认（AGENTS.md §6 红线 5）。

- 真实数据冒烟（2026-11-13，本地 C001、M1~M22 bars 1006560 行、146007 个 p=1 信号、θ=0）：`run_backtest` 用时 10.53 秒，成交 68 笔、跳过 145939 条；跳过原因含 `entry_price_stale` 12455、`halted` 132680、`chase` 23、`no_entry_price` 1；风控触发 R5（2025-01-11、2025-01-12）与 R8（2025-01-13）后按 `halt_mode=strict` 停止交易；期末权益 83.61（起始 100）。该次冒烟说明：单币、全部预过滤样本开仓时，R5/R8 会在两周内触发，回测剩余月份不再交易——闸门运行需要按真实仓位口径复核停机模式（见 `.clinerules` 与 06 §2）。
- 数据质量：C001 的 M1~M22 bars 中有 34 个分钟 `p_1s` 与 `p_1s_filled` 同时为 NaN（`is_filled = 0`）。回测把这类分钟按 `no_entry_price` 放弃，不把 NaN 当作“非陈旧价”也不当作 1。



## 2026-11-14 流式低内存改造与全量嵌套确认（结论）

- `scripts/tuning_exits.py` 新增 `--low-memory`（需 `--features-dir`）：不常驻 bars/特征，第一遍按币重建标签只留标签数组，第二遍按币读落盘特征、直接写入预分配输出（峰值 ≈ 2,000,000 × 4F × 4B），组合层预计算按币加载验证窗口 bars 后释放。单测 `tests/test_tuning_exits_streaming.py` 证明样本数组与内存模式逐字段一致。服务器全量（169 币、ds_20261114b）实测峰值 RSS 约 **2.5 GB**（原设计需约 15 GB），6 组总耗时约 **65 分钟**。
- 新增 `--dump-signals`：导出 M10~M12 的 M0 预测概率（271,136 行）供信号密度统计。**θ 日均信号数（3 个月、按 30 天/月）**：θ=0.30 → **1.69**，θ=0.50 → **0.00**，θ=0.70 → **0.00**。
- 6 组嵌套确认结果（M10 早停 / M11 选 θ / M12 评估；6 组全部无法选出 θ）：

| 组 | 早停轮数 | 验证 PR-AUC | P50 | P90 | P99 | max | p≥0.30 条数 | θ=0.30 日均成交 | θ 网格内最高日均 |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| 1 | 13 | 0.2281 | 0.182 | 0.207 | 0.236 | 0.332 | 99 | 0.08 | 0.08 |
| 2 | 64 | 0.2184 | 0.159 | 0.226 | 0.289 | 0.457 | 1315 | 0.74 | 1.02（θ=0.32） |
| 3 | 31 | 0.2090 | 0.144 | 0.202 | 0.262 | 0.429 | 617 | 0.64 | 0.64 |
| 4 | 86 | 0.2174 | 0.157 | 0.227 | 0.298 | 0.462 | 1726 | 0.60 | 0.90 |
| 5 | 101 | 0.2030 | 0.131 | 0.215 | 0.302 | 0.516 | 1944 | 1.43 | 1.43 |
| 6 | 45 | 0.1923 | 0.112 | 0.190 | 0.265 | 0.441 | 947 | 0.76 | 0.76 |

- 结论：M0 的概率尺度整体低于 05 §7.2 的 θ 网格（P99 ≈ 0.24~0.30，θ≥0.46 没有任何候选），全部 6 组都达不到“验证段日均 ≥ 3 笔”，**嵌套确认无法执行**。按事先约定**保留冻结的出场与止损参数**（不改 `exits.yaml`/`features.yaml`），判定规则本身不修改。
- 需要使用者确认的方法规则问题：θ 网格 0.30~0.80 是否改为按分位数（如验证集 p 的 P95/P99）或按信号密度反推阈值；这属于 05 §7.2 的方法变更，必须在看测试段结果之前决定。M0 早停轮数（13~101）与 PR-AUC（0.19~0.23）也说明特征与标签的判别力有限，属 T4-3 第 2 步（模型超参搜索）要解决的问题。


## 2026-11-14 服务器实测（第 13/15/19/20 步）

- **数据集重建（第 15 步，服务器 ds_20261114b）**：用修复后的 bars 重建，`notional_ref = 100.0`、`rules_sha256` 与 `created_at` 已写入 manifest，`label_dropped.segment_tail.total = 4983`。新旧对比（同一 237 个币）：样本数 9,484,415 → **7,108,730（0.7495 倍）**，平均样本保留率 0.1910 → **0.1348**，平均正样本率 0.1931 → **0.2023**，warnings 4 → 215（新增 `p_1s` 为空但 `is_filled = 0` 的分钟告警与正样本率越界告警）。保留率下降来自本次新增的入场口径（陈旧价放弃、卖一近似追价）。
- **数据质量**：210/237 个币存在 `p_1s` 与 `p_1s_filled` 同为 NaN 而 `is_filled = 0` 的分钟，最多的 C034 64,289 分钟、C052 55,655、C170 41,224、C123 35,569、C190 29,503。这些分钟入场按 `no_entry_price` 放弃；构建已按币写入 `nan_entry_price_minutes` 与 warnings。
- **IOC 未成交率（第 13 步第二部分，服务器，M12 = 2025-09，S8 同组 3 币）**：XRPUSDT（C004）R0 信号 191 条、ARBUSDT（C023）131 条、STRKUSDT（C022）不在该月 `U(R)` 内为 0 条；合计 301 条可评估信号中 **26 条“可能未成交”（8.64%）**，129 条窗口内无主动买成交（另计）。“可能未成交”组单笔净收益均值 −0.0300R / −3.00bp，“成交”组 −0.0531R / −5.31bp：**未成交组并不更好**，因此不建议加入 `ioc_miss` 压力情景（与“按建议执行”的定案一致）。
- **实测点差（第 19 步，部分录制）**：2026-11-14 约 1.5 小时录制（38 币 bookTicker），全部分钟相对点差 P50：1000PEPE 0.23bp、ARB 0.71bp、ALGO 1.21bp、APT 1.96bp、BSB 3.83bp、ALLO 5.10bp；部分币显著高于 `half_spread_min = 1bp` 的假设，说明 `spread_measured` 情景有必要。信号分钟分位需录制期有 bars/信号，当前为 NaN。录制已按 7 天重启（`artifacts/live/book`，分段文件 `{YYYYMMDD}-{HHMMSS}.parquet`）。
- **嵌套确认（第 20 步，未完成）**：服务器 5 币冒烟跑通全流程（M10 早停、M11 选 θ、M12 评估），峰值内存 1.85GB，但 5 币在 M11 上没有任何 θ 达到“日均 ≥ 3 笔”，`select_threshold` 按规则报错。全量需 169 币：按单币 bars（≈85MB）+ 特征（≈35MB）+ 样本矩阵（≈84MB）估算需约 15GB 内存，服务器 3.6GB、本机 16GB（当前空闲约 4GB）均不足；需要先做 `tuning_exits` 的流式化改造（按币构造样本后立即释放 bars/特征，全局按 2,000,000 上限预留），再用 ≥ 32GB 机器运行。
- **theta 日均信号数（第 22 步剩余部分）**：需 M0 概率信号；M0 训练与上面同受内存限制，未运行。
- 服务器上另留有 `ds_20261114`（失败的部分重建，49 币）与 `ds_smoke_server`、`ds_smoke_server2`、`ds_probe`（冒烟用）等临时目录，待使用者确认后清理。

## 2026-11-13 待决项按建议定案

- **停机模式**：`risk.halt_mode = strict`（严格停机，触发 R6/R8 后停止交易至本次运行结束）作为闸门口径；`reset` 保留为可选实现，不改默认。
- **reference 本金统一为 100 USDT**：`06 §2` yaml 示例的 `reference.initial_equity` 由 10000.0 改为 100.0，`06 §4.2`、`06 §5.1` 表与 `06 §5.5` 扫描口径同步为 100；代码默认值同步（`ReferenceConfig.initial_equity` 100.0、`sweep.sizing_sweep(initial_equity=100.0)`）。原先 `06 §5.1` 写 10000 而 `configs/backtest.yaml` 写 100 的不一致已消除。
- **G6 改为 100 USDT**：`08 §3 G6` 标题由“10 USDT”改为“100 USDT”，其余标准（每笔 20%、≥ 30 天且 ≥ 50 笔）不变；修改发生在查看任何测试段/封存集结果之前。
- **段尾样本**：保留“丢弃”现状（不做 `eod` 保留），只在 manifest 与报告首页报告条数，避免再引入一次标签规则变更。
- **验证月不拆分**：五折内早停、温度缩放与 θ 仍用同一验证月；调参期另设 `--nested-check`（M10 早停、M11 选 θ、M12 评估）作为选择偏差的独立核对，`model_meta.json` 记录 `pf_curve` 供复查。
- **`ioc_miss` 压力情景暂不加入**：只保留 `scripts/analyze_ioc_miss.py` 的报告口径，待实测“可能未成交”比例与收益差异证明有必要再议（需使用者新增情景授权）。
- **点差**：`base` 不改，只保留 `spread_measured` 只报告情景（方案 A）。
- **`p_1s` 为空但 `is_filled = 0` 的分钟**：不回改历史聚合，改为在 `build_dataset` 统计 `nan_entry_price_minutes` 并写入 `manifest.warnings`，回测按 `no_entry_price` 放弃。


