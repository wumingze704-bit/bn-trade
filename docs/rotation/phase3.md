# phase3：最终检验（M23~M24 封存集 + 2026-10/2026-11 新数据）结论

结论：NOT_READY

- 结束原因：第二部分前置检查第 1 条不满足。`docs/rotation/phase2.md` 存在，但其结论为 **FAIL**（测试段 M13~M22 一次性确认回测的 6 项固定标准中第 1、3、5 项不通过），而 `final` 阶段要求 `phase2.md` 结论为 PASS。
- 因此本任务按指令“任一不满足就立即结束”停止：未下载 2026-10 / 2026-11 数据、未生成任何选币快照、未运行 `--stage final`（任何情景）、未产出 `artifacts/reports/rotation/rot_final/` 报告目录。
- 未运行的部分在下文逐项写“未运行”及原因，不用任何估计值或占位数字替代（AGENTS.md §6 红线 8）。

## 1. 前置检查结果

| # | 检查项 | 要求 | 实测 | 结论 |
| :- | :--- | :--- | :--- | :--- |
| 1 | `docs/rotation/phase2.md` | 存在且结论为 PASS | 存在（152 行），第 3 行为 `结论：FAIL`；§6 判定 6 项中第 1、3、5 项不通过 | **不满足** |
| 2 | 10 个冻结文件 SHA256 | 与 `phase1.md` §6 记录逐个一致 | 10 个全部一致（见 §2） | 满足 |
| 3 | `artifacts/reports/FINAL_RUNS.log` | 无 `run_id=rot_final` 的完成记录 | 文件不存在（从未写入），无任何 `rot_final` 记录 | 满足 |
| 4 | AGENTS.md §8 五条质量命令 | 全部通过 | 未运行（第 1 条已不满足，按指令立即结束；本任务无代码改动） | 未运行 |

检查 1 的原文证据（`docs/rotation/phase2.md`）：

- 第 3 行：`结论：FAIL`
- 第 8 行：`判定结果：6 项固定标准中第 1、3、5 项不通过，结论 FAIL。未根据测试段结果做任何调整。`
- §6 判定表：第 1 项 `net_return = −7.93%`、`sharpe = −0.711` 不通过；第 3 项 `cost2x` 净收益 `−11.22%` 不通过；第 5 项 `bootstrap_mean_low = −0.000964` 不通过。

检查 2 的核对方式：用 `hashlib.sha256(Path(file).read_bytes()).hexdigest()` 逐个计算，与 `phase1.md` §6 表格中的路径与哈希逐字比对。

## 2. 冻结文件 SHA256 核对

| 文件 | SHA256（phase1.md 记录 = 本次实测） | 结论 |
| :--- | :--- | :--- |
| `configs/rotation_frozen.yaml` | `e93cdba1fa8c256a8441efd0642b4816fea24032d13c68770bfb7160eeaddd6e` | 一致 |
| `configs/sizing.yaml` | `399e4e27680056751c3af06a38c57d667883f024b17d07108b25bc52d5f9dfb1` | 一致 |
| `scripts/rotation_backtest.py` | `e6bebb4b90ea22c6c09dbbe5d57536a00ca034f61a38220a2c50029184d5f727` | 一致 |
| `src/trade/rotation/__init__.py` | `dbcbe77270d2c43910b31a02e017da230a80d2c1dee3c28c036e4fdd2ece148d` | 一致 |
| `src/trade/rotation/config.py` | `169f9f4a12841aaa87ac207955874e61ad3df88c6bd810be6c7e0c4e3dba1954` | 一致 |
| `src/trade/rotation/data.py` | `22d47c6cdf81b7ef3e3642ba3341f5fa52994510b8e4641809dbb333c64ab9fa` | 一致 |
| `src/trade/rotation/engine.py` | `e6ee989057b6b72d9c0d4a75bb02c8ecd58a3dca7cabf5a5d301780557a4b3f0` | 一致 |
| `src/trade/rotation/metrics.py` | `1329bdc15c52c653d5e8be92ad8c4ff4f0775e2a46cce55bbb746321d8b9aef6` | 一致 |
| `src/trade/rotation/signals.py` | `19f161536c3c096d1734f691f6d4f746319d25dcba79365d68bac3b60de18421` | 一致 |
| `src/trade/rotation/stage.py` | `1f02e457068dc95d4717558d97d32235df5eb8f47ae9e3266cc496eac8f9fa90` | 一致 |

本任务未修改以上任何文件（`git status` 运行前为空）。

## 3. 阶段守卫实测

用冻结的解释器 `C:\Users\32837\AppData\Local\Programs\Python\Python313\python.exe`（3.13.7、pandas 3.1.0）执行：

```text
python scripts/rotation_backtest.py --stage final --run-id rot_final \
    --rotation configs/rotation_frozen.yaml --scenarios base --no-log
```

输出：

```text
Traceback (most recent call last):
  File "C:\Users\32837\Desktop\trade\scripts\rotation_backtest.py", line 608, in <module>
    sys.exit(main())
  File "C:\Users\32837\Desktop\trade\scripts\rotation_backtest.py", line 437, in main
    window = stage_window(args.stage, docs_dir=Path(args.docs), artifact_root=artifact_root)
  File "C:\Users\32837\Desktop\trade\src\trade\rotation\stage.py", line 135, in stage_window
    raise ValueError("final 阶段要求 docs/rotation/phase2.md 结论为 PASS（11 §7）")
ValueError: final 阶段要求 docs/rotation/phase2.md 结论为 PASS（11 §7）
退出码 1
```

`stage_window` 在 `main()` 中先于任何 `EXPERIMENTS.log` / `FINAL_RUNS.log` 写入被调用（`scripts/rotation_backtest.py` 第 437 行），因此该次尝试不产生任何日志或产物；运行后 `artifacts/reports/FINAL_RUNS.log` 仍不存在。

## 4. 新数据下载与质量统计

未运行。

- 未在东京服务器执行 `scripts/download_daily.py`、`scripts/select_coins.py --build --month`，未新建 `scripts/fetch_final_months.py`，未下载或聚合任何 1m K 线 / aggTrades / metrics / fundingRate。
- 未按 docs/03 §2.4 口径统计新月份每币 K 线缺失与逐笔缺失比例（无新数据可统计）。
- `data/` 下没有新增或修改任何文件。

## 5. 各月快照入选币数

未生成。

| 月份 | 快照 | U(R) 入选币数 |
| :--- | :--- | :--- |
| 2026-10 | 未生成 | 未生成 |
| 2026-11 | 未生成 | 未生成 |

未把任何快照的 `reviewed` 置 `true`（本任务未生成快照）。

## 6. 全部情景数字表

未运行：`base`、`cost2x`、`nohalt`、`delay1`、`shuffle`、`neighbors` 均未执行，`artifacts/reports/rotation/rot_final/` 目录不存在。

| 情景 | 年化夏普 | 净收益 | 最大回撤 | 笔数 | 亏损月数 | 最差月 | 成本占比 | 换手（年化） | bootstrap 95% 区间 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| base | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| cost2x | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| nohalt | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| delay1 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| shuffle | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |
| neighbors | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 | 未运行 |

## 7. 判定逐项结果

未判定：第六部分 5 条判据都需要 `base` / `cost2x` / `neighbors` 的实际运行数字，本次没有运行，因此不给 PASS/FAIL，也不推测任何一档。

| # | 判据 | 要求 | 实测 | 结论 |
| :- | :--- | :--- | :--- | :--- |
| 1 | base 净收益 | > 0 | 未运行 | 未判定 |
| 2 | base 最大回撤 | ≤ 25% | 未运行 | 未判定 |
| 3 | cost2x 净收益 | ≥ −2% | 未运行 | 未判定 |
| 4 | 4 个月亏损月 ≤ 2，最差月 ≥ −12% | 亏损月 ≤ 2 且最差月 ≥ −0.12 | 未运行 | 未判定 |
| 5 | neighbors 至少一半净收益 > 0 | ≥ 一半 | 未运行 | 未判定 |

## 8. 泄露检查结果

不适用：未运行任何情景，没有出现“月化 > 20% 或年化夏普 > 3”的结果需要检查；未读取 M23~M24（2026-08、2026-09）与 2026-10 / 2026-11 的任何数据文件。

## 9. 运行命令与报告目录

本任务实际执行的与回测相关的唯一命令即 §3 的守卫尝试（被拒绝、无产物）。

报告目录 `artifacts/reports/rotation/rot_final/`：不存在（未创建）。

`artifacts/reports/FINAL_RUNS.log`：不存在（未写入开始记录，因为运行在守卫处即被拒绝）。
`artifacts/reports/EXPERIMENTS.log`：未追加任何 `kind=final` 记录。

## 10. 结论与后续

- 冻结策略在测试段（M13~M22）的固定标准判定为 FAIL，代码层守卫据此禁止进入 `final` 阶段，`--stage final` 无法执行。
- 本任务不解除该前置条件、不修改冻结文件、不重新调参、不重跑测试段；M23~M24 封存集与 2026-10 / 2026-11 新数据保持未被评估的状态，仍可用于未来一次合格的最终检验。
- 后续是否继续（例如由使用者决定终止该策略方向，或另行下达新的研究任务）属于使用者决策，本任务不自行推进。
