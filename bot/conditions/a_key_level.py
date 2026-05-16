"""Condition A — HTF Key Level Reaction (PRIMARY).

Spec:
  - Price CLOSE within 0.5% of a major Daily/4H S/R level (close-based only)
  - Level must have >= 2 prior reactions on Daily or 4H
  - Rejection or acceptance visible on the reaction candle body (not wick)

Method:
  1. Cluster historical swing pivots (highs and lows) to identify candidate
     S/R levels — a level is the mean price of >= 2 clustered pivots.
  2. Check the latest close: if within 0.5% of a clustered level, AND the
     candle body confirms (rejection: body closes away; acceptance: body
     closes through), emit Condition A.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from .. import config as cfg
from ..indicators import Pivot, swing_pivots
from .zones import Zone


@dataclass
class KeyLevel:
    price: float
    touches: int
    side: str  # "resistance" | "support"


@dataclass
class ConditionA:
    direction: str          # "long" | "short"
    level: KeyLevel
    behaviour: str          # "rejection" | "acceptance"
    zone: Zone              # retracement zone built from the level


def find_key_levels(df: pd.DataFrame, tolerance_pct: float = cfg.A_LEVEL_TOLERANCE_PCT) -> List[KeyLevel]:
    """Cluster swing pivots into S/R levels via simple 1D agglomeration."""
    pivots = swing_pivots(df)
    if len(pivots) < cfg.A_LEVEL_MIN_REACTIONS:
        return []

    highs = sorted([p.price for p in pivots if p.kind == "high"])
    lows = sorted([p.price for p in pivots if p.kind == "low"])

    return _cluster(highs, "resistance", tolerance_pct) + _cluster(lows, "support", tolerance_pct)


def _cluster(prices: List[float], side: str, tol: float) -> List[KeyLevel]:
    out: List[KeyLevel] = []
    if not prices:
        return out
    current: List[float] = [prices[0]]
    for p in prices[1:]:
        if abs(p - current[-1]) / max(current[-1], 1e-9) <= tol:
            current.append(p)
        else:
            if len(current) >= cfg.A_LEVEL_MIN_REACTIONS:
                out.append(KeyLevel(price=float(np.mean(current)), touches=len(current), side=side))
            current = [p]
    if len(current) >= cfg.A_LEVEL_MIN_REACTIONS:
        out.append(KeyLevel(price=float(np.mean(current)), touches=len(current), side=side))
    return out


def detect_condition_a(df: pd.DataFrame) -> Optional[ConditionA]:
    levels = find_key_levels(df)
    if not levels:
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    open_ = float(last["open"])
    high = float(last["high"])
    low = float(last["low"])

    for lvl in levels:
        dist = abs(close - lvl.price) / lvl.price
        if dist > cfg.A_LEVEL_TOLERANCE_PCT:
            continue

        # Determine behaviour from candle body
        # rejection at resistance: body closes BELOW level after touching
        # acceptance through resistance: body closes ABOVE level
        if lvl.side == "resistance":
            if high >= lvl.price and close < lvl.price and close < open_:
                direction = "short"
                behaviour = "rejection"
            elif close > lvl.price and close > open_:
                direction = "long"
                behaviour = "acceptance"
            else:
                continue
        else:  # support
            if low <= lvl.price and close > lvl.price and close > open_:
                direction = "long"
                behaviour = "rejection"
            elif close < lvl.price and close < open_:
                direction = "short"
                behaviour = "acceptance"
            else:
                continue

        # Build retracement zone — the candle body itself (institutional reaction zone)
        zone = Zone(
            low=min(open_, close),
            high=max(open_, close),
            kind="key_level",
            direction="bullish" if direction == "long" else "bearish",
            at=df.index[-1],
            note=f"{behaviour} at {lvl.side} {lvl.price:.6f} (touches={lvl.touches})",
        )
        return ConditionA(direction=direction, level=lvl, behaviour=behaviour, zone=zone)

    return None
