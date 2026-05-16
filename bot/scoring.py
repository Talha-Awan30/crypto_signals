"""Signal quality scoring — 1..10 confidence.

v5 criteria (additive scoring, capped at 10):
  Base                          = 4
  HTF structural alignment      +1
  MSS displacement strength     +1 (Condition C only, body > 1.5x avg)
  Volatility compression        +1 (ATR contraction in last 5 vs prior 5)
  Liquidity context & class     +1 (D class 1, 2 or 5 present)
  BTC correlation relevant      +1 (Tier 1/2 aligned, or Tier 3 strong RS)
  Volume expansion at structure +1 (last vol > 1.5x 20-bar avg)
  Retracement zone quality      FVG: +1, OB: +1, imbalance: 0, neckline: +1, boundary: 0
  Three or more HTF conditions  +1 (A+B+C+D >=3)
  LTF confluence (2+)           +1 (assigned later by state machine)
  Distance to opposing liq.     +1 (TP2 / opposing >= 2x risk)
  Session — London/NY           +1
  Regime — Transitioning        -2

Reasonable score range thus: ~3..10.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

import pandas as pd

from . import config as cfg
from .conditions.zones import Zone
from .indicators import atr
from .regime import Regime


@dataclass
class ScoreContext:
    direction: str
    htf_aligned: bool                 # structure HH/HL agrees with direction
    has_c_displacement: bool          # MSS body > 1.5x avg
    vol_compression: bool             # last 5-bar ATR < prior 5-bar ATR
    has_d_class_1_2_5: bool
    btc_correlation_ok: bool
    vol_expansion_at_structure: bool
    zone_kind: str                    # "FVG" | "OB" | ...
    htf_condition_count: int          # how many of A/B/C/D fired
    ltf_confluence_2plus: bool = False  # set by state machine on Stage 2
    rr_to_tp2_2plus: bool = False
    is_london_or_ny_session: bool = True
    regime: Regime = None  # type: ignore[assignment]


def compute_score(ctx: ScoreContext) -> int:
    s = 4
    if ctx.htf_aligned:
        s += 1
    if ctx.has_c_displacement:
        s += 1
    if ctx.vol_compression:
        s += 1
    if ctx.has_d_class_1_2_5:
        s += 1
    if ctx.btc_correlation_ok:
        s += 1
    if ctx.vol_expansion_at_structure:
        s += 1
    if ctx.zone_kind in ("FVG", "neckline"):
        s += 1
    elif ctx.zone_kind == "OB":
        s += 1
    if ctx.htf_condition_count >= 3:
        s += 1
    if ctx.ltf_confluence_2plus:
        s += 1
    if ctx.rr_to_tp2_2plus:
        s += 1
    if ctx.is_london_or_ny_session:
        s += 1
    if ctx.regime and ctx.regime.label == "Transitioning":
        s -= 2
    return max(1, min(10, s))


def is_london_or_ny() -> bool:
    """Crude session check — London 07-16 UTC, NY 12-21 UTC."""
    hour = datetime.now(timezone.utc).hour
    return 7 <= hour <= 21


def vol_compression(df: pd.DataFrame) -> bool:
    if len(df) < 15:
        return False
    a = atr(df, cfg.ATR_PERIOD)
    recent = float(a.iloc[-5:].mean())
    prior = float(a.iloc[-10:-5].mean())
    return recent < prior


def vol_expansion(df: pd.DataFrame, mult: float = 1.5) -> bool:
    if len(df) < 22:
        return False
    avg = float(df["volume"].iloc[-21:-1].mean())
    last = float(df["volume"].iloc[-1])
    return avg > 0 and last > mult * avg
