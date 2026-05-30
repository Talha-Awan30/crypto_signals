"""LTF (30M / 15M) validation — v7 Step 4.

Three strict mathematical conditions. At least ONE must confirm inside the
retracement zone for Stage 2 to fire. If two or more confirm simultaneously,
+1 confidence bonus.

  1. Structural MSS:
     Close beyond LTF swing pivot by >= 0.15 x LTF ATR (14)

  2. Institutional Displacement Validation:
     - Body > 1.5x avg of preceding 5 candles' bodies
     - Closes away from zone boundary
     - Volume > 20-period MA by >= 20%

  3. ATR-Normalized Rejection Strength:
     - LONG:  lower wick > 1.5x body AND close in top 25% of range
     - SHORT: upper wick > 1.5x body AND close in bottom 25% of range
     - Total range >= 0.75 x LTF ATR (14)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from . import config as cfg
from .conditions.zones import Zone
from .indicators import atr, last_n_swings, swing_pivots


@dataclass
class LTFValidation:
    triggered: bool
    triggers: List[str]
    trigger_summary: str
    confluence_bonus: bool


def _mss(df: pd.DataFrame, direction: str, atr_now: float) -> Optional[str]:
    pivots = swing_pivots(df, left=2, right=2)
    last_h = last_n_swings(pivots, "high", 1)
    last_l = last_n_swings(pivots, "low", 1)
    close = float(df["close"].iloc[-1])
    threshold = cfg.LTF_MSS_BREAK_ATR_MULT * atr_now

    if direction == "long" and last_h:
        pivot = last_h[-1].price
        if close - pivot >= threshold:
            return f"MSS: close {close:.6f} above last LTF high {pivot:.6f} by >= 0.15xATR"
    if direction == "short" and last_l:
        pivot = last_l[-1].price
        if pivot - close >= threshold:
            return f"MSS: close {close:.6f} below last LTF low {pivot:.6f} by >= 0.15xATR"
    return None


def _displacement(df: pd.DataFrame, direction: str, zone: Zone) -> Optional[str]:
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    prior5 = df.iloc[-6:-1]
    avg_body = float((prior5["close"] - prior5["open"]).abs().mean())
    if avg_body <= 0:
        return None
    body = abs(float(last["close"]) - float(last["open"]))
    if body <= cfg.LTF_DISP_BODY_MULT * avg_body:
        return None

    vol_ma20 = float(df["volume"].iloc[-21:-1].mean())
    last_vol = float(last["volume"])
    if vol_ma20 <= 0 or last_vol < cfg.LTF_DISP_VOL_MULT * vol_ma20:
        return None

    close = float(last["close"])
    if direction == "long" and close > zone.high and close > float(last["open"]):
        return f"Displacement: body {body:.6f} > 1.5x prior-5 avg; vol +{last_vol/vol_ma20:.1f}x MA20; closed above zone"
    if direction == "short" and close < zone.low and close < float(last["open"]):
        return f"Displacement: body {body:.6f} > 1.5x prior-5 avg; vol +{last_vol/vol_ma20:.1f}x MA20; closed below zone"
    return None


def _rejection(df: pd.DataFrame, direction: str, zone: Zone, atr_now: float) -> Optional[str]:
    last = df.iloc[-1]
    o, h, l, c = (float(last["open"]), float(last["high"]),
                  float(last["low"]), float(last["close"]))
    rng = h - l
    body = abs(c - o)
    if rng <= 0 or body <= 0:
        return None
    if rng < cfg.LTF_REJECTION_MIN_RANGE_ATR * atr_now:
        return None

    if direction == "long":
        lower_wick = min(o, c) - l
        if lower_wick < cfg.LTF_REJECTION_WICK_BODY_RATIO * body:
            return None
        # close in top 25% of range
        if (c - l) / rng < (1 - cfg.LTF_REJECTION_CLOSE_PCTILE):
            return None
        return f"Rejection: lower wick {lower_wick:.6f} > 1.5x body; close in top 25% of range"
    else:
        upper_wick = h - max(o, c)
        if upper_wick < cfg.LTF_REJECTION_WICK_BODY_RATIO * body:
            return None
        if (h - c) / rng < (1 - cfg.LTF_REJECTION_CLOSE_PCTILE):
            return None
        return f"Rejection: upper wick {upper_wick:.6f} > 1.5x body; close in bottom 25% of range"


def validate_ltf(df: pd.DataFrame, direction: str, zone: Zone) -> LTFValidation:
    if df is None or len(df) < 25:
        return LTFValidation(False, [], "LTF data insufficient", False)
    atr_series = atr(df, cfg.ATR_PERIOD)
    if len(atr_series) == 0:
        return LTFValidation(False, [], "LTF ATR unavailable", False)
    atr_now = float(atr_series.iloc[-1])
    if atr_now <= 0:
        return LTFValidation(False, [], "LTF ATR <= 0", False)

    triggers: List[str] = []
    for fn in (lambda: _mss(df, direction, atr_now),
               lambda: _displacement(df, direction, zone),
               lambda: _rejection(df, direction, zone, atr_now)):
        try:
            res = fn()
            if res:
                triggers.append(res)
        except Exception:
            continue

    if not triggers:
        return LTFValidation(False, [], "No LTF trigger yet", False)

    return LTFValidation(
        triggered=True,
        triggers=triggers,
        trigger_summary="; ".join(triggers),
        confluence_bonus=len(triggers) >= 2,
    )
