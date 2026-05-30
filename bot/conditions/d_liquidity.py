"""Condition D (v7) — Institutional Liquidity Event (INDEPENDENT trigger).

Four valid classifications:
  1. Sweep and Reclaim          — HIGH PROBABILITY REVERSAL (fires Stage 1)
  2. External Liquidity Sweep   — HIGH PROBABILITY (fires Stage 1 after 1-2 candle confirm)
  3. Sweep and Acceptance       — CONTINUATION (fires Stage 1 after 2 acceptance candles)
  4. Pending Order Zone Alert   — PRE-SWEEP WARNING (informational, NOT Stage 1)

Internal liquidity (minor highs/lows inside consolidation) does NOT qualify.

Valid liquidity LEVELS:
  - Equal highs (EQH) / lows (EQL) within 0.3% on 4H or Daily
  - Prior day high / low
  - Weekly open / monthly open (approximated)
  - HTF swing high/low with >= 5 candle separation
  - Visible stop cluster zones
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

import numpy as np
import pandas as pd

from .. import config as cfg
from ..indicators import atr, last_n_swings, swing_pivots
from .zones import Zone


EventKind = Literal["sweep_reclaim", "sweep_accept", "external_sweep", "pending_zone"]


@dataclass
class LiquidityEvent:
    kind: EventKind
    direction: Optional[str]   # None for pending_zone (informational)
    level_price: float
    note: str
    is_stage1_trigger: bool    # True for kinds 1-3, False for pending_zone
    zone: Optional[Zone] = None
    historical_reactions: int = 0
    distance_pct: float = 0.0  # for pending_zone


# ---------------------------------------------------------------------------
# Level identification
# ---------------------------------------------------------------------------

def _equal_clusters(pivots) -> List[tuple]:
    """Return [(level_price, 'high'|'low', touches), ...] for EQH/EQL clusters."""
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
                out.append((float(np.mean(cluster)), kind, len(cluster)))
            i = j
    return out


def _major_swing_levels(pivots) -> List[tuple]:
    """v7: HTF swing high/low with >= 5 candle separation."""
    out = []
    for kind in ("high", "low"):
        kpivots = [p for p in pivots if p.kind == kind]
        for i in range(len(kpivots)):
            # Require this pivot to be separated from neighbors by >= 5 candles
            if i > 0 and (kpivots[i].idx - kpivots[i - 1].idx) < cfg.D_MIN_SWING_SEPARATION:
                continue
            out.append((kpivots[i].price, kind, 1))
    return out


def _prior_day_levels(df: pd.DataFrame) -> List[tuple]:
    """Prior day high/low — institutional reference levels."""
    if len(df) < 2:
        return []
    # On a daily df, "prior day" is df.iloc[-2]. On 4H, look at last 6 bars.
    out = []
    prev = df.iloc[-2]
    out.append((float(prev["high"]), "high", 1))
    out.append((float(prev["low"]), "low", 1))
    return out


# ---------------------------------------------------------------------------
# Sweep classification
# ---------------------------------------------------------------------------

def _classify_sweep_above(df: pd.DataFrame, level: float, atr_now: float) -> Optional[LiquidityEvent]:
    last = df.iloc[-1]
    close = float(last["close"])
    high = float(last["high"])

    # Sweep occurred? wick above level
    if high <= level:
        return None

    # Reclaim — same/next candle closes back inside
    if close < level:
        # FVG / imbalance left by the sweep candle (rough proxy: candle body)
        zone_low = float(min(last["open"], last["close"]))
        zone_high = float(max(last["open"], last["close"]))
        # Check excessive imbalance
        if (zone_high - zone_low) > cfg.D_FVG_EXCESSIVE_ATR_MULT * atr_now:
            return None
        return LiquidityEvent(
            kind="sweep_reclaim", direction="short", level_price=level,
            note=f"EQH sweep & reclaim @ {level:.6f}",
            is_stage1_trigger=True,
            zone=Zone(low=zone_low, high=zone_high, kind="FVG",
                      direction="bearish", at=df.index[-1],
                      note=f"Sweep&Reclaim FVG"),
            historical_reactions=2,
        )

    # Acceptance — last 2 closes both above level
    if len(df) >= cfg.D_SWEEP_ACCEPT_CANDLES + 1:
        recent = df.iloc[-cfg.D_SWEEP_ACCEPT_CANDLES:]
        if (recent["close"] > level).all():
            zone_low = float(level)
            zone_high = float(level * 1.001)
            return LiquidityEvent(
                kind="sweep_accept", direction="long", level_price=level,
                note=f"EQH sweep & acceptance @ {level:.6f} (2+ closes above)",
                is_stage1_trigger=True,
                zone=Zone(low=zone_low, high=zone_high, kind="boundary",
                          direction="bullish", at=df.index[-1],
                          note="Sweep&Accept retest level"),
                historical_reactions=2,
            )

    # External sweep — volume expansion + sweep beyond major swing
    if len(df) >= 6:
        avg_vol = float(df["volume"].iloc[-6:-1].mean())
        last_vol = float(last["volume"])
        if avg_vol > 0 and last_vol > cfg.D_EXT_SWEEP_VOL_MULT * avg_vol:
            return LiquidityEvent(
                kind="external_sweep", direction="short", level_price=level,
                note=f"External sweep above {level:.6f} with vol +{last_vol/avg_vol:.1f}x",
                is_stage1_trigger=True,
                zone=Zone(low=level * 0.999, high=level * 1.001, kind="boundary",
                          direction="bearish", at=df.index[-1],
                          note="External sweep retest"),
                historical_reactions=1,
            )
    return None


def _classify_sweep_below(df: pd.DataFrame, level: float, atr_now: float) -> Optional[LiquidityEvent]:
    last = df.iloc[-1]
    close = float(last["close"])
    low = float(last["low"])
    if low >= level:
        return None

    if close > level:
        zone_low = float(min(last["open"], last["close"]))
        zone_high = float(max(last["open"], last["close"]))
        if (zone_high - zone_low) > cfg.D_FVG_EXCESSIVE_ATR_MULT * atr_now:
            return None
        return LiquidityEvent(
            kind="sweep_reclaim", direction="long", level_price=level,
            note=f"EQL sweep & reclaim @ {level:.6f}",
            is_stage1_trigger=True,
            zone=Zone(low=zone_low, high=zone_high, kind="FVG",
                      direction="bullish", at=df.index[-1],
                      note="Sweep&Reclaim FVG"),
            historical_reactions=2,
        )

    if len(df) >= cfg.D_SWEEP_ACCEPT_CANDLES + 1:
        recent = df.iloc[-cfg.D_SWEEP_ACCEPT_CANDLES:]
        if (recent["close"] < level).all():
            zone_low = float(level * 0.999)
            zone_high = float(level)
            return LiquidityEvent(
                kind="sweep_accept", direction="short", level_price=level,
                note=f"EQL sweep & acceptance @ {level:.6f} (2+ closes below)",
                is_stage1_trigger=True,
                zone=Zone(low=zone_low, high=zone_high, kind="boundary",
                          direction="bearish", at=df.index[-1],
                          note="Sweep&Accept retest level"),
                historical_reactions=2,
            )

    if len(df) >= 6:
        avg_vol = float(df["volume"].iloc[-6:-1].mean())
        last_vol = float(last["volume"])
        if avg_vol > 0 and last_vol > cfg.D_EXT_SWEEP_VOL_MULT * avg_vol:
            return LiquidityEvent(
                kind="external_sweep", direction="long", level_price=level,
                note=f"External sweep below {level:.6f} with vol +{last_vol/avg_vol:.1f}x",
                is_stage1_trigger=True,
                zone=Zone(low=level * 0.999, high=level * 1.001, kind="boundary",
                          direction="bullish", at=df.index[-1],
                          note="External sweep retest"),
                historical_reactions=1,
            )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_condition_d(df: pd.DataFrame) -> List[LiquidityEvent]:
    """Return all detectable liquidity events on the latest candle.

    Triggers (kinds 1-3) carry is_stage1_trigger=True.
    Pending Order Zone alerts (kind 4) are informational only.
    """
    out: List[LiquidityEvent] = []
    if len(df) < 25:
        return out

    pivots = swing_pivots(df)
    atr_series = atr(df, cfg.ATR_PERIOD)
    if len(atr_series) == 0:
        return out
    atr_now = float(atr_series.iloc[-1])
    if atr_now <= 0:
        return out

    last = df.iloc[-1]
    current_price = float(last["close"])

    # Collect external liquidity levels
    levels: List[tuple] = []
    levels.extend(_equal_clusters(pivots))
    levels.extend(_major_swing_levels(pivots))
    levels.extend(_prior_day_levels(df))

    # Dedupe levels within 0.1%
    seen_prices: List[float] = []
    unique_levels = []
    for price, side, touches in levels:
        if any(abs(price - sp) / max(sp, 1e-9) < 0.001 for sp in seen_prices):
            continue
        seen_prices.append(price)
        unique_levels.append((price, side, touches))

    for price, side, touches in unique_levels:
        # Pending Order Zone (kind 4) — informational only
        dist_pct = abs(price - current_price) / max(current_price, 1e-9)
        if dist_pct <= cfg.D_PENDING_ZONE_APPROACH_PCT:
            # Has it already been swept? simple check: any recent candle exceeded it
            recent_window = df.iloc[-10:]
            if side == "high" and float(recent_window["high"].max()) >= price:
                pass  # already swept; skip
            elif side == "low" and float(recent_window["low"].min()) <= price:
                pass
            else:
                out.append(LiquidityEvent(
                    kind="pending_zone", direction=None, level_price=price,
                    note=f"Approaching untapped {'EQH' if side == 'high' else 'EQL'} @ {price:.6f}",
                    is_stage1_trigger=False,
                    historical_reactions=touches,
                    distance_pct=dist_pct,
                ))

        # Actual sweep events (kinds 1-3)
        if side == "high":
            ev = _classify_sweep_above(df, price, atr_now)
        else:
            ev = _classify_sweep_below(df, price, atr_now)
        if ev:
            ev.historical_reactions = touches
            out.append(ev)

    return out


def primary_triggers(events: List[LiquidityEvent]) -> List[LiquidityEvent]:
    """Filter to events that actually trigger Stage 1."""
    return [e for e in events if e.is_stage1_trigger]


def pending_zones(events: List[LiquidityEvent]) -> List[LiquidityEvent]:
    return [e for e in events if e.kind == "pending_zone"]
