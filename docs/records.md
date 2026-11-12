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


