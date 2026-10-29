"""特征注册表测试。

规格：docs/04-特征与标签.md §3、§4.5；08 §2 P2 验收
"""

from __future__ import annotations

import pytest

from trade.features import (
    EXECUTION_ATOMIC_COLUMNS,
    FEATURES,
    FEATURES_BY_NAME,
    Feature,
    FeaturesConfig,
    enabled_features,
    feature_hash,
)


def test_registry_has_37_features() -> None:
    assert len(FEATURES) == 37
    assert len(FEATURES_BY_NAME) == 37


def test_registry_rejects_execution_atomic_as_feature() -> None:
    for name in EXECUTION_ATOMIC_COLUMNS:
        with pytest.raises(ValueError, match="执行类原子量"):
            Feature(name, "kline", lambda c, b: c["close"], 1, "none")


def test_feature_hash_deterministic_and_order_sensitive() -> None:
    features = list(FEATURES[:3])
    assert feature_hash(features) == feature_hash(features)
    assert feature_hash(features) != feature_hash([features[1], features[0], features[2]])


def test_feature_hash_changes_with_lookback() -> None:
    features = [Feature("x", "kline", lambda c, b: c["close"], 1, "none")]
    changed = [Feature("x", "kline", lambda c, b: c["close"], 2, "none")]
    assert feature_hash(features) != feature_hash(changed)


def test_enabled_features_default_all() -> None:
    assert len(enabled_features(FeaturesConfig())) == 37


def test_enabled_features_disabled() -> None:
    cfg = FeaturesConfig(features={"disabled": ["r_1", "funding"]})
    names = [f.name for f in enabled_features(cfg)]
    assert "r_1" not in names
    assert "funding" not in names
    assert len(names) == 35


def test_enabled_features_rejects_unknown() -> None:
    cfg = FeaturesConfig(features={"disabled": ["not_a_feature"]})
    with pytest.raises(ValueError, match="不存在"):
        enabled_features(cfg)
