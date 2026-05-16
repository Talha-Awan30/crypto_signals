"""LTF (1H / 15M) validation — Stage 2 trigger logic.

v5 spec — once price is INSIDE the defined zone, require AT LEAST ONE of:
  1. Micro market structure shift in the intended trade direction
  2. Displacement candle with body > prior 3-candle average, closing away from zone
  3. Clear rejection candle: wick into zone, body closes outside (away from zone)

If 2+ triggers fire simultaneously: +1 confidence bonus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .conditions.zones import Zone
from .indicators import body, last_n_swings, swing_pivots


@dataclass
class LTFValidation:
    triggered: bool
    triggers: List[str]
    trigger_summary: str
    confluence_bonus: bool          # 2+ triggers => +1


def _micro_mss(df: pd.DataFrame, direction: str) -> Optional[str]:
    pivots = swing_pivots(df, left=2, right=2)
    last_h = last_n_swings(pivots, "high", 2)
    last_l = last_n_swings(pivots, "low", 2)
    if not last_h or not last_l:
        return None
    close = float(df["close"].iloc[-1])
    if direction == "long" and last_h and close > last_h[-1].price:
        return f"Micro-MSS: close {close:.6f} broke last LTF swing high {last_h[-1].price:.6f}"
    if direction == "short" and last_l and close < last_l[-1].price:
        return f"Micro-MSS: close {close:.6f} broke last LTF swing low {last_l[-1].price:.6f}"
    return None


def _displacement(df: pd.DataFrame, direction: str, zone: Zone) -> Optional[str]:
    if len(df) < 5:
        return None
    last = df.iloc[-1]
    prior = df.iloc[-4:-1]
    avg_body = float((prior["close"] - prior["open"]).abs().mean())
    if avg_body <= 0:
        return None
    if body(last) <= avg_body:
        return None
    close = float(last["close"])
    if direction == "long" and close > zone.high and float(last["close"]) > float(last["open"]):
        return f"Displacement candle body {body(last):.6f} > 3-bar avg, closed above zone"
    if direction == "short" and close < zone.low and float(last["close"]) < float(last["open"]):
        return f"Displacement candle body {body(last):.6f} > 3-bar avg, closed below zone"
    return None


def _rejection(df: pd.DataFrame, direction: str, zone: Zone) -> Optional[str]:
    last = df.iloc[-1]
    open_ = float(last["open"])
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    body_low = min(open_, close)
    body_high = max(open_, close)
    if direction == "long":
        # wick into zone (low <= zone.high), body above zone
        if low <= zone.high and body_low > zone.high:
            return f"Rejection wick into zone, body closed above {zone.high:.6f}"
    else:
        if high >= zone.low and body_high < zone.low:
            return f"Rejection wick into zone, body closed below {zone.low:.6f}"
    return None


def validate_ltf(df: pd.DataFrame, direction: str, zone: Zone) -> LTFValidation:
    """Apply all three v5 LTF checks to the latest LTF candle."""
    if df is None or len(df) < 5:
        return LTFValidation(False, [], "LTF data insufficient", False)

    triggers: List[str] = []
    for fn in (_micro_mss, _displacement, _rejection):
        try:
            res = fn(df, direction, zone) if fn is not _micro_mss else _micro_mss(df, direction)
        except TypeError:
            res = fn(df, direction)
        if res:
            triggers.append(res)

    if not triggers:
        return LTFValidation(False, [], "No LTF trigger yet", False)

    return LTFValidation(
        triggered=True,
        triggers=triggers,
        trigger_summary="; ".join(triggers),
        confluence_bonus=len(triggers) >= 2,
    )
