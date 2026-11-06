# 09 币安数据与 API 说明

> 本项目的数据与交易**全部基于币安（Binance）**，不使用任何第三方行情源或交易所。
> 本文汇总项目用到的全部币安资源：用途、是否需要 API Key、在哪个阶段使用。接口路径、参数、权重、费率会变化，**实施与每次部署前都必须对照 [developers.binance.com](https://developers.binance.com) 最新文档核对**，并在 §9 记录核对日期。

## 1. 总览

| 资源                             | 用途                                                                                                         | 需要 API Key           | 使用阶段                        | 使用位置                          |
| :------------------------------- | :----------------------------------------------------------------------------------------------------------- | :--------------------- | :------------------------------ | :-------------------------------- |
| 历史数据仓库 data.binance.vision | 下载最近 24 个月全市场日线（含已下架合约）与入选币的 1m K 线、逐笔、资金费率、合约指标，用于选币、训练、回测 | 否                     | P1~P4、每月重训、实盘订单流预热 | **服务器**（本地不访问，02 §5.2） |
| USDⓈ-M 合约 REST 公开行情        | 交易规则、K 线补齐、逐笔回补、服务器时间                                                                     | 否                     | 选币（交易规则）、实盘          | 服务器                            |
| USDⓈ-M 合约 REST 账户与交易      | 费率、杠杆、下单、撤单、查单、持仓、成交                                                                     | **是**（签名）         | P5 以后                         | 实盘服务器                        |
| USDⓈ-M 合约 WebSocket 行情       | 实时 1m K 线、逐笔、最优挂单                                                                                 | 否                     | P5 以后                         | 实盘服务器                        |
| USDⓈ-M 合约用户数据流            | 订单成交与账户变动推送                                                                                       | **是**（listenKey）    | P5 以后                         | 实盘服务器                        |
| USDⓈ-M 合约测试网                | `paper` 模式全流程演练                                                                                       | 是（测试网单独的 Key） | P5、G4                          | 实盘服务器                        |

## 2. 域名与环境

| 环境     | REST                                | WebSocket                                                      |
| :------- | :---------------------------------- | :------------------------------------------------------------- |
| 正式网   | `https://fapi.binance.com`          | `wss://fstream.binance.com`（组合流：`/stream?streams=a/b/c`） |
| 测试网   | `https://testnet.binancefuture.com` | 见官方文档测试网说明                                           |
| 历史数据 | `https://data.binance.vision`       | —                                                              |

- 环境由 `live.yaml` 的 `mode` 决定：`paper` → 测试网；`shadow`、`live` → 正式网。代码中禁止出现“正式网与测试网混用”的路径（例如用测试网 Key 访问正式网）。
- 币安合约 WebSocket 的接入地址可能按流类型（公共 / 行情 / 私有）拆分，以最新文档为准，地址集中在 `trade.live.exchange.endpoints` 一处配置。
- 网络与部署要求见 02 §5。

## 3. 接口清单（USDⓈ-M 合约）

`trade.live.exchange` 只实现下表中的接口。新增接口需先更新本表。

### 3.1 公开行情（无需签名）

| 接口                                     | 用途                                                                           | 调用时机                       |
| :--------------------------------------- | :----------------------------------------------------------------------------- | :----------------------------- |
| `GET /fapi/v1/ping`、`GET /fapi/v1/time` | 连通性、服务器时间、时钟偏差                                                   | 启动、端点测速、每 10 分钟校时 |
| `GET /fapi/v1/exchangeInfo`              | 交易对列表、上线时间、`tickSize`/`stepSize`/`minNotional` 过滤器、`rateLimits` | 选币、启动、每 6 小时          |
| `GET /fapi/v1/klines`                    | 1m K 线（预热、WS 迟到时补齐）                                                 | 启动预热、补齐                 |
| `GET /fapi/v1/aggTrades`                 | 逐笔回补（`fromId`）                                                           | 逐笔缺口时                     |
| `GET /fapi/v1/ticker/bookTicker`         | 最优买卖价（WS 不可用时兜底）                                                  | 兜底                           |
| `GET /fapi/v1/klines?interval=1d`        | 日线 K 线，月度再平衡计算上线时长与 30 天日成交额中位数（07 §3.5、03 §4.2）    | 每月 1 日再平衡时              |
| `GET /fapi/v1/premiumIndex`              | 标记价格、下次资金费时间                                                       | 每 5 分钟                      |
| `GET /fapi/v1/fundingRate`               | 历史资金费率                                                                   | 日报核算                       |

月度再平衡需要对全部 USDT 永续各拉一次日线（约数百次请求），权重以最新文档为准、待 P5 用 `api_smoke.py` 实测；按权重分批、跨多分钟完成，避开分钟收盘决策窗口（07 §4）。

### 3.2 账户与交易（需签名，见 §5）

| 接口                                                                                                     | 用途                                                                                                                                                              |
| :------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /fapi/v1/commissionRate`                                                                            | 读取账户实际手续费率，核对 VIP0（§4）                                                                                                                             |
| `GET /fapi/v1/leverageBracket`                                                                           | 杠杆分层与维持保证金率（06 §5.4 校验 3）                                                                                                                          |
| `GET /fapi/v1/positionSide/dual`、`POST` 同路径                                                          | 查询 / 设置单向持仓模式                                                                                                                                           |
| `POST /fapi/v1/marginType`                                                                               | 设置逐仓 ISOLATED                                                                                                                                                 |
| `POST /fapi/v1/leverage`                                                                                 | 设置杠杆（5x）                                                                                                                                                    |
| 账户、余额、持仓查询（`/fapi/v2` 或 `/fapi/v3` 的 `account`、`balance`、`positionRisk`，以最新版本为准） | 权益、可用保证金、持仓、对账                                                                                                                                      |
| `POST /fapi/v1/order`                                                                                    | 开仓（LIMIT IOC）、止盈（LIMIT GTC reduceOnly）、超时平仓（MARKET reduceOnly）                                                                                    |
| `GET /fapi/v1/order`、`DELETE /fapi/v1/order`                                                            | 按 `origClientOrderId` 查询、撤单                                                                                                                                 |
| `GET /fapi/v1/openOrders`、`DELETE /fapi/v1/allOpenOrders`                                               | 对账、Kill Switch                                                                                                                                                 |
| 算法条件单接口（`POST /fapi/v1/algoOrder`、`DELETE /fapi/v1/algoOrder`、`GET /fapi/v1/openAlgoOrders`、`GET /fapi/v1/algoOrder`） | 止损单与追踪改单（07 §5.2）。自 2025-12-09 起，经典 `POST /fapi/v1/order` 发送 STOP_MARKET 等条件单返回 `-4120`，必须走算法订单接口；实测参数见下 |

算法条件单实测参数（2026-11-05，东京服务器，测试网 `testnet.binancefuture.com`）：

- `POST /fapi/v1/algoOrder`：`algoType` 是「算法类别」，条件单固定为 **`CONDITIONAL`**，真正的 STOP_MARKET 放在 **`type`** 字段（`algoType=STOP_MARKET` 会返回 `-4500 Invalid algoType`）。其余字段：`side`、`quantity`、`reduceOnly`、`triggerPrice`、`triggerPriceType`（`CONTRACT_PRICE`）、`workingType`（`CONTRACT_PRICE`）、`newClientAlgoId`；响应含 `algoId`、`clientAlgoId`、`algoStatus`、`orderType`。
- `DELETE /fapi/v1/algoOrder`：按 `symbol` + `algoId`（或 `clientAlgoId`）撤销。
- `GET /fapi/v1/openAlgoOrders`：查询未触发条件单（`symbol` 可选）；`GET /fapi/v1/algoOrder`：按 `algoId` 或 `clientAlgoId` 查单张。
- 价格取整：tick 取整必须消掉浮点尾差（`round(x/tick)*tick` 会产生 `77710.90000000001`，被拒 `-1111 Precision is over the maximum`），统一用 `trade.live.orders.floor_to_tick`。

| `GET /fapi/v1/userTrades`                                                                                | 成交明细（对账、补记出场）                                                                                                                                        |
| `GET /fapi/v1/income`                                                                                    | 资金费、手续费、已实现盈亏流水（日报核算、断线缺口补齐）                                                                                                          |
| `POST` / `PUT` / `DELETE /fapi/v1/listenKey`                                                             | 创建、续期、关闭用户数据流（07 §3.1）                                                                                                                             |

`userTrades`、`allOrders` 单次查询的时间范围为最近 7 天（[币安公告](https://www.binance.com/es-LA/support/announcement/detail/6bc47f8b8a05445cb07b30454fec4084)），`income` 的查询窗口以最新文档为准，P5 用 `api_smoke.py` 核对。交易记录以状态库为准、永久保留（07 §6.1），这些接口只用于补齐断线缺口，必须在缺口发生后 7 天内执行。**看板不调用任何币安接口**（10 §1）。

### 3.3 WebSocket

| 流                              | 用途                                                   |
| :------------------------------ | :----------------------------------------------------- |
| `<symbol>@kline_1m`             | 1m K 线（`x = true` 表示已收盘）                       |
| `<symbol>@aggTrade`             | 归集逐笔（订单流原子量）                               |
| `<symbol>@bookTicker`           | 最优买卖价与数量（开仓限价、价差风控 R9）              |
| 用户数据流 `ORDER_TRADE_UPDATE` | 订单状态与成交                                         |
| 用户数据流 `ACCOUNT_UPDATE`     | 余额与持仓变化                                         |
| 用户数据流 `listenKeyExpired`   | listenKey 失效，需重建                                 |
| 用户数据流 `ALGO_UPDATE`        | 算法订单（止损单）挂出、触发与状态，字段以最新文档为准 |

两种写法并存，`trade.live.exchange.ws` 两者都兼容：

- 单流：`wss://<host>/ws/<streamName>`，消息即事件本体。
- 组合流：`wss://<host>/stream?streams=<a>/<b>/...`，消息为 `{"stream": "<名称>", "data": {事件}}`；`MarketDataFeed` 取 `data` 字段后交给同一套解析函数（07 §3.1）。
- `/ws/`（带尾斜杠）发 `SUBSCRIBE` 会被拒绝（HTTP 400）；需要主动订阅时用 `/ws`（无尾斜杠），返回 `{"result": null, "id": <id>}` 为成功 ACK。

实测（2026-11-05，东京服务器，记录见 §9）：

- 测试网 `wss://stream.binancefuture.com`：`btcusdt@kline_1m`、`btcusdt@aggTrade`、`btcusdt@bookTicker` 及三者组合流均正常推送（60 秒 1278 条；同日 `@kline_1m` 单流 65 秒 120 条）。
- 正式网 `wss://fstream.binance.com`：`@bookTicker`（8 秒 14948 条）、`@trade`（8 秒 2308 条）、`@depth@100ms` 正常；`@kline_1m`、`@aggTrade`、`@markPrice`、`@ticker`、`@miniTicker` 在 `/ws/`、`/stream?streams=`、`/ws`+`SUBSCRIBE` 三种写法下均 0 条（65 秒长窗口复测同样为 0，排除推送稀疏）。正式网 `trade` 事件带 `st` 字段，是测试网当前没有的字段。
- 官方文档站被 Cloudflare 挑战页拦截（HTTP 202、正文 0 字节），未能取得正文；正式网 `@kline_1m` / `@aggTrade` 的可用写法**仍未确定**，`paper` 模式不受影响，`shadow` / `live` 的行情来源需在使用者确认前保持停摆。

### 3.4 历史数据仓库（data.binance.vision）

| 数据     | 路径（`{S}` 为交易对）                                | 用途                   |
| :------- | :---------------------------------------------------- | :--------------------- |
| 1m K 线  | `data/futures/um/monthly/klines/{S}/1m/`、`daily/...` | 特征、标签、回测       |
| 归集逐笔 | `data/futures/um/daily/aggTrades/{S}/`                | 订单流特征、大单阈值   |
| 资金费率 | `data/futures/um/monthly/fundingRate/{S}/`            | 回测资金费             |
| 合约指标 | `data/futures/um/daily/metrics/{S}/`                  | 持仓量变化特征（可选） |

每个 zip 均有同名 `.CHECKSUM` 校验文件。格式陷阱见 03 §1.1。

## 4. 手续费（VIP0）

- **VIP0、不使用 BNB 抵扣**。配置值（`base.yaml`）：挂单 0.02%、吃单 0.05%（以币安官方费率页为准）。
- 实盘启动时调用 `GET /fapi/v1/commissionRate`（任取一个交易币）读取 `makerCommissionRate`、`takerCommissionRate`，与配置比对（07 §2 第 6 步）。
- 回测与实盘使用同一份费率配置；费率变化后必须重跑回测，因为本策略对成本极其敏感（06 §4.1）。

## 5. API Key 与签名

### 5.1 创建步骤（使用者操作）

1. 建议先创建**子账户**，只划入策略资金（07 §9）。
2. 在该账户中开通 USDⓈ-M 合约账户（首次需要完成合约开通流程）。
3. 在“API 管理”中创建 API Key：
   - 类型：优先选择**自行生成 Ed25519 密钥对**并上传公钥；也可使用系统生成的 HMAC 密钥。
   - 权限：勾选“允许读取”和“允许合约”；**不要**勾选提现、内部划转、杠杆借贷等其他权限。
   - IP 访问限制：选择“仅限受信任 IP”，填入实盘服务器的固定公网 IP。
4. 把 Key 与 Secret（或私钥文件路径）写入服务器 `/etc/trade/env`（02 §7）。
5. 测试网在**合约测试网**申请 Key，写入 `BINANCE_TESTNET_*` 环境变量。
   - 网址是 `https://testnet.binancefuture.com`（U 本位合约测试网）；**不要**用 `https://testnet.binance.vision`（现货测试网），两者 key 完全不通用，用错时合约接口返回 `-2015 Invalid API-key, IP, or permissions`（实测：同一把现货测试网 key 打 `/api/v3/account` 返回 200、打 `/fapi/v2/account` 返回 -2015）。
   - 该站只支持 GitHub 账号登录；登录后默认给测试资金，在页面底部“API Key”处生成 Key 与 Secret（Secret 只显示一次）。
   - 测试网 key 长期不用会被自动删除，失效后重新生成即可。

### 5.2 签名规则

签名 REST 默认由官方 SDK `binance-sdk-derivatives-trading-usds-futures`（锁定版本，02 §4）完成，`trade.live.exchange.client` 只做封装。以下规则用于核对 SDK 行为，以及自研替换时的实现规格：

- 所有签名请求带 `timestamp`（毫秒，已按 07 §2 校正的服务器时间）与 `recvWindow`（默认 5000 ms），请求头 `X-MBX-APIKEY`。
- HMAC：对完整查询字符串（含 `timestamp`）用 Secret 做 HMAC-SHA256，十六进制结果作为 `signature` 参数。
- RSA：对同样的载荷用 RSA 私钥做 SHA256withRSA（PKCS#1 v1.5），Base64 后作为 `signature`（需 URL 编码）；私钥为 PEM（PKCS#8 `BEGIN PRIVATE KEY` 或 PKCS#1 `BEGIN RSA PRIVATE KEY`），实现用已锁定的 `pycryptodome`（`trade.live.exchange.signing.rsa_signature`），不新增依赖。
- Ed25519：用私钥对同样的载荷签名，Base64 编码后作为 `signature`（需 URL 编码）。
- 三者在同一个 `ExchangeClient` 上通过构造参数互斥选择（`api_secret` / `rsa_private_key_pem` / `private_key_hex`）；私钥文件由 `BINANCE_PRIVATE_KEY_PATH` 指向，签名方式按 PEM 头自动判别（有 PEM 头即 RSA，纯十六进制即 Ed25519），见 `private_key_kind`。
- 签名实现必须有单测：HMAC 用官方文档示例 Key 与载荷校验输出与文档一致；RSA / Ed25519 用临时密钥对做签名-验签往返。

## 6. 限频

- 限额从 `exchangeInfo.rateLimits` 实时读取（请求权重 / 每分钟、下单数 / 每 10 秒与每分钟），**不在代码中硬编码**。
- 每个响应读取头部 `X-MBX-USED-WEIGHT-1M`、`X-MBX-ORDER-COUNT-1M`（以文档为准），用量 > 70% 时停止新开仓（07 §5.5），> 90% 时暂停所有非必要请求（预热、补齐）。
- 首次启动预热拉取 K 线的权重较大（交易范围约 60 个币 + BTCUSDT × 30 天 ≈ 每币 29 次请求，合计约 1800 次），`trade.live.market_data` 需按权重分批、跨多分钟完成，预热期间不开仓；有近期 EWM 状态时只补缺口（07 §2）。月度再平衡时只对新进入的币做完整预热。
- 月度再平衡拉取全市场日线（见 §3.1 说明），每月一次，用量计入 `X-MBX-USED-WEIGHT-1M` 监控。
- WebSocket 单连接订阅数与每秒订阅消息数有上限（以最新文档为准）：约 60 个币 × 3 类流时按上限拆分为多个连接（07 §3.1）。
- 本策略稳态下的请求量很小：每分钟最多数笔订单与追踪改单 + 每 60 秒一次对账查询，远低于限额。

## 7. 测试网注意事项

- 测试网的流动性、价格与正式网不同，**测试网结果不能用于评价策略收益**，只用于验证流程（08 G4）。
- 测试网的交易对、过滤器与正式网可能不一致，`paper` 模式启动时以测试网自己的 exchangeInfo 为准。
- 测试网可能不支持全部新接口（如算法条件单），若不支持，在完成报告中注明并改为在正式网用最小下单额做一次人工演练。
- 实测（2026-11-05）：**测试网支持算法条件单**（`POST /fapi/v1/algoOrder`，`algoType=CONDITIONAL` + `type=STOP_MARKET` 挂单、撤单、`GET /fapi/v1/openAlgoOrders` 均正常），但 `GET /fapi/v1/algoOpenOrders` 路径不存在（-5000），参数细节见 §3.2。

## 8. 接口冒烟测试

`scripts/api_smoke.py --env {testnet|mainnet}`：

- `mainnet`：只调用公开接口与只读签名接口（时间、exchangeInfo、commissionRate、leverageBracket、账户与持仓查询），**不下单**。
- `testnet`：额外执行一次完整订单生命周期：开仓 IOC → 挂止损（算法条件单）→ 挂止盈 → 撤销 → 平仓 → 确认仓位为 0。
- `--ws-check [--ws-seconds N]`：只做行情订阅冒烟，**不需要密钥**——订阅 `<symbol>@kline_1m` + `@aggTrade` + `@bookTicker` 组合流，统计各类消息数、验证断线自动重连与缺口回补，退出码 0 表示三类流都收到消息。
- 未设置对应密钥环境变量（`BINANCE_TESTNET_API_KEY` / `BINANCE_API_KEY`，02 §7）时，只跑公开接口，签名与下单部分打印 `[SKIP]` 并以公开检查结果作为退出码；缺密钥导致的 SKIP 不算失败。
- 每次部署新版本前必须运行；任一步失败则不得以 `live` 模式启动。

## 9. 接口核对记录

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
