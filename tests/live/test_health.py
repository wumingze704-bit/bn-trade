"""健康监控单测。

规格：docs/07-实盘与风控.md §7.1 R10/R11；docs/09-币安数据与API.md §6
"""

from __future__ import annotations

from trade.live.health import LatencyMonitor, OrderErrorMonitor, WeightUsage


def test_latency_monitor_triggers_after_sustained_window() -> None:
    monitor = LatencyMonitor(lag_sec=2.0, sustained_sec=3)
    assert monitor.sample(2.5) is False
    assert monitor.sample(2.5) is False
    assert monitor.sample(2.5) is True  # 连续 3 次 > 2s


def test_latency_monitor_reset_clears_window() -> None:
    monitor = LatencyMonitor(lag_sec=2.0, sustained_sec=2)
    monitor.sample(3.0)
    monitor.reset()
    assert monitor.sample(3.0) is False


def test_order_error_monitor_counts_window() -> None:
    monitor = OrderErrorMonitor(limit=3, window_sec=300)
    assert monitor.record_error(1000) is False
    assert monitor.record_error(2000) is False
    assert monitor.record_error(3000) is True


def test_weight_usage_thresholds() -> None:
    usage = WeightUsage(open_limit=0.7, pause_limit=0.9)
    usage.update(0.6)
    assert usage.should_stop_opening is False
    usage.update(0.75)
    assert usage.should_stop_opening is True
    assert usage.should_pause is False
    usage.update(0.95)
    assert usage.should_pause is True
