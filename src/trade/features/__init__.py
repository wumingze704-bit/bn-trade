"""离线与实盘共用的特征计算包。

规格：docs/02-系统架构与部署.md §2
"""

from trade.features.normalize import EwmState, ewm_step
from trade.features.pipeline import IncrementalComputer, compute_features, compute_raw
from trade.features.registry import (
    EXECUTION_ATOMIC_COLUMNS,
    FEATURES,
    FEATURES_BY_NAME,
    Feature,
    FeaturesConfig,
    enabled_features,
    feature_hash,
)

__all__ = [
    "EXECUTION_ATOMIC_COLUMNS",
    "FEATURES",
    "FEATURES_BY_NAME",
    "EwmState",
    "Feature",
    "FeaturesConfig",
    "IncrementalComputer",
    "compute_features",
    "compute_raw",
    "enabled_features",
    "ewm_step",
    "feature_hash",
]
