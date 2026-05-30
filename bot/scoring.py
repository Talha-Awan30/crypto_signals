"""Signal quality scoring — v7.

Base score: 5.
Modifiers:
  Setup TF weight:       1D +2, 4H/2H 0, 1H -1
  Market regime:         Trending/Ranging 0, Transitioning -1, Compression +1
  Pattern quality:       H&S/IH&S +1, Broadening/Unclassified/Wedge -1
  Liquidity event:       Sweep&Reclaim or External Sweep +1, Sweep&Accept 0
  Volume confirmation:   breakout vol >= 1.2x prior-5 +1, < prior-5 -1
  LTF confluence 2+:     +1 (added at Stage 2 only)
  RS vs BTC (Tier 3):    +1 if >= +10%, -1 if <= -10%
  Trap warning:          cap at 5
  B+D confluence:        +1 (and label B+D CONFLUENCE DETECTED)
  Tier 2 BTC opposing:   -1 (does NOT suppress)
  Daily ATR > 2.5x avg:  suppress (handled in main, not here)

Final score >= 8 delivers. Below = logged only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from . import config as cfg
from .indicators import atr
from .regime import Regime


@dataclass
class ScoreContext:
    timeframe: str                          # "1d" | "4h" | "2h" | "1h"
    regime: Regime
    pattern_name: Optional[str] = None
    liquidity_kind: Optional[str] = None    # "sweep_reclaim" | "external_sweep" | "sweep_accept"
    breakout_vol_ratio_prior5: float = 1.0  # 1.2+ = +1, < 1.0 = -1
    ltf_confluence_2plus: bool = False
    rs_score_delta: int = 0
    trap_warning: bool = False
    bd_confluence: bool = False
    tier2_btc_opposing: bool = False


def compute_score(ctx: ScoreContext) -> int:
    s = cfg.SCORE_BASE

    # TF weight
    s += cfg.TF_WEIGHT.get(ctx.timeframe, 0)

    # Regime
    if ctx.regime:
        label = ctx.regime.label
        if label == "Transitioning":
            s -= 1
        elif label == "Compression":
            s += 1
        # Trending and Ranging contribute 0

    # Pattern quality
    if ctx.pattern_name:
        nm = ctx.pattern_name.lower()
        if "head and shoulders" in nm:
            s += 1
        elif "broadening" in nm or "unclassified" in nm or "wedge" in nm:
            s -= 1

    # Liquidity quality
    if ctx.liquidity_kind in ("sweep_reclaim", "external_sweep"):
        s += 1

    # Volume confirmation at breakout
    if ctx.breakout_vol_ratio_prior5 >= 1.2:
        s += 1
    elif ctx.breakout_vol_ratio_prior5 < 1.0:
        s -= 1

    # LTF confluence (set at Stage 2)
    if ctx.ltf_confluence_2plus:
        s += 1

    # RS (Tier 3 only — caller passes 0 otherwise)
    s += ctx.rs_score_delta

    # B+D confluence
    if ctx.bd_confluence:
        s += 1

    # Tier 2 BTC opposing
    if ctx.tier2_btc_opposing:
        s -= 1

    # Trap warning cap
    if ctx.trap_warning:
        s = min(s, cfg.B_TRAP_SCORE_CAP)

    return max(1, min(10, s))


# Tier 3 / Tier 4 RS score helper (v7 only Tier 3 per spec)
RS_OUTPERFORM_PCT = 0.10
RS_UNDERPERFORM_PCT = -0.10


def rs_score_delta(rs_value: float) -> int:
    if rs_value >= RS_OUTPERFORM_PCT:
        return 1
    if rs_value <= RS_UNDERPERFORM_PCT:
        return -1
    return 0


# Regime helpers reused from earlier code

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


def vol_ratio_prior5(df: pd.DataFrame) -> float:
    if len(df) < 6:
        return 1.0
    avg = float(df["volume"].iloc[-6:-1].mean())
    last = float(df["volume"].iloc[-1])
    if avg <= 0:
        return 1.0
    return last / avg
