# P5/P6 旧任务清单摘录

摘自旧任务清单，是为分钟级策略设计的，只供实盘工程任务参考；实际方案以 `指令/自动多轮/3_实盘工程与看板.txt` 为准。

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
  - 完成（2026-11-12，第 9 步）：WebSocket 按流类别拆分路由（`public`/`market`/`private`），`endpoints.stream_route` 做流名映射、`MarketDataFeed` 按路由分组并受单连接 1024 条流上限切分；`api_smoke.py --ws-check` 打印每条连接地址。正式网 60 秒实测：`/public`+`@bookTicker` 64967 条、`/market`+`@kline_1m` 180 条、`/market`+`@aggTrade` 1831 条，退出码 0（原始输出见 docs/records.md）。
  - 缺：原子量 parquet 每小时落盘未接；单连接订阅数上限的拆分只用单测覆盖，未在满负荷下实测；T1-5 的“实时 aggTrade 与次日历史文件逐条比对”仍需在服务器连续录制 1 个 UTC 日，未运行。

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


## 与 P5/P6 有关的待决定事项（摘自旧清单）

3. **`paper` 模式的合约测试网端点**（09 §2、§3）：`https://testnet.binancefuture.com` 的网页路径已 301 跳转到 `https://demo.binance.com/...`，而 `https://demo-fapi.binance.com`（REST）与 `wss://fstream-demo.binance.com`（WS）可用。需确认 `paper` 模式是否切换为 demo 域名，确认后再改 `trade.live.exchange.endpoints` 并同步 09 §2。
4. **`shadow` / `live` 的行情来源**（09 §3.3，详见 T5-7 条目）：正式网 `@kline_1m` / `@aggTrade` 在 `/ws/`、`/stream?streams=`、`/ws`+`SUBSCRIBE` 三种写法下均无消息，K 线与逐笔的正式网来源尚未确定。
