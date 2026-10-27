# 09 币安数据与 API 说明

> 本项目的数据与交易**全部基于币安（Binance）**，不使用任何第三方行情源或交易所。
> 本文汇总项目用到的全部币安资源：用途、是否需要 API Key、在哪个阶段使用。接口路径、参数、权重、费率会变化，**实施与每次部署前都必须对照 [developers.binance.com](https://developers.binance.com) 最新文档核对**，并在 §9 记录核对日期。

## 1. 总览

| 资源 | 用途 | 需要 API Key | 使用阶段 | 使用位置 |
| :-- | :-- | :-- | :-- | :-- |
| 历史数据仓库 data.binance.vision | 下载过去一年 1m K 线、逐笔、资金费率、合约指标，用于选币、训练、回测 | 否 | P1~P4、每月重训 | 本地或服务器 |
| USDⓈ-M 合约 REST 公开行情 | 交易规则、K 线补齐、逐笔回补、服务器时间 | 否 | 选币（交易规则）、实盘 | 双端 |
| USDⓈ-M 合约 REST 账户与交易 | 费率、杠杆、下单、撤单、查单、持仓、成交 | **是**（签名） | P5 以后 | 实盘服务器 |
| USDⓈ-M 合约 WebSocket 行情 | 实时 1m K 线、逐笔、最优挂单 | 否 | P5 以后 | 实盘服务器 |
| USDⓈ-M 合约用户数据流 | 订单成交与账户变动推送 | **是**（listenKey） | P5 以后 | 实盘服务器 |
| USDⓈ-M 合约测试网 | `paper` 模式全流程演练 | 是（测试网单独的 Key） | P5、G4 | 实盘服务器 |

## 2. 域名与环境

| 环境 | REST | WebSocket |
| :-- | :-- | :-- |
| 正式网 | `https://fapi.binance.com` | `wss://fstream.binance.com`（组合流：`/stream?streams=a/b/c`） |
| 测试网 | `https://testnet.binancefuture.com` | 见官方文档测试网说明 |
| 历史数据 | `https://data.binance.vision` | — |

- 环境由 `live.yaml` 的 `mode` 决定：`paper` → 测试网；`shadow`、`live` → 正式网。代码中禁止出现“正式网与测试网混用”的路径（例如用测试网 Key 访问正式网）。
- 币安合约 WebSocket 的接入地址可能按流类型（公共 / 行情 / 私有）拆分，以最新文档为准，地址集中在 `trade.live.exchange.endpoints` 一处配置。
- 网络与部署要求见 02 §5。

## 3. 接口清单（USDⓈ-M 合约）

`trade.live.exchange` 只实现下表中的接口。新增接口需先更新本表。

### 3.1 公开行情（无需签名）

| 接口 | 用途 | 调用时机 |
| :-- | :-- | :-- |
| `GET /fapi/v1/ping`、`GET /fapi/v1/time` | 连通性、服务器时间、时钟偏差 | 启动、端点测速、每 10 分钟校时 |
| `GET /fapi/v1/exchangeInfo` | 交易对列表、上线时间、`tickSize`/`stepSize`/`minNotional` 过滤器、`rateLimits` | 选币、启动、每 6 小时 |
| `GET /fapi/v1/klines` | 1m K 线（预热、WS 迟到时补齐） | 启动预热、补齐 |
| `GET /fapi/v1/aggTrades` | 逐笔回补（`fromId`） | 逐笔缺口时 |
| `GET /fapi/v1/ticker/bookTicker` | 最优买卖价（WS 不可用时兜底） | 兜底 |
| `GET /fapi/v1/premiumIndex` | 标记价格、下次资金费时间 | 每 5 分钟 |
| `GET /fapi/v1/fundingRate` | 历史资金费率 | 日报核算 |

### 3.2 账户与交易（需签名，见 §5）

| 接口 | 用途 |
| :-- | :-- |
| `GET /fapi/v1/commissionRate` | 读取账户实际手续费率，核对 VIP0（§4） |
| `GET /fapi/v1/leverageBracket` | 杠杆分层与维持保证金率（06 §5.4 校验 3） |
| `GET /fapi/v1/positionSide/dual`、`POST` 同路径 | 查询 / 设置单向持仓模式 |
| `POST /fapi/v1/marginType` | 设置逐仓 ISOLATED |
| `POST /fapi/v1/leverage` | 设置杠杆（5x） |
| 账户、余额、持仓查询（`/fapi/v2` 或 `/fapi/v3` 的 `account`、`balance`、`positionRisk`，以最新版本为准） | 权益、可用保证金、持仓、对账 |
| `POST /fapi/v1/order` | 开仓（LIMIT IOC）、止盈（LIMIT GTC reduceOnly）、超时平仓（MARKET reduceOnly） |
| `GET /fapi/v1/order`、`DELETE /fapi/v1/order` | 按 `origClientOrderId` 查询、撤单 |
| `GET /fapi/v1/openOrders`、`DELETE /fapi/v1/allOpenOrders` | 对账、Kill Switch |
| 算法条件单接口（止损 STOP_MARKET 的下单、撤单、查询未完成条件单） | 止损单。币安已将合约条件单迁移至独立的算法订单接口，具体路径与参数以最新文档为准 |
| `GET /fapi/v1/userTrades` | 成交明细（对账、补记出场） |
| `GET /fapi/v1/income` | 资金费、手续费、已实现盈亏流水（日报核算） |
| `POST` / `PUT` / `DELETE /fapi/v1/listenKey` | 创建、续期、关闭用户数据流 |

### 3.3 WebSocket

| 流 | 用途 |
| :-- | :-- |
| `<symbol>@kline_1m` | 1m K 线（`x = true` 表示已收盘） |
| `<symbol>@aggTrade` | 归集逐笔（订单流原子量） |
| `<symbol>@bookTicker` | 最优买卖价与数量（开仓限价、价差风控 R9） |
| 用户数据流 `ORDER_TRADE_UPDATE` | 订单状态与成交 |
| 用户数据流 `ACCOUNT_UPDATE` | 余额与持仓变化 |
| 用户数据流 `listenKeyExpired` | listenKey 失效，需重建 |
| 用户数据流中条件单（算法订单）状态事件 | 止损单触发与状态，事件名以最新文档为准 |

### 3.4 历史数据仓库（data.binance.vision）

| 数据 | 路径（`{S}` 为交易对） | 用途 |
| :-- | :-- | :-- |
| 1m K 线 | `data/futures/um/monthly/klines/{S}/1m/`、`daily/...` | 特征、标签、回测 |
| 归集逐笔 | `data/futures/um/daily/aggTrades/{S}/` | 订单流特征、大单阈值 |
| 资金费率 | `data/futures/um/monthly/fundingRate/{S}/` | 回测资金费 |
| 合约指标 | `data/futures/um/daily/metrics/{S}/` | 持仓量变化特征（可选） |

每个 zip 均有同名 `.CHECKSUM` 校验文件。格式陷阱见 03 §1.1。

## 4. 手续费（VIP0）

- 使用者确认：**VIP0、不使用 BNB 抵扣**。配置值（`base.yaml`）：挂单 0.02%、吃单 0.05%（以币安官方费率页为准）。
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
5. 测试网在测试网网站单独申请 Key，写入 `BINANCE_TESTNET_*` 环境变量。

### 5.2 签名规则（`trade.live.exchange.signing`）

- 所有签名请求带 `timestamp`（毫秒，已按 07 §2 校正的服务器时间）与 `recvWindow`（默认 5000 ms），请求头 `X-MBX-APIKEY`。
- HMAC：对完整查询字符串（含 `timestamp`）用 Secret 做 HMAC-SHA256，十六进制结果作为 `signature` 参数。
- Ed25519：用私钥对同样的载荷签名，Base64 编码后作为 `signature`（需 URL 编码）。
- 签名实现必须有单测：用官方文档中的示例 Key 与载荷，校验输出与文档示例一致。

## 6. 限频

- 限额从 `exchangeInfo.rateLimits` 实时读取（请求权重 / 每分钟、下单数 / 每 10 秒与每分钟），**不在代码中硬编码**。
- 每个响应读取头部 `X-MBX-USED-WEIGHT-1M`、`X-MBX-ORDER-COUNT-1M`（以文档为准），用量 > 70% 时停止新开仓（07 §5.5），> 90% 时暂停所有非必要请求（预热、补齐）。
- 启动预热拉取 K 线的权重较大（16 币 × 10 天），`trade.live.market_data` 需按权重分批、跨多分钟完成，预热期间不开仓。
- 本策略稳态下的请求量很小：每分钟最多数笔订单 + 每 60 秒一次对账查询，远低于限额。

## 7. 测试网注意事项

- 测试网的流动性、价格与正式网不同，**测试网结果不能用于评价策略收益**，只用于验证流程（08 G4）。
- 测试网的交易对、过滤器与正式网可能不一致，`paper` 模式启动时以测试网自己的 exchangeInfo 为准。
- 测试网可能不支持全部新接口（如算法条件单），若不支持，在完成报告中注明并改为在正式网用最小下单额做一次人工演练。

## 8. 接口冒烟测试

`scripts/api_smoke.py --env {testnet|mainnet}`：

- `mainnet`：只调用公开接口与只读签名接口（时间、exchangeInfo、commissionRate、leverageBracket、账户与持仓查询），**不下单**。
- `testnet`：额外执行一次完整订单生命周期：开仓 IOC → 挂止损（算法条件单）→ 挂止盈 → 撤销 → 平仓 → 确认仓位为 0。
- 每次部署新版本前必须运行；任一步失败则不得以 `live` 模式启动。

## 9. 接口核对记录

| 核对日期 | 核对人 | 范围 | 结论 / 变更 |
| :-- | :-- | :-- | :-- |
| 待填写 | | §3 全部接口 | 初次实现时核对 |
