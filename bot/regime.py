"""Daily market regime classifier — Trending / Ranging / Transitioning.

v5 rule:
  ADX >= 25 (rising)         -> Trending
  ADX <= 20 (flat)           -> Ranging
  otherwise                  -> Transitioning (deduct 2 points from score)
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import config as cfg
from .indicators import adx, atr


@dataclass
class Regime:
    label: str            # "Trending" | "Ranging" | "Transitioning"
    adx_value: float
    adx_rising: bool
    atr_value: float
    atr_avg: float
    score_modifier: int   # -2 if transitioning, else 0


def classify_regime(daily_df: pd.DataFrame) -> Regime:
    if len(daily_df) < cfg.ADX_PERIOD + 5:
        return Regime("Transitioning", 0.0, False, 0.0, 0.0, -2)

    adx_series = adx(daily_df, cfg.ADX_PERIOD)
    atr_series = atr(daily_df, cfg.ATR_PERIOD)

    adx_now = float(adx_series.iloc[-1])
    adx_prev = float(adx_series.iloc[-3])
    rising = adx_now > adx_prev

    atr_now = float(atr_series.iloc[-1])
    atr_avg = float(atr_series.iloc[-cfg.ATR_LOOKBACK_DAYS:].mean())

    if adx_now >= cfg.ADX_TRENDING and rising:
        label = "Trending"
        mod = 0
    elif adx_now <= cfg.ADX_RANGING:
        label = "Ranging"
        mod = 0
    else:
        label = "Transitioning"
        mod = -2

    return Regime(label, adx_now, rising, atr_now, atr_avg, mod)


def volatility_suppress(regime: Regime) -> bool:
    """v5: suppress alerts if Daily ATR > 2.5x 20-day avg."""
    if regime.atr_avg <= 0:
        return False
    return regime.atr_value > cfg.VOL_SUPPRESS_ATR_MULT * regime.atr_avg
