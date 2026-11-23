# 第二轮研究进度（重做研究 → 迭代确认）

> 只记进度与下一步；结论见 `docs/rotation/round2.md` 与 `docs/rotation/phase2.md`。

## 当前状态（2026-11-19）

- [x] 协议与数据样本安排（decisions.md 第 27~31 条）：不碰封存集，开发期 M2~M12 + 验证折 V1/V2
- [x] 新增 `scripts/rotation_grid_scan.py`（复用冻结实现，按 dev/V1/V2 切片）
- [x] 第 1 轮 8 组跑完（base）：V1 全负，无可选组
- [x] 结论：停止、不改预登记规则、不在已看窗口继续搜参；`phase2.md` 维持 FAIL
- [ ] 提交（本轮文件）；push 仍受本机无法连 github.com 的限制

## 下一步（需使用者决定才能继续）

1. 若继续这条路线：需要新的策略假设（改 `trade.rotation` 代码）＋从未看过的评估数据（2026-10 及以后需服务器下载并聚合，选币快照须使用者审阅）。
2. 若接受失败：冻结配置与 `phase2.md` 保持现状，不再运行 confirm。
