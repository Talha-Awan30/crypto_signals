"""Compute TP1 / TP2 / SL / R:R from a setup.

Rules:
  SL  = invalidation price (candle-close basis, already set by the Condition that fired)
  TP1 = next opposing structure or liquidity level
  TP2 = major HTF target / nearest untapped external liquidity
  R:R = (TP2 - entry) / (entry - SL) for longs; flipped for shorts
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from .indicators import last_n_swings, swing_pivots


@dataclass
class Targets:
    entry: float
    sl: float
    tp1: float
    tp2: float
    rr_to_tp2: float


def compute_targets(
    df: pd.DataFrame,
    direction: str,
    entry: float,
    sl: float,
    untapped_targets: List[float],
) -> Optional[Targets]:
    if (direction == "long" and entry <= sl) or (direction == "short" and entry >= sl):
        return None

    pivots = swing_pivots(df)
    risk = abs(entry - sl)

    if direction == "long":
        # TP1 = nearest swing high above entry
        highs = sorted([p.price for p in pivots if p.kind == "high" and p.price > entry])
        tp1 = highs[0] if highs else entry + risk * 2.0
        # TP2 = nearest untapped liquidity above tp1, else highest swing high
        ut = sorted([t for t in untapped_targets if t > tp1])
        if ut:
            tp2 = ut[0]
        elif highs:
            tp2 = highs[-1]
        else:
            tp2 = entry + risk * 3.0
        rr = (tp2 - entry) / risk if risk > 0 else 0.0
    else:
        lows = sorted([p.price for p in pivots if p.kind == "low" and p.price < entry], reverse=True)
        tp1 = lows[0] if lows else entry - risk * 2.0
        ut = sorted([t for t in untapped_targets if t < tp1], reverse=True)
        if ut:
            tp2 = ut[0]
        elif lows:
            tp2 = lows[-1]
        else:
            tp2 = entry - risk * 3.0
        rr = (entry - tp2) / risk if risk > 0 else 0.0

    return Targets(entry=entry, sl=sl, tp1=tp1, tp2=tp2, rr_to_tp2=round(rr, 2))
