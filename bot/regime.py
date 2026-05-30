"""Daily market regime classifier — v7.

v7 categories:
  Trending          — ADX >= 25 rising; clear direction
  Ranging           — ADX <= 20; oscillating
  Transitioning     — ADX 20-25 or conflicting; -1 confidence
  Compression       — ADX < 15 AND ATR < 50% of 20-period avg; +1 confidence;
                      flagged as HIGH ALERT — COMPRESSION

Tier 4 assets are exempt — use structural regime (their own HH/HL).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config as cfg
from .indicators import adx, atr, last_n_swings, swing_pivots


@dataclass
class Regime:
    label: str  # "Trending" | "Ranging" | "Transitioning" | "Compression"
    adx_value: float
    adx_rising: bool
    atr_value: float
    atr_avg: float
    score_modifier: int  # +1 Compression, -1 Transitioning, 0 otherwise
    high_alert: bool = False


def classify_regime(daily_df: pd.DataFrame) -> Regime:
    if len(daily_df) < cfg.ADX_PERIOD + 5:
        return Regime("Transitioning", 0.0, False, 0.0, 0.0, -1)

    adx_series = adx(daily_df, cfg.ADX_PERIOD)
    atr_series = atr(daily_df, cfg.ATR_PERIOD)

    adx_now = float(adx_series.iloc[-1])
    adx_prev = float(adx_series.iloc[-3])
    rising = adx_now > adx_prev

    atr_now = float(atr_series.iloc[-1])
    atr_avg = float(atr_series.iloc[-cfg.ATR_LOOKBACK_DAYS:].mean())

    # Compression — HIGHEST PRIORITY check first
    if (adx_now < cfg.ADX_COMPRESSION
            and atr_avg > 0
            and atr_now < 0.5 * atr_avg):
        return Regime("Compression", adx_now, rising, atr_now, atr_avg, 1, high_alert=True)

    if adx_now >= cfg.ADX_TRENDING and rising:
        return Regime("Trending", adx_now, rising, atr_now, atr_avg, 0)
    if adx_now <= cfg.ADX_RANGING:
        return Regime("Ranging", adx_now, rising, atr_now, atr_avg, 0)
    return Regime("Transitioning", adx_now, rising, atr_now, atr_avg, -1)


def classify_regime_tier4(asset_daily: pd.DataFrame) -> Regime:
    """Tier 4 — own structural regime, no ADX."""
    atr_series = atr(asset_daily, cfg.ATR_PERIOD)
    atr_now = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
    atr_avg = float(atr_series.iloc[-cfg.ATR_LOOKBACK_DAYS:].mean()) if len(atr_series) else 0.0

    pivots = swing_pivots(asset_daily)
    hs = last_n_swings(pivots, "high", 2)
    ls = last_n_swings(pivots, "low", 2)
    if len(hs) < 2 or len(ls) < 2:
        return Regime("Ranging", 0.0, False, atr_now, atr_avg, 0)
    hh = hs[-1].price > hs[-2].price
    hl = ls[-1].price > ls[-2].price
    lh = hs[-1].price < hs[-2].price
    ll = ls[-1].price < ls[-2].price
    if (hh and hl) or (lh and ll):
        return Regime("Trending", 0.0, True, atr_now, atr_avg, 0)
    return Regime("Ranging", 0.0, False, atr_now, atr_avg, 0)


def volatility_suppress(regime: Regime) -> bool:
    """v7: suppress all alerts if Daily ATR > 2.5x 20-period avg."""
    if regime.atr_avg <= 0:
        return False
    return regime.atr_value > cfg.VOL_SUPPRESS_ATR_MULT * regime.atr_avg


def is_news_candle(df: pd.DataFrame) -> bool:
    """v7: single candle exceeding 3x ATR = news candle, suppress."""
    if len(df) < cfg.ATR_PERIOD + 2:
        return False
    a = atr(df, cfg.ATR_PERIOD)
    if len(a) == 0:
        return False
    atr_now = float(a.iloc[-1])
    if atr_now <= 0:
        return False
    last = df.iloc[-1]
    candle_range = float(last["high"]) - float(last["low"])
    return candle_range > cfg.NEWS_CANDLE_ATR_MULT * atr_now
