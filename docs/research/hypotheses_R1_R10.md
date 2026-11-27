# R1~R10 预登记假设与网格

R1~R10 的预登记假设与网格；不含任何运行结果。

来源：`docs/research/rounds/<轮次>.md` 的“假设（先写后跑）”一节原文（该目录在研究重置中删除，
全文见 git 历史），以及 `configs/strategies/round_<轮次>.yaml` 的参数列表。

## R1 截面动量多空（xs_momentum_ls）

### 假设（先写后跑）

- 经济机制：加密永续市场里，近期相对强势的币继续跑赢、弱势的币继续跑输，源于信息扩散缓慢与注意力效应；多空对冲后去掉大盘方向。
- 预期收益来源：截面动量的多空价差；资金费过滤避开拥挤方向（高正费率币多为人气多头，做空它们会被资金费侵蚀）。
- 可能失效的情形：趋势反转月、山寨整体高波动、成交额骤降导致滑点上升、资金费极端化。
- 网格（12 组）：`L ∈ {7, 14, 28}` 天 × `K ∈ {3, 5}` × 调仓 `{8h, 24h}`；止损 3 × ATR14(4h) ≤ 15%；缓冲区 2K。
- 打分 = 过去 L 天对数收益（截止决策前 4 小时）÷ 同窗口 1h 收益标准差。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`，缺口见 `docs/research/decisions.md`（重置前的记录，只在 git 历史中）。

### 配置网格（`configs/strategies/round_R1.yaml`）

```yaml
# R1 截面动量多空网格（docs/13 §5；指令第八部分）
round: R1
family: xs_momentum_ls
exposure: neutral
params:
  - {lookback_days: 7, top_k: 3, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 3, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 7, top_k: 3, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 3, rebalance_hours: 24, sizing_mode: vol_target}
  - {lookback_days: 7, top_k: 5, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 5, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 7, top_k: 5, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 5, rebalance_hours: 24, sizing_mode: vol_target}
  - {lookback_days: 14, top_k: 3, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 3, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 14, top_k: 3, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 3, rebalance_hours: 24, sizing_mode: vol_target}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 24, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 24, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 8, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 24, sizing_mode: vol_target}
```

## R2 资金费拥挤多空（funding_carry_ls）

### 假设（先写后跑）

- 经济机制：资金费率是永续多空拥挤度的直接价格。费率持续偏高的币说明多头拥挤、持仓成本高，未来
  多头平仓压力更大；费率偏低（负）的币说明空头拥挤。
- 预期收益来源：做空高费率、做多低费率，赚取拥挤度回落与资金费收入（多头收负费率、空头收正费率）。
- 可能失效的情形：单边趋势期资金费长期同号、交易所调整结算间隔、费率数据缺口。
- 网格（6 组）：`D ∈ {1, 3, 7}` 天 × `K ∈ {3, 5}`；每 8 小时调仓；止损 3 × ATR14(4h) ≤ 15%；缓冲区 2K。
- 打分 = 过去 D 天平均每小时资金费率（只用已结算的结算点）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；资金费按实际结算间隔换算为每小时费率。

### 配置网格（`configs/strategies/round_R2.yaml`）

```yaml
# R2 资金费拥挤多空网格（docs/13 §5；指令第八部分）
round: R2
family: funding_carry_ls
exposure: neutral
params:
  - {lookback_days: 1, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 1, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 1, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 1, top_k: 5, sizing_mode: vol_target}
  - {lookback_days: 3, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 3, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 3, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 3, top_k: 5, sizing_mode: vol_target}
  - {lookback_days: 7, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 7, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 7, top_k: 5, sizing_mode: vol_target}
```

## R3 时间序列趋势（ts_trend）

### 假设（先写后跑）

- 经济机制：加密资产存在时间序列动量，BTC/ETH 与高流动性山寨的中期收益方向延续。
- 预期收益来源：方向性趋势收益；按波动等风险控制单币权重，BTC 过滤降低逆势开仓。
- 可能失效的情形：震荡市、BTC 与山寨分化、波动率剧变导致等风险权重失衡。
- 网格（8 组）：`L ∈ {10, 20, 40, 80}` 天 × BTC 过滤 {是, 否}；每 24 小时调仓；
  止损 3 × ATR14(1d) ≤ 15%。

### 配置网格（`configs/strategies/round_R3.yaml`）

```yaml
# R3 时间序列趋势网格（docs/13 §5；指令第八部分）
round: R3
family: ts_trend
exposure: directional
params:
  - {lookback_days: 10, btc_filter: 1, sizing_mode: fixed_risk}
  - {lookback_days: 10, btc_filter: 1, sizing_mode: vol_target}
  - {lookback_days: 10, btc_filter: 0, sizing_mode: fixed_risk}
  - {lookback_days: 10, btc_filter: 0, sizing_mode: vol_target}
  - {lookback_days: 20, btc_filter: 1, sizing_mode: fixed_risk}
  - {lookback_days: 20, btc_filter: 1, sizing_mode: vol_target}
  - {lookback_days: 20, btc_filter: 0, sizing_mode: fixed_risk}
  - {lookback_days: 20, btc_filter: 0, sizing_mode: vol_target}
  - {lookback_days: 40, btc_filter: 1, sizing_mode: fixed_risk}
  - {lookback_days: 40, btc_filter: 1, sizing_mode: vol_target}
  - {lookback_days: 40, btc_filter: 0, sizing_mode: fixed_risk}
  - {lookback_days: 40, btc_filter: 0, sizing_mode: vol_target}
  - {lookback_days: 80, btc_filter: 1, sizing_mode: fixed_risk}
  - {lookback_days: 80, btc_filter: 1, sizing_mode: vol_target}
  - {lookback_days: 80, btc_filter: 0, sizing_mode: fixed_risk}
  - {lookback_days: 80, btc_filter: 0, sizing_mode: vol_target}
```

## R4 短期反转多空（xs_reversal_ls）

### 假设（先写后跑）

- 经济机制：短期（1~3 天）涨幅过大或跌幅过大的山寨币出现流动性冲击后的过度反应，随后部分回归；
  多空对冲去掉大盘方向。
- 预期收益来源：短周期均值回复；排除缩量币避免无法成交的极端价格。
- 可能失效的情形：单边趋势市（反转策略持续逆势）、缩量币过滤过严导致标的不足。
- 网格（6 组）：`L ∈ {1, 2, 3}` 天 × `K ∈ {3, 5}`；每 24 小时调仓；排除当日成交额低于 30 天日成交额
  中位数一半的币；止损 3 × ATR14(4h) ≤ 15%；缓冲区 2K。
- 数据：研究期 2021-02-01 ~ 2026-07-31。

### 配置网格（`configs/strategies/round_R4.yaml`）

```yaml
# R4 短期反转多空网格（docs/13 §5；指令第八部分）
round: R4
family: xs_reversal_ls
exposure: neutral
params:
  - {lookback_days: 1, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 1, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 1, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 1, top_k: 5, sizing_mode: vol_target}
  - {lookback_days: 2, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 2, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 2, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 2, top_k: 5, sizing_mode: vol_target}
  - {lookback_days: 3, top_k: 3, sizing_mode: fixed_risk}
  - {lookback_days: 3, top_k: 3, sizing_mode: vol_target}
  - {lookback_days: 3, top_k: 5, sizing_mode: fixed_risk}
  - {lookback_days: 3, top_k: 5, sizing_mode: vol_target}
```

## R5 组合（ensemble）

### 假设（先写后跑）

- 经济机制：动量、资金费拥挤与短期反转三个方向的收益来源不同、相关性较低，等权合成可以降低单一
  风格失效时的回撤。
- 成分事先固定：R1 取 L14 K3，R2 取 D1 K3，R4 取 L3 K5；打分做截面 z 分数后等权相加，不拟合权重、
  不按结果筛选成分。
- 网格（4 组）：合成后 `K ∈ {3, 5}` × 调仓 `{8h, 24h}`；止损 3 × ATR14(4h) ≤ 15%。
- 数据：研究期 2021-02-01 ~ 2026-07-31。

### 配置网格（`configs/strategies/round_R5.yaml`）

```yaml
# R5 组合网格（docs/13 §5、1 号第八部分 R5）：成分参数不写死，
# 每一折只用该折开始之前的数据在下面三个网格里按年化夏普选参（1 号第七部分第 3 条）。
round: R5
family: ensemble
exposure: neutral
component_grids:
  xs_momentum_ls: configs/strategies/round_R1.yaml
  funding_carry_ls: configs/strategies/round_R2.yaml
  xs_reversal_ls: configs/strategies/round_R4.yaml
params:
  - {top_k: 3, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {top_k: 3, rebalance_hours: 8, sizing_mode: vol_target}
  - {top_k: 3, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {top_k: 3, rebalance_hours: 24, sizing_mode: vol_target}
  - {top_k: 5, rebalance_hours: 8, sizing_mode: fixed_risk}
  - {top_k: 5, rebalance_hours: 8, sizing_mode: vol_target}
  - {top_k: 5, rebalance_hours: 24, sizing_mode: fixed_risk}
  - {top_k: 5, rebalance_hours: 24, sizing_mode: vol_target}
```

## R6 资金费调整的横截面动量（funding_adj_momentum）

### 假设（先写后跑）

- 经济机制：横截面动量的收益来自信息扩散缓慢；但永续合约的资金费率给出了“持仓拥挤成本”的直接价格。
  同样强势的币，如果多头拥挤（资金费高），未来多头平仓压力更大、持仓成本更高；同样弱势的币，如果空头
  拥挤（资金费为负），未来空头回补压力更大。把资金费作为连续惩罚项加进动量打分，可以避开“靠资金费
  堆出来的动量”，这是 R1（资金费只在 10%/90% 分位做硬过滤）与 R2（纯资金费排序）之外的一种交互用法。
- 预期收益来源：动量多空价差减去拥挤成本的修正；周频调仓压低换手与成本。
- 可能失效的情形：动量方向反转；资金费长期同号（单边行情）；周频调仓使止损后的仓位要等到下一周才补。
- 打分：`score = z(动量) − λ × z(过去 D 天平均小时化资金费)`。动量 = 过去 L 天对数收益（截止决策前 4 小时）
  ÷ `(同窗口 1h 收益标准差 × √L×24)`，即 t 统计量的量纲；两个分量都在当月 U(R) 内做截面 z 分数。
- 选币：做多 score 最高 K 个、做空最低 K 个；缓冲区 2K；止损 3 × ATR14(4h)，≤ 15%。
- 资金费缺失的币不参与打分（不按 0 计、不用默认值，红线 12）。
- 网格（9 组，≤10）：`λ ∈ {0, 1, 2}` × `L ∈ {14, 28, 56}` 天；`D = 7` 天、`K = 5`、调仓 168h（周频）固定。
  λ = 0 组是同网格内的对照（纯周频动量），不用于事后挑选方向。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30，并列取交易数少的）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`。

### 配置网格（`configs/strategies/round_R6.yaml`）

```yaml
# R6 资金费调整的横截面动量网格（docs/13 §5；指令第八部分 R6~R10）
# score = z(动量 t 统计量) − λ × z(过去 D 天平均小时化资金费)；周频调仓。
round: R6
family: funding_adj_momentum
exposure: neutral
params:
  - {lookback_days: 14, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 56, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 56, funding_days: 7, funding_weight: 0.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 14, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 56, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 56, funding_days: 7, funding_weight: 1.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 14, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 56, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 56, funding_days: 7, funding_weight: 2.0, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
```

## R7 低波动异常多空（xs_low_vol_ls）

### 假设（先写后跑）

- 经济机制：加密永续的散户需求偏好“彩票型”高波动币（高波动 = 高赔率），愿意为它支付溢价；同时杠杆受限
  的投资者更愿意持有低波动资产。若高波动币被系统性高估，做多低波动、做空高波动的截面组合应当获得
  波动率溢价。这里的排序变量是波动率水平本身，不是收益（动量 R1/R6）或资金费（R2），也不是短期反转 R4。
- 预期收益来源：截面波动率溢价；多空对冲去掉大盘方向；周频调仓压低换手与成本。
- 可能失效的情形：高波动币在单边行情中继续上涨（做空端被挤压）；流动性差的低波动币滑点大；周频调仓
  使止损后的仓位要等到下一周才补。
- 打分：`score = −过去 W 天 1h 对数收益标准差`（越低分越高）。排除当日 24 小时平均成交额低于 30 天日
  成交额中位数一半的缩量币（与 R4 相同的流动性门槛，避免不可成交的价格）。
- 选币：做多 score 最高（波动最低）K 个、做空最低（波动最高）K 个；缓冲区 2K；止损 3 × ATR14(4h)，≤ 15%。
- 网格（6 组，≤10）：`W ∈ {7, 14, 28}` 天 × `K ∈ {3, 5}`；调仓 168 小时（周频）固定。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30，并列取交易数少的）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`。

### 配置网格（`configs/strategies/round_R7.yaml`）

```yaml
# R7 低波动异常多空网格（docs/13 §5；指令第八部分 R6~R10）
# 打分 = −过去 W 天 1h 收益标准差；做多低波动、做空高波动；周频调仓。
round: R7
family: xs_low_vol_ls
exposure: neutral
params:
  - {vol_days: 7, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 7, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {vol_days: 7, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 7, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {vol_days: 14, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 14, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {vol_days: 14, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 14, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {vol_days: 28, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 28, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {vol_days: 28, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {vol_days: 28, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
```

## R8 波动率择时的横截面动量（vol_timed_momentum）

### 假设（先写后跑）

- 经济机制：加密资产波动率高度聚集，且高波动期往往伴随更差的截面收益分布（杠杆挤压与强平连锁）。
  在波动率高的时段按比例缩小暴露、在波动率低时保持满仓，可以在不改变选币方向的前提下降低尾部损失，
  提高单位风险的收益。这是暴露缩放（risk scaling），不是新的选币方向，也不是对某一段行情的过滤。
- 预期收益来源：把“信号正确但暴露过大”的损失压下去，使同一动量信号的夏普与最大回撤改善。
- 可能失效的情形：波动率上升后收益更好（低波动期反而是震荡市）；缩放后单仓名义低于最小下单额而被丢弃。
- 打分与选币：与 R6 的动量分量相同（过去 L 天对数收益 ÷（1h 收益标准差 × √窗口小时数），截止决策前 4 小时），
  做多最高 K 个、做空最低 K 个，缓冲区 2K，K = 5，调仓 168 小时。
- 暴露缩放：取所选目标的过去 24 小时 1h 波动均值，年化后得到组合波动估计 `σ_hat`；
  缩放系数 `s = min(1, σ_target / σ_hat)`，写入每个目标的 `weight`。`σ_target` 为网格参数。
- 波动数据缺失的目标不参与（不按默认值补齐，红线 12）。
- 止损 3 × ATR14(4h)，≤ 15%。
- 网格（6 组，≤10）：`σ_target ∈ {0.5, 0.8, 1.2}`（年化）× `L ∈ {14, 28}` 天。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30，并列取交易数少的）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`。

### 配置网格（`configs/strategies/round_R8.yaml`）

```yaml
# R8 波动率择时的横截面动量网格（docs/13 §5；指令第八部分 R6~R10）
# 动量选币 + 按组合波动估计缩放暴露（weight = min(1, σ_target / σ_hat)）；周频调仓。
round: R8
family: vol_timed_momentum
exposure: neutral
params:
  - {lookback_days: 14, target_annual_vol: 0.5, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, target_annual_vol: 0.5, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, target_annual_vol: 0.5, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, target_annual_vol: 0.5, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 14, target_annual_vol: 0.8, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, target_annual_vol: 0.8, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, target_annual_vol: 0.8, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, target_annual_vol: 0.8, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 14, target_annual_vol: 1.2, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, target_annual_vol: 1.2, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, target_annual_vol: 1.2, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, target_annual_vol: 1.2, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
```

## R9 BTC 对冲的单边动量（btc_hedged_momentum）

### 假设（先写后跑）

- 经济机制：山寨币的横截面动量里同时混着“相对强弱”与“对 BTC 的 beta”。只做多或用对称多空时，
  beta 会随行情变化主导收益。用 BTC 反向头寸把市场方向对冲掉，只留下相对强弱；再按 BTC 中期趋势决定
  做多强势组还是做空弱势组，因为动量收益在
  上涨与下跌阶段分布在不同的腿（上涨期强势币延续、下跌期弱势币补跌）。
- 预期收益来源：山寨对 BTC 的相对强弱（alpha），扣除 BTC 对冲成本与资金费。
- 可能失效的情形：山寨与 BTC 同涨同跌（对冲后无收益）；BTC 趋势判错导致站错边；对冲比例与仓位取整不匹配。
- 打分：山寨动量 = 过去 L 天对数收益 ÷（1h 收益标准差 × √窗口小时数），截止决策前 4 小时。
- 方向：BTC 过去 T 天对数收益 > 0 → 做多动量最高的 K 个山寨、用 BTC 空头对冲；< 0 → 做空最低的 K 个、
  用 BTC 多头对冲。对冲权重 = `hedge_weight`（网格固定 1.0，即与风控名义同量级）。
- 止损：山寨与 BTC 都用 3 × ATR14(4h)，≤ 15%。
- 网格（4 组，≤10）：`K ∈ {3, 5}` × `T ∈ {50, 100}` 天；`L = 28` 天、调仓 168 小时固定。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30，并列取交易数少的）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`。

### 配置网格（`configs/strategies/round_R9.yaml`）

```yaml
# R9 BTC 对冲的单边动量网格（docs/13 §5；指令第八部分 R6~R10）
# BTC T 天趋势决定做多/做空山寨单边，BTC 反向头寸对冲；周频调仓。
round: R9
family: btc_hedged_momentum
exposure: directional
params:
  - {lookback_days: 28, trend_days: 50, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, trend_days: 50, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, trend_days: 50, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, trend_days: 50, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, trend_days: 100, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, trend_days: 100, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, trend_days: 100, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, trend_days: 100, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
```

## R10 按流动性分层的动量（liquidity_tiered_momentum）

### 假设（先写后跑）

- 经济机制：加密山寨的信息扩散速度与流动性高度相关。全样本统一排序时，动量组合会被成交额最大的一层
  主导（大币涨跌慢、但权重高），真正的“信息扩散慢 + 还能成交”的中层币被挤出。先在流动性分层内部做
  z 分数、再跨层比较，让每一层都贡献候选，等于把“同层可比”这件事显式写进打分。
- 预期收益来源：中层流动性山寨的动量溢价；每层内部标准化降低大币主导。
- 可能失效的情形：层内样本太少（不足 2 个币时整层不参与）；分层边界随成交量漂移导致换手上升。
- 打分：动量 = 过去 L 天对数收益 ÷（1h 收益标准差 × √窗口小时数），截止决策前 4 小时；按 30 天日成交额
  中位数把当月 U(R) 分成 3 层（按成交额从高到低等分），层内做截面 z 分数后合并。
- 选币：做多合并分数最高 K 个、做空最低 K 个；缓冲区 2K；止损 3 × ATR14(4h)，≤ 15%。
- 网格（4 组，≤10）：`L ∈ {14, 28}` 天 × `K ∈ {3, 5}`；调仓 168 小时固定。
- 选参：每折用折前全部数据按年化夏普选参（训练期交易数 ≥ 30，并列取交易数少的）。
- 数据：研究期 2021-02-01 ~ 2026-07-31；U(R) 为 `data/universe_hist/`。

### 配置网格（`configs/strategies/round_R10.yaml`）

```yaml
# R10 按流动性分层的动量网格（docs/13 §5；指令第八部分 R6~R10）
# 动量在 30 天成交额分层的层内做 z 分数，再跨层选多空；周频调仓。
round: R10
family: liquidity_tiered_momentum
exposure: neutral
params:
  - {lookback_days: 14, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 14, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 3, rebalance_hours: 168, sizing_mode: vol_target}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 168, sizing_mode: fixed_risk}
  - {lookback_days: 28, top_k: 5, rebalance_hours: 168, sizing_mode: vol_target}
```
