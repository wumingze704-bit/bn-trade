"""事件预过滤与标签计算包。

规格：docs/02-系统架构与部署.md §2
"""

from trade.labels.prefilter import is_sample_mask, prefilter_mask
from trade.labels.triple_barrier import triple_barrier, uniqueness_weights

__all__ = [
    "is_sample_mask",
    "prefilter_mask",
    "triple_barrier",
    "uniqueness_weights",
]
