"""特征流水线：批量计算与逐分钟增量计算共用同一份特征实现。

规格：docs/04-特征与标签.md §3~§5；docs/02-系统架构与部署.md §3（单一实现）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from trade.features.normalize import (
    EwmState,
    alpha_from_halflife,
    ewm_standardize,
    ewm_step,
    sanitize,
)
from trade.features.registry import Feature, FeaturesConfig

# 特征计算从 coin bars 读取的列（03 §2.3）。缺失列按 NaN 处理（含可选 futures 列）。
_COIN_FEATURE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "count",
    "taker_buy_quote_volume",
    "buy_quote",
    "sell_quote",
    "max_trade_quote",
    "max_buy_1s_quote",
    "whale_buy_quote",
    "whale_sell_quote",
    "whale_thr",
)
_BTC_FEATURE_COLUMNS = ("open_time", "close")
_FUTURES_COLUMNS = ("oi", "funding_rate")


def align_btc(coin_open_times: np.ndarray, btc_bars: pd.DataFrame) -> pd.DataFrame:
    """把 BTC 分钟数据按 open_time 对齐到本币（缺失分钟用上一值填充，04 §4.3）。"""
    coin_frame = pd.DataFrame({"open_time": np.asarray(coin_open_times, dtype="int64")})
    btc_sorted = btc_bars.sort_values("open_time")
    return pd.merge_asof(coin_frame, btc_sorted, on="open_time", direction="backward")


def _column_array(frame: pd.DataFrame, column: str, n: int) -> np.ndarray:
    if column in frame.columns:
        return frame[column].to_numpy(dtype="float64")
    return np.full(n, np.nan, dtype="float64")


def compute_raw(
    bars: pd.DataFrame, btc_bars: pd.DataFrame, features: Sequence[Feature]
) -> dict[str, np.ndarray]:
    """计算全部启用特征的原始值（未归一化），返回 name -> 数组。"""
    n = len(bars)
    coin: dict[str, np.ndarray] = {
        col: _column_array(bars, col, n) for col in _COIN_FEATURE_COLUMNS
    }
    for col in _FUTURES_COLUMNS:
        coin[col] = _column_array(bars, col, n)
    btc_aligned = align_btc(bars["open_time"].to_numpy(dtype="int64"), btc_bars)
    btc: dict[str, np.ndarray] = {
        col: _column_array(btc_aligned, col, n) for col in _BTC_FEATURE_COLUMNS
    }
    return {feature.name: feature.func(coin, btc) for feature in features}


def normalize_features(
    raw: Mapping[str, np.ndarray], features: Sequence[Feature], cfg: FeaturesConfig
) -> pd.DataFrame:
    """对原始特征做 EWM 标准化或裁剪（04 §5），NaN/Inf 先置 0。"""
    columns: dict[str, np.ndarray] = {}
    for feature in features:
        x = sanitize(raw[feature.name])
        if feature.norm == "ewm_z":
            columns[feature.name] = ewm_standardize(
                x, cfg.ewm.halflife_bars, cfg.ewm.clip, cfg.ewm.min_var
            )
        else:
            columns[feature.name] = np.clip(x, -cfg.ewm.clip, cfg.ewm.clip)
    return pd.DataFrame(columns)


def compute_features(
    bars: pd.DataFrame,
    btc_bars: pd.DataFrame,
    features: Sequence[Feature],
    cfg: FeaturesConfig,
    segments: np.ndarray | None = None,
) -> pd.DataFrame:
    """批量计算标准化特征，返回 open_time + 特征列 的 DataFrame。

    按数据段独立计算（03 §6）：每个连续段内的滚动窗口与 EWM 状态不跨越段界，段首
    作为预热。`segments` 为 None 时整段视为单一数据段（与旧行为一致）。
    """
    n = len(bars)
    if segments is None:
        segments = np.zeros(n, dtype="int64")
    segments = np.asarray(segments, dtype="int64")
    open_time = bars["open_time"].to_numpy(dtype="int64")
    parts: list[pd.DataFrame] = []
    for seg in np.unique(segments):
        idx = np.nonzero(segments == seg)[0]
        seg_bars = bars.iloc[idx].reset_index(drop=True)
        raw = compute_raw(seg_bars, btc_bars, features)
        result = normalize_features(raw, features, cfg)
        result.insert(0, "open_time", open_time[idx])
        parts.append(result)
    if not parts:
        return pd.DataFrame({"open_time": open_time})
    return pd.concat(parts, ignore_index=True)


class IncrementalComputer:
    """逐分钟增量计算（实盘用），与批量计算共用同一份特征函数与 EWM 递推。

    注意：为与离线批量结果逐点一致（08 §2 P2），本实现保留完整历史缓冲；
    实盘部署时的有界窗口优化与状态持久化属 P5 范畴（04 §5、07 §2）。
    """

    def __init__(self, features: Sequence[Feature], cfg: FeaturesConfig) -> None:
        self._features = list(features)
        self._cfg = cfg
        self._max_lookback = max(feature.lookback for feature in self._features)
        self._coin: dict[str, list[float]] = {}
        self._btc: dict[str, list[float]] = {}
        self._ewm: dict[str, EwmState] = {
            feature.name: EwmState(0.0, 1.0)
            for feature in self._features
            if feature.norm == "ewm_z"
        }
        self._alpha = alpha_from_halflife(cfg.ewm.halflife_bars)

    def update(
        self, coin_bar: Mapping[str, object], btc_bar: Mapping[str, object]
    ) -> dict[str, float]:
        """喂入一根新分钟（coin + 已对齐的 btc），返回该分钟的标准化特征。"""
        for col in (*_COIN_FEATURE_COLUMNS, *_FUTURES_COLUMNS):
            self._coin.setdefault(col, []).append(float(coin_bar.get(col, np.nan)))
        for col in _BTC_FEATURE_COLUMNS:
            self._btc.setdefault(col, []).append(float(btc_bar.get(col, np.nan)))

        coin = {key: np.asarray(value, dtype="float64") for key, value in self._coin.items()}
        btc = {key: np.asarray(value, dtype="float64") for key, value in self._btc.items()}
        # 滚动类特征只依赖末尾 max_lookback 根；atr_pct 为 Wilder 全前缀递推，需完整历史
        limit = self._max_lookback
        coin_window = {key: value[-limit:] for key, value in coin.items()}
        btc_window = {key: value[-limit:] for key, value in btc.items()}

        out: dict[str, float] = {}
        for feature in self._features:
            use_full = feature.name == "atr_pct"
            raw = feature.func(coin if use_full else coin_window, btc if use_full else btc_window)[
                -1
            ]
            x = float(raw) if np.isfinite(raw) else 0.0
            if feature.norm == "ewm_z":
                z, state = ewm_step(
                    self._ewm[feature.name],
                    x,
                    self._alpha,
                    self._cfg.ewm.clip,
                    self._cfg.ewm.min_var,
                )
                self._ewm[feature.name] = state
                out[feature.name] = z
            else:
                out[feature.name] = float(np.clip(x, -self._cfg.ewm.clip, self._cfg.ewm.clip))
        return out
