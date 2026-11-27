"""R12 山寨篮子做空 + BTC 做多（中性，docs/13 §5）。

规格：1 号第八部分 R12；计算顺序按 docs/13 §3 与 1 号第五部分 B 第 15 条 j 款
（先定相对权重并按模式缩放，再做 β 中性调整，最后上限只缩不放）。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from trade.research.interface import EXPOSURE_NEUTRAL, Decision, Target
from trade.research.view import View
from trade.strategies.common import alt_universe, short_exclusions

# 山寨/BTC 比值指数的窗口（1 号第八部分 R12）：日线 120 根、均线 60 个值
_INDEX_DAYS = 120
_RATIO_MA_DAYS = 60
# 进入指数的最少日线根数（1 号第八部分 R12）
_MIN_INDEX_OBS = 60
# 资金费过滤的窗口（与 R11 通用要求一致）
_FUNDING_HOURS = 7 * 24
# 模式 B 的波动率窗口与 β 回看窗口（docs/13 §3 第 2、4 条，与组合层一致）
_VOL_LOOKBACK_DAYS = 30
_BETA_LOOKBACK_DAYS = 60
_MIN_BETA_OBS = 30


def daily_sigma(view: View, symbol: str) -> float | None:
    """过去 30 天日收益标准差（模式 B 的波动率窗口，docs/13 §3 第 2 条）。"""
    data = view.daily_returns(symbol, _VOL_LOOKBACK_DAYS)
    if data is None:
        return None
    _times, returns = data
    sigma = float(np.std(returns, ddof=1))
    return sigma if np.isfinite(sigma) and sigma > 0.0 else None


def beta_to_btc(view: View, symbol: str) -> float:
    """该币对 BTCUSDT 日收益的 β（样本不足 30 天取 1.0，docs/13 §3 第 4 条）。"""
    if symbol == "BTCUSDT":
        return 1.0
    btc = view.daily_returns("BTCUSDT", _BETA_LOOKBACK_DAYS)
    data = view.daily_returns(symbol, _BETA_LOOKBACK_DAYS)
    if btc is None or data is None:
        return 1.0
    times, returns = data
    btc_times, btc_returns = btc
    common, index_symbol, index_btc = np.intersect1d(times, btc_times, return_indices=True)
    if common.size < _MIN_BETA_OBS:
        return 1.0
    benchmark = btc_returns[index_btc]
    variance = float(np.var(benchmark, ddof=1))
    if not np.isfinite(variance) or variance <= 0.0:
        return 1.0
    beta = float(np.cov(returns[index_symbol], benchmark, ddof=1)[0, 1] / variance)
    return beta if np.isfinite(beta) else 1.0


class AltShortBtcLong:
    """做空成交额前 N 个山寨、用 BTCUSDT 做多腿，由组合层做 β 中性调整。"""

    name = "R12_alt_short_btc_long"
    exposure = EXPOSURE_NEUTRAL
    rebalance_hours = 168

    def __init__(
        self,
        *,
        short_n: int,
        drop_negative_funding: bool,
        alt_index_filter: bool,
        stop_max: float,
        universe_top_n: int = 30,
    ) -> None:
        self.short_n = short_n
        self.drop_negative_funding = drop_negative_funding
        self.alt_index_filter = alt_index_filter
        self.stop_max = stop_max
        self.universe_top_n = universe_top_n

    def short_leg_candidates(self, view: View) -> list[str]:
        """资金费过滤之前的空头腿候选（成交额前 N，用于构造山寨指数）。"""
        return alt_universe(view, self.universe_top_n)[: self.short_n]

    def short_leg(self, view: View) -> list[str]:
        """空头腿：成交额前 N，剔除资金费拥挤 / 上线不足与被主动剔除的负资金费币（不补位）。"""
        pool = alt_universe(view, self.universe_top_n)
        excluded = short_exclusions(view, pool)
        if self.drop_negative_funding:
            for symbol in pool:
                rate = view.funding_hourly_mean(symbol, _FUNDING_HOURS)
                if rate is not None and rate < 0.0:
                    excluded.add(symbol)
        picked: list[str] = []
        for symbol in pool:
            if symbol in excluded:
                continue
            picked.append(symbol)
            if len(picked) >= self.short_n:
                break
        return picked

    def alt_season(self, view: View) -> bool:
        """山寨/BTC 比值最新值是否高于其 60 日均线（山寨季则空仓）。

        Raises:
            ValueError: BTCUSDT 日线不足，或没有一个候选币有 60 根以上日线（红线 12）。
        """
        btc = view.daily_closes("BTCUSDT", _INDEX_DAYS)
        if btc is None:
            raise ValueError(
                f"{view.now_ms} BTCUSDT 日线不足 {_INDEX_DAYS} 根，无法构造 R12 的山寨/BTC 指数"
                "（docs/13 §5，红线 12）"
            )
        btc_times, btc_closes = btc
        values: list[np.ndarray] = []
        for symbol in self.short_leg_candidates(view):
            data = view.daily_closes(symbol, _INDEX_DAYS)
            if data is None:
                continue
            times, closes = data
            if times.size < _MIN_INDEX_OBS or closes[0] <= 0.0:
                continue
            normalised = closes / closes[0]
            index = np.searchsorted(times, btc_times, side="right") - 1
            aligned = np.full(btc_times.size, np.nan, dtype="float64")
            valid = index >= 0
            aligned[valid] = normalised[index[valid]]
            values.append(aligned)
        if not values:
            raise ValueError(
                f"{view.now_ms} R12 的候选币都没有 {_MIN_INDEX_OBS} 根以上日线，无法构造山寨指数"
                "（docs/13 §5，红线 12）"
            )
        with np.errstate(invalid="ignore"):
            index_level = np.nanmean(np.vstack(values), axis=0)
        btc_norm = btc_closes / btc_closes[0]
        ratio = index_level / btc_norm
        window = ratio[-_RATIO_MA_DAYS:]
        window = window[np.isfinite(window)]
        if window.size < _RATIO_MA_DAYS or not np.isfinite(ratio[-1]):
            raise ValueError(
                f"{view.now_ms} R12 的山寨/BTC 比值序列不足 {_RATIO_MA_DAYS} 个有效值"
                "（docs/13 §5，红线 12）"
            )
        return bool(ratio[-1] > float(np.mean(window)))

    def btc_hedge_weight(self, view: View, shorts: list[str]) -> float:
        """BTC 多头腿的相对权重，使组合事前 β 为 0（1 号第八部分 R12、docs/13 §5）。

        模式 B 的名义 ∝ `target.weight ÷ σ`（§3 第 2 条），因此使
        Σ_空头 (1 ÷ σ_i) × β_i = (w_btc ÷ σ_btc) × 1 的 `w_btc` 即下式。

        Args:
            view: 决策时刻视角。
            shorts: 空头腿的币。

        Returns:
            BTC 多头腿的相对权重（> 0）。

        Raises:
            ValueError: BTC 或空头腿缺少日线波动数据（红线 12：不得静默按等权处理）。
        """
        sigma_btc = daily_sigma(view, "BTCUSDT")
        if sigma_btc is None:
            raise ValueError(
                f"{view.now_ms} BTCUSDT 日线不足 {_VOL_LOOKBACK_DAYS} 天，无法确定 R12 的对冲腿"
                "（docs/13 §5，红线 12）"
            )
        weighted_beta = 0.0
        for symbol in shorts:
            sigma = daily_sigma(view, symbol)
            if sigma is None:
                raise ValueError(
                    f"{view.now_ms} {symbol} 日线不足 {_VOL_LOOKBACK_DAYS} 天，"
                    "无法确定 R12 的对冲腿（docs/13 §5，红线 12）"
                )
            weighted_beta += beta_to_btc(view, symbol) / sigma
        return sigma_btc * weighted_beta

    def decide(self, view: View, held: Mapping[str, int]) -> Decision:
        """山寨季或没有空头腿时空仓；否则做空山寨篮子并做多 BTC。"""
        if self.alt_index_filter and self.alt_season(view):
            return Decision((), EXPOSURE_NEUTRAL)
        shorts = self.short_leg(view)
        targets: list[Target] = [
            Target(symbol=symbol, side=-1, sl=self.stop_max, weight=1.0) for symbol in shorts
        ]
        if shorts and view.has("BTCUSDT"):
            targets.append(
                Target(
                    symbol="BTCUSDT",
                    side=1,
                    sl=self.stop_max,
                    weight=self.btc_hedge_weight(view, shorts),
                )
            )
        return Decision(tuple(targets), EXPOSURE_NEUTRAL)


def build(params: Mapping[str, float], *, stop_max: float) -> AltShortBtcLong:
    """从网格参数构造 R12。"""
    return AltShortBtcLong(
        short_n=int(params["short_n"]),
        drop_negative_funding=bool(int(params["drop_negative_funding"])),
        alt_index_filter=bool(int(params["alt_index_filter"])),
        stop_max=stop_max,
    )
