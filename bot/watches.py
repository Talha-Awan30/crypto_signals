"""Standalone WATCH scanners — v7.

These are informational alerts, NOT Stage 1 / Stage 2.
Both can be disabled via env (ENABLE_CONSOLIDATION_WATCH, ENABLE_LIQUIDITY_APPROACH).

Consolidation Watch — fires per Daily candle close when ALL of:
  - Price within 3% of multi-week/month HTF base level
  - Volume < 50% of 20-period avg in last 10 candles
  - ATR < 50% of 20-period avg
  - ADX < 20

Liquidity Approach — fires whenever price is within 1% of an untapped EQH/EQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from . import config as cfg
from .indicators import adx, atr, swing_pivots


@dataclass
class ConsolidationWatch:
    base: str
    current_price: float
    htf_base_level: float
    vol_pct_below_avg: float
    atr_pct_below_avg: float
    adx_value: float
    note: str


@dataclass
class LiquidityApproachWatch:
    base: str
    current_price: float
    approaching_level: float
    side: str            # "EQH" | "EQL"
    distance_pct: float
    historical_reactions: int


def consolidation_watch(df: pd.DataFrame, base: str) -> Optional[ConsolidationWatch]:
    if len(df) < cfg.ATR_LOOKBACK_DAYS + cfg.CONS_LOOKBACK_CANDLES + 5:
        return None

    # 1) HTF base level proximity — use lowest swing low of last ~60 bars
    pivots = swing_pivots(df.iloc[-60:])
    lows = [p.price for p in pivots if p.kind == "low"]
    if not lows:
        return None
    htf_base = min(lows)
    current = float(df["close"].iloc[-1])
    if abs(current - htf_base) / max(htf_base, 1e-9) > cfg.CONS_BASE_PROXIMITY_PCT:
        return None

    # 2) Volume contraction
    recent_vol = float(df["volume"].iloc[-cfg.CONS_LOOKBACK_CANDLES:].mean())
    vol_ma20 = float(df["volume"].iloc[-(cfg.ATR_LOOKBACK_DAYS):].mean())
    if vol_ma20 <= 0 or recent_vol / vol_ma20 >= cfg.CONS_VOL_THRESHOLD_PCT:
        return None
    vol_pct_below = (1 - recent_vol / vol_ma20) * 100

    # 3) ATR contraction
    atr_series = atr(df, cfg.ATR_PERIOD)
    if len(atr_series) < cfg.ATR_LOOKBACK_DAYS + 1:
        return None
    recent_atr = float(atr_series.iloc[-cfg.CONS_LOOKBACK_CANDLES:].mean())
    atr_ma20 = float(atr_series.iloc[-(cfg.ATR_LOOKBACK_DAYS):].mean())
    if atr_ma20 <= 0 or recent_atr / atr_ma20 >= cfg.CONS_ATR_THRESHOLD_PCT:
        return None
    atr_pct_below = (1 - recent_atr / atr_ma20) * 100

    # 4) ADX < 20
    adx_series = adx(df, cfg.ADX_PERIOD)
    if len(adx_series) == 0:
        return None
    adx_val = float(adx_series.iloc[-1])
    if adx_val >= cfg.CONS_ADX_MAX:
        return None

    return ConsolidationWatch(
        base=base,
        current_price=current,
        htf_base_level=htf_base,
        vol_pct_below_avg=vol_pct_below,
        atr_pct_below_avg=atr_pct_below,
        adx_value=adx_val,
        note="price compressing at HTF base — potential large directional move developing",
    )


def liquidity_approach_watch(df: pd.DataFrame, base: str) -> List[LiquidityApproachWatch]:
    """Find untapped EQH/EQL levels within 1% of current price."""
    out: List[LiquidityApproachWatch] = []
    if len(df) < 30:
        return out

    pivots = swing_pivots(df)
    current = float(df["close"].iloc[-1])
    recent_window = df.iloc[-20:]
    recent_high = float(recent_window["high"].max())
    recent_low = float(recent_window["low"].min())

    # Reuse the EQH/EQL clustering from d_liquidity
    from .conditions.d_liquidity import _equal_clusters
    clusters = _equal_clusters(pivots)
    seen: List[float] = []
    for price, side, touches in clusters:
        if any(abs(price - sp) / max(sp, 1e-9) < 0.001 for sp in seen):
            continue
        seen.append(price)
        # Untapped check
        if side == "high" and recent_high >= price:
            continue
        if side == "low" and recent_low <= price:
            continue
        dist_pct = abs(price - current) / max(current, 1e-9)
        if dist_pct <= cfg.D_PENDING_ZONE_APPROACH_PCT:
            out.append(LiquidityApproachWatch(
                base=base,
                current_price=current,
                approaching_level=price,
                side="EQH" if side == "high" else "EQL",
                distance_pct=dist_pct,
                historical_reactions=touches,
            ))
    return out
