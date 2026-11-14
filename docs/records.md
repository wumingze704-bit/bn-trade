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


