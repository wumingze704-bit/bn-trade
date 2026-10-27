"""延迟统计函数测试。

规格：docs/08-开发计划与验收.md §2 P0
"""

import pytest

from scripts.latency_probe import percentile


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([8.0, 2.0, 5.0, 1.0, 9.0], 0.50) == 5.0
    assert percentile([8.0, 2.0, 5.0, 1.0, 9.0], 0.95) == 9.0


@pytest.mark.parametrize(
    ("samples", "percentage"),
    [([], 0.5), ([1.0], 0.0), ([1.0], 1.1)],
)
def test_percentile_rejects_invalid_input(samples: list[float], percentage: float) -> None:
    with pytest.raises(ValueError):
        percentile(samples, percentage)
