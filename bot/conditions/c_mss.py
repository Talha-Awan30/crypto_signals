"""Condition C — HTF Market Structure Shift (PRIMARY).

Spec (all required):
  - Confirmed swing-high/low break on 4H or Daily
  - Displacement candle body > prior 5-candle average body
  - Candle CLOSE beyond structure (wick alone disqualifies)
  - Minimum 1 ATR expansion through the broken level
  - No immediate rejection back into prior range on the next candle

On confirmation: identify retracement zone — FVG / OB / imbalance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .. import config as cfg
from ..indicators import atr, avg_body, body, last_n_swings, swing_pivots
from .zones import Zone


@dataclass
class ConditionC:
    direction: str           # "long" | "short"
    broken_level: float
    displacement_body: float
    atr_value: float
    zone: Zone               # FVG > OB > imbalance preference


def detect_condition_c(df: pd.DataFrame) -> Optional[ConditionC]:
    if len(df) < cfg.ATR_PERIOD + cfg.C_DISPLACEMENT_LOOKBACK + 10:
        return None

    pivots = swing_pivots(df)
    last_highs = last_n_swings(pivots, "high", 2)
    last_lows = last_n_swings(pivots, "low", 2)
    if not last_highs or not last_lows:
        return None

    atr_now = float(atr(df, cfg.ATR_PERIOD).iloc[-2])  # ATR BEFORE the breaking candle
    avg = avg_body(df, cfg.C_DISPLACEMENT_LOOKBACK)
    if atr_now <= 0 or avg <= 0:
        return None

    # Need at least 1 candle after the potential break candle to check "no immediate rejection"
    if len(df) < 2:
        return None

    break_candle = df.iloc[-2]   # the candle that broke
    confirm_candle = df.iloc[-1]  # next candle, must NOT immediately reject

    break_close = float(break_candle["close"])
    break_open = float(break_candle["open"])
    break_high = float(break_candle["high"])
    break_low = float(break_candle["low"])

    body_size = body(break_candle)
    if body_size <= avg:
        return None

    # Check bullish break (above last swing high)
    swing_high = last_highs[-1].price
    if (
        break_close > swing_high
        and break_close > break_open
        and (break_close - swing_high) > cfg.C_ATR_DISPLACEMENT_MULT * atr_now
        and float(confirm_candle["close"]) >= swing_high  # no immediate rejection
    ):
        zone = _build_zone(df, "bullish", swing_high)
        return ConditionC(
            direction="long",
            broken_level=swing_high,
            displacement_body=body_size,
            atr_value=atr_now,
            zone=zone,
        )

    # Check bearish break (below last swing low)
    swing_low = last_lows[-1].price
    if (
        break_close < swing_low
        and break_close < break_open
        and (swing_low - break_close) > cfg.C_ATR_DISPLACEMENT_MULT * atr_now
        and float(confirm_candle["close"]) <= swing_low
    ):
        zone = _build_zone(df, "bearish", swing_low)
        return ConditionC(
            direction="short",
            broken_level=swing_low,
            displacement_body=body_size,
            atr_value=atr_now,
            zone=zone,
        )

    return None


def _build_zone(df: pd.DataFrame, direction: str, broken_level: float) -> Zone:
    """Find FVG > OB > imbalance origin of the displacement candle."""
    # Displacement candle was iloc[-2]; check for FVG using neighbours
    if len(df) >= 4:
        n = len(df)
        i = n - 2  # displacement index
        highs = df["high"].values
        lows = df["low"].values
        if direction == "bullish":
            # Bullish FVG: low[i] > high[i-2]
            if lows[i] > highs[i - 2]:
                return Zone(
                    low=float(highs[i - 2]),
                    high=float(lows[i]),
                    kind="FVG",
                    direction="bullish",
                    at=df.index[i],
                    note="Bullish FVG from displacement",
                )
        else:
            if highs[i] < lows[i - 2]:
                return Zone(
                    low=float(highs[i]),
                    high=float(lows[i - 2]),
                    kind="FVG",
                    direction="bearish",
                    at=df.index[i],
                    note="Bearish FVG from displacement",
                )

    # Fall back to Order Block — last opposing candle before displacement
    if len(df) >= 3:
        for back in range(2, min(8, len(df))):
            cand = df.iloc[-2 - back + 1]
            cand_close = float(cand["close"])
            cand_open = float(cand["open"])
            if direction == "bullish" and cand_close < cand_open:
                return Zone(
                    low=float(cand["low"]),
                    high=float(cand["high"]),
                    kind="OB",
                    direction="bullish",
                    at=cand.name,
                    note="Bullish OB (last bearish candle before displacement)",
                )
            if direction == "bearish" and cand_close > cand_open:
                return Zone(
                    low=float(cand["low"]),
                    high=float(cand["high"]),
                    kind="OB",
                    direction="bearish",
                    at=cand.name,
                    note="Bearish OB (last bullish candle before displacement)",
                )

    # Last resort: imbalance zone around the broken level
    return Zone(
        low=broken_level * 0.998,
        high=broken_level * 1.002,
        kind="imbalance",
        direction="bullish" if direction == "bullish" else "bearish",
        at=df.index[-2],
        note="Imbalance zone at broken level",
    )
