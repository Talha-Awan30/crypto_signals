"""Condition D — Liquidity Event (SECONDARY).

Spec — classify every liquidity event as ONE of:
  1. Sweep and Reclaim       — taken, reclaimed inside same/next candle (reversal hint)
  2. Sweep and Acceptance    — taken, held beyond level >= 2 candles (continuation hint)
  3. Untapped Liquidity      — unswept EQH/EQL — TP reference only, not confluence
  4. Internal Liquidity Sweep — minor inducement inside range — context only
  5. External Liquidity Sweep — major HTF resting liquidity taken beyond prior swing — high sig.

Only classes 1, 2, 5 contribute to the confluence requirement.
Classes 3, 4 are context / TP references.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

import numpy as np
import pandas as pd

from .. import config as cfg
from ..indicators import last_n_swings, swing_pivots
from .zones import Zone

EventKind = Literal[
    "sweep_reclaim",
    "sweep_accept",
    "untapped",
    "internal_sweep",
    "external_sweep",
]


@dataclass
class LiquidityEvent:
    kind: EventKind
    direction: Optional[str]    # "long" | "short" | None (untapped is direction-agnostic)
    level_price: float
    note: str
    contributes_to_confluence: bool
    zone: Optional[Zone] = None


def detect_condition_d(df: pd.DataFrame, htf_pivots_lookback: int = 30) -> List[LiquidityEvent]:
    """Return all liquidity events detectable on the latest candle."""
    out: List[LiquidityEvent] = []
    if len(df) < htf_pivots_lookback + 5:
        return out

    pivots = swing_pivots(df)

    # ----- Equal highs / equal lows clusters -----
    eq_levels = _equal_clusters(pivots)

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])

    for lvl_price, side in eq_levels:
        # External sweep — price wicked beyond level
        if side == "high" and high > lvl_price:
            kind, direction, contrib, note = _classify_sweep_above(df, lvl_price)
            zone = Zone(
                low=lvl_price * 0.999,
                high=lvl_price * 1.001,
                kind="liquidity",
                direction="bullish" if direction == "long" else "bearish",
                at=df.index[-1],
                note=f"EQH sweep at {lvl_price:.6f}",
            ) if direction else None
            out.append(LiquidityEvent(kind=kind, direction=direction, level_price=lvl_price,
                                      note=note, contributes_to_confluence=contrib, zone=zone))
        elif side == "low" and low < lvl_price:
            kind, direction, contrib, note = _classify_sweep_below(df, lvl_price)
            zone = Zone(
                low=lvl_price * 0.999,
                high=lvl_price * 1.001,
                kind="liquidity",
                direction="bullish" if direction == "long" else "bearish",
                at=df.index[-1],
                note=f"EQL sweep at {lvl_price:.6f}",
            ) if direction else None
            out.append(LiquidityEvent(kind=kind, direction=direction, level_price=lvl_price,
                                      note=note, contributes_to_confluence=contrib, zone=zone))
        else:
            # Untapped — reference target
            out.append(LiquidityEvent(
                kind="untapped",
                direction=None,
                level_price=lvl_price,
                note=f"Untapped {'EQH' if side=='high' else 'EQL'} liquidity magnet",
                contributes_to_confluence=False,
            ))

    return out


def _equal_clusters(pivots) -> List[tuple]:
    """Return [(level_price, 'high'|'low'), ...] for clustered equal highs/lows."""
    out = []
    for kind in ("high", "low"):
        prices = sorted([p.price for p in pivots if p.kind == kind])
        i = 0
        while i < len(prices):
            cluster = [prices[i]]
            j = i + 1
            while j < len(prices) and abs(prices[j] - cluster[-1]) / max(cluster[-1], 1e-9) <= cfg.D_EQ_TOLERANCE_PCT:
                cluster.append(prices[j])
                j += 1
            if len(cluster) >= 2:
                out.append((float(np.mean(cluster)), kind))
            i = j
    return out


def _classify_sweep_above(df: pd.DataFrame, level: float):
    """Sweep above a high-side level. Return (kind, direction, contributes, note)."""
    last = df.iloc[-1]
    close = float(last["close"])

    if close < level:
        return ("sweep_reclaim", "short", True, "EQH liquidity swept and reclaimed (bearish reversal hint)")

    # Held above — need 2 candles confirming
    if len(df) >= cfg.D_SWEEP_ACCEPT_CANDLES + 1:
        recent = df.iloc[-cfg.D_SWEEP_ACCEPT_CANDLES:]
        if (recent["close"] > level).all():
            return ("sweep_accept", "long", True, "EQH accepted beyond level (continuation)")

    return ("external_sweep", "short", True, "External liquidity taken above swing high")


def _classify_sweep_below(df: pd.DataFrame, level: float):
    last = df.iloc[-1]
    close = float(last["close"])

    if close > level:
        return ("sweep_reclaim", "long", True, "EQL liquidity swept and reclaimed (bullish reversal hint)")

    if len(df) >= cfg.D_SWEEP_ACCEPT_CANDLES + 1:
        recent = df.iloc[-cfg.D_SWEEP_ACCEPT_CANDLES:]
        if (recent["close"] < level).all():
            return ("sweep_accept", "short", True, "EQL accepted beyond level (continuation)")

    return ("external_sweep", "long", True, "External liquidity taken below swing low")


def directional_events(events: List[LiquidityEvent], direction: str) -> List[LiquidityEvent]:
    """Filter contributing events that agree with a given trade direction."""
    return [e for e in events if e.contributes_to_confluence and e.direction == direction]


def untapped_targets(events: List[LiquidityEvent]) -> List[float]:
    return [e.level_price for e in events if e.kind == "untapped"]
