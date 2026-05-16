"""Condition B — HTF pattern detection + 11 classifiers (SECONDARY).

Spec — detect first (pattern-agnostic), then classify. Patterns are checked in
priority order; the first matching pattern wins.

Continuation: Bull/Bear Flag, Pennant, Rectangle, Asc/Desc Triangle, Broadening
Reversal:    Double Top/Bottom, Head & Shoulders / Inverse H&S, Rounding Top/Bottom
Fallback:    UNCLASSIFIED (capped at 6/10 confidence)

Each classifier returns Optional[PatternResult]. The dispatcher tries them in
order and stops at the first match.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

import numpy as np
import pandas as pd

from .. import config as cfg
from ..indicators import atr, last_n_swings, swing_pivots
from .zones import Zone


@dataclass
class PatternResult:
    name: str                          # e.g. "Bull Flag"
    category: Literal["continuation", "reversal", "unclassified"]
    direction: str                     # "long" | "short"
    zone: Zone                         # retracement zone (boundary or neckline)
    invalidation: float
    confidence_cap: Optional[int] = None  # e.g. 6 for Broadening / Rounding / Unclassified
    note: str = ""


# ---------------------------------------------------------------------------
# Pattern-agnostic detection layer
# ---------------------------------------------------------------------------

def _has_compression_or_formation(df: pd.DataFrame, lookback: int = 20) -> bool:
    """Cheap gate — last `lookback` candles show contracting range OR clear boundary touches."""
    if len(df) < lookback + 5:
        return False
    recent = df.iloc[-lookback:]
    rng = recent["high"].max() - recent["low"].min()
    early_rng = df.iloc[-2 * lookback : -lookback]["high"].max() - df.iloc[-2 * lookback : -lookback]["low"].min()
    return rng > 0 and (rng < early_rng or rng > 0)


# ---------------------------------------------------------------------------
# Continuation patterns
# ---------------------------------------------------------------------------

def _bull_bear_flag(df: pd.DataFrame) -> Optional[PatternResult]:
    """Prior impulse >= 3x ATR; 3–10 candle consolidation sloping against impulse; first close out."""
    if len(df) < 25:
        return None
    a = float(atr(df, cfg.ATR_PERIOD).iloc[-cfg.B_FLAG_MAX_CANDLES - 5])
    if a <= 0:
        return None

    for cons_len in range(cfg.B_FLAG_MIN_CANDLES, cfg.B_FLAG_MAX_CANDLES + 1):
        if len(df) < cons_len + 5:
            continue
        cons = df.iloc[-cons_len - 1 : -1]   # consolidation candles
        last = df.iloc[-1]                    # potential breakout candle
        # Impulse = the candles before consolidation
        impulse_window = df.iloc[-cons_len - 6 : -cons_len - 1]
        if len(impulse_window) < 3:
            continue
        impulse_range = float(impulse_window["close"].iloc[-1] - impulse_window["close"].iloc[0])
        if abs(impulse_range) < cfg.B_FLAG_IMPULSE_ATR_MULT * a:
            continue

        cons_highs = cons["high"].values
        cons_lows = cons["low"].values
        # Volume declining
        vols = cons["volume"].values
        vol_declining = vols[0] > vols[-1]
        if not vol_declining:
            continue

        # Bull flag: impulse up, channel slopes down, break above channel high
        if impulse_range > 0:
            ch_high = float(cons_highs.max())
            ch_low = float(cons_lows.min())
            if cons_highs[-1] < cons_highs[0] and float(last["close"]) > ch_high:
                zone = Zone(low=ch_low, high=ch_high, kind="boundary",
                            direction="bullish", at=df.index[-2],
                            note="Bull Flag channel boundary")
                return PatternResult(
                    name="Bull Flag", category="continuation", direction="long",
                    zone=zone, invalidation=ch_low,
                    note=f"Impulse {impulse_range:.4f} >= 3x ATR; {cons_len}-bar channel; close > {ch_high:.4f}",
                )

        # Bear flag: impulse down, channel slopes up, break below channel low
        if impulse_range < 0:
            ch_high = float(cons_highs.max())
            ch_low = float(cons_lows.min())
            if cons_lows[-1] > cons_lows[0] and float(last["close"]) < ch_low:
                zone = Zone(low=ch_low, high=ch_high, kind="boundary",
                            direction="bearish", at=df.index[-2],
                            note="Bear Flag channel boundary")
                return PatternResult(
                    name="Bear Flag", category="continuation", direction="short",
                    zone=zone, invalidation=ch_high,
                    note=f"Impulse {impulse_range:.4f} <= -3x ATR; {cons_len}-bar channel; close < {ch_low:.4f}",
                )

    return None


def _pennant(df: pd.DataFrame) -> Optional[PatternResult]:
    """3x ATR impulse, then converging structure (HL + LH simultaneously), min 4 bars, close outside."""
    if len(df) < 15:
        return None
    a = float(atr(df, cfg.ATR_PERIOD).iloc[-10])
    if a <= 0:
        return None
    for cons_len in range(cfg.B_PENNANT_MIN_CANDLES, 10):
        if len(df) < cons_len + 6:
            continue
        cons = df.iloc[-cons_len - 1 : -1]
        last = df.iloc[-1]
        impulse_window = df.iloc[-cons_len - 6 : -cons_len - 1]
        impulse_range = float(impulse_window["close"].iloc[-1] - impulse_window["close"].iloc[0])
        if abs(impulse_range) < cfg.B_FLAG_IMPULSE_ATR_MULT * a:
            continue

        highs = cons["high"].values
        lows = cons["low"].values
        # converging: highs decreasing AND lows increasing
        highs_trend = np.polyfit(range(len(highs)), highs, 1)[0]
        lows_trend = np.polyfit(range(len(lows)), lows, 1)[0]
        if highs_trend < 0 and lows_trend > 0:
            ch_high = float(highs.max())
            ch_low = float(lows.min())
            close = float(last["close"])
            if impulse_range > 0 and close > ch_high:
                zone = Zone(low=ch_low, high=ch_high, kind="boundary", direction="bullish",
                            at=df.index[-2], note="Pennant boundary")
                return PatternResult("Bull Pennant", "continuation", "long", zone,
                                     invalidation=ch_low, note="Converging pennant after up-impulse")
            if impulse_range < 0 and close < ch_low:
                zone = Zone(low=ch_low, high=ch_high, kind="boundary", direction="bearish",
                            at=df.index[-2], note="Pennant boundary")
                return PatternResult("Bear Pennant", "continuation", "short", zone,
                                     invalidation=ch_high, note="Converging pennant after down-impulse")
    return None


def _rectangle(df: pd.DataFrame) -> Optional[PatternResult]:
    """Horizontal channel (slope within 1%), >= 4 touches (2H+2L), break either direction."""
    if len(df) < 20:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    # Horizontal-ness check
    h_arr = np.array([p.price for p in highs[-2:]])
    l_arr = np.array([p.price for p in lows[-2:]])
    h_slope = abs(h_arr[1] - h_arr[0]) / max(h_arr.mean(), 1e-9)
    l_slope = abs(l_arr[1] - l_arr[0]) / max(l_arr.mean(), 1e-9)
    if h_slope > cfg.B_RECTANGLE_HORIZ_TOL or l_slope > cfg.B_RECTANGLE_HORIZ_TOL:
        return None

    ch_high = float(h_arr.mean())
    ch_low = float(l_arr.mean())
    last_close = float(df["close"].iloc[-1])
    last_open = float(df["open"].iloc[-1])
    if last_close > ch_high and last_close > last_open:
        zone = Zone(low=ch_low, high=ch_high, kind="boundary", direction="bullish",
                    at=df.index[-1], note="Rectangle range")
        return PatternResult("Rectangle Breakout", "continuation", "long", zone,
                             invalidation=ch_low, note="Horizontal range broken upside")
    if last_close < ch_low and last_close < last_open:
        zone = Zone(low=ch_low, high=ch_high, kind="boundary", direction="bearish",
                    at=df.index[-1], note="Rectangle range")
        return PatternResult("Rectangle Breakdown", "continuation", "short", zone,
                             invalidation=ch_high, note="Horizontal range broken downside")
    return None


def _ascending_descending_triangle(df: pd.DataFrame) -> Optional[PatternResult]:
    """Ascending: flat resistance + rising lows. Descending: flat support + falling highs."""
    if len(df) < 20:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < cfg.B_TRIANGLE_FLAT_TOUCHES or len(lows) < cfg.B_TRIANGLE_CONV_TOUCHES:
        return None

    h_prices = np.array([p.price for p in highs[-cfg.B_TRIANGLE_FLAT_TOUCHES:]])
    l_prices = np.array([p.price for p in lows[-cfg.B_TRIANGLE_FLAT_TOUCHES:]])
    last_close = float(df["close"].iloc[-1])

    h_slope = (h_prices[-1] - h_prices[0]) / max(h_prices.mean(), 1e-9)
    l_slope = (l_prices[-1] - l_prices[0]) / max(l_prices.mean(), 1e-9)

    # Ascending: flat highs, rising lows
    if abs(h_slope) < 0.005 and l_slope > 0.01:
        flat_level = float(h_prices.mean())
        if last_close > flat_level:
            zone = Zone(low=float(l_prices[-1]), high=flat_level, kind="boundary",
                        direction="bullish", at=df.index[-1], note="Ascending Triangle boundary")
            return PatternResult("Ascending Triangle", "continuation", "long", zone,
                                 invalidation=float(l_prices[-1]),
                                 note=f"Flat resistance at {flat_level:.4f}, rising lows")
    # Descending: flat lows, falling highs
    if abs(l_slope) < 0.005 and h_slope < -0.01:
        flat_level = float(l_prices.mean())
        if last_close < flat_level:
            zone = Zone(low=flat_level, high=float(h_prices[-1]), kind="boundary",
                        direction="bearish", at=df.index[-1], note="Descending Triangle boundary")
            return PatternResult("Descending Triangle", "continuation", "short", zone,
                                 invalidation=float(h_prices[-1]),
                                 note=f"Flat support at {flat_level:.4f}, falling highs")
    return None


def _broadening(df: pd.DataFrame) -> Optional[PatternResult]:
    """Expanding structure: lower lows AND higher highs simultaneously, min 4 candles."""
    if len(df) < 20:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    # Expanding: most recent high > previous high AND most recent low < previous low
    if highs[-1].price > highs[-2].price and lows[-1].price < lows[-2].price:
        last = df.iloc[-1]
        last_close = float(last["close"])
        # Volume expansion check
        vol_avg = float(df["volume"].iloc[-21:-1].mean()) if len(df) > 21 else 0
        vol_now = float(last["volume"])
        if vol_avg <= 0 or vol_now < 1.3 * vol_avg:
            return None

        if last_close > highs[-1].price:
            zone = Zone(low=lows[-1].price, high=highs[-1].price, kind="boundary",
                        direction="bullish", at=df.index[-1], note="Broadening boundary")
            return PatternResult("Broadening Formation", "continuation", "long", zone,
                                 invalidation=lows[-1].price, confidence_cap=6,
                                 note="Megaphone — confidence capped at 6")
        if last_close < lows[-1].price:
            zone = Zone(low=lows[-1].price, high=highs[-1].price, kind="boundary",
                        direction="bearish", at=df.index[-1], note="Broadening boundary")
            return PatternResult("Broadening Formation", "continuation", "short", zone,
                                 invalidation=highs[-1].price, confidence_cap=6,
                                 note="Megaphone — confidence capped at 6")
    return None


# ---------------------------------------------------------------------------
# Reversal patterns
# ---------------------------------------------------------------------------

def _double_top_bottom(df: pd.DataFrame) -> Optional[PatternResult]:
    """Two swing highs/lows within 0.5%; min separation 5 bars; close beyond neckline."""
    pivots = swing_pivots(df)
    last_close = float(df["close"].iloc[-1])

    # Double top
    highs = [p for p in pivots if p.kind == "high"][-3:]
    lows = [p for p in pivots if p.kind == "low"][-3:]
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        if (h2.idx - h1.idx) >= cfg.B_DOUBLE_MIN_SEPARATION:
            diff = abs(h1.price - h2.price) / max(h1.price, h2.price)
            if diff <= cfg.B_DOUBLE_TOL_PCT:
                # Find neckline = lowest low between the two highs
                between = [p for p in pivots if p.kind == "low" and h1.idx < p.idx < h2.idx]
                if between:
                    neckline = min(between, key=lambda p: p.price).price
                    if last_close < neckline:
                        zone = Zone(low=neckline * 0.997, high=neckline * 1.003, kind="neckline",
                                    direction="bearish", at=df.index[-1],
                                    note=f"Double Top neckline {neckline:.6f}")
                        return PatternResult("Double Top", "reversal", "short", zone,
                                             invalidation=max(h1.price, h2.price),
                                             note=f"Tops at {h1.price:.4f}, {h2.price:.4f}")

    # Double bottom
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        if (l2.idx - l1.idx) >= cfg.B_DOUBLE_MIN_SEPARATION:
            diff = abs(l1.price - l2.price) / max(l1.price, l2.price)
            if diff <= cfg.B_DOUBLE_TOL_PCT:
                between = [p for p in pivots if p.kind == "high" and l1.idx < p.idx < l2.idx]
                if between:
                    neckline = max(between, key=lambda p: p.price).price
                    if last_close > neckline:
                        zone = Zone(low=neckline * 0.997, high=neckline * 1.003, kind="neckline",
                                    direction="bullish", at=df.index[-1],
                                    note=f"Double Bottom neckline {neckline:.6f}")
                        return PatternResult("Double Bottom", "reversal", "long", zone,
                                             invalidation=min(l1.price, l2.price),
                                             note=f"Bottoms at {l1.price:.4f}, {l2.price:.4f}")
    return None


def _head_shoulders(df: pd.DataFrame) -> Optional[PatternResult]:
    """3 swing highs/lows; centre exceeds both shoulders; shoulders within 1%; close beyond neckline."""
    pivots = swing_pivots(df)
    last_close = float(df["close"].iloc[-1])

    highs = [p for p in pivots if p.kind == "high"][-4:]
    lows = [p for p in pivots if p.kind == "low"][-4:]

    # H&S — bearish
    if len(highs) >= 3 and len(lows) >= 2:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head.price > ls.price and head.price > rs.price:
            shoulder_diff = abs(ls.price - rs.price) / max(ls.price, rs.price)
            if shoulder_diff <= cfg.B_HS_SHOULDER_TOL_PCT:
                troughs = [p for p in lows if ls.idx < p.idx < rs.idx]
                if len(troughs) >= 2:
                    neckline = float(np.mean([troughs[0].price, troughs[-1].price]))
                    if last_close < neckline:
                        zone = Zone(low=neckline * 0.997, high=neckline * 1.003, kind="neckline",
                                    direction="bearish", at=df.index[-1],
                                    note=f"H&S neckline {neckline:.6f}")
                        return PatternResult("Head and Shoulders", "reversal", "short", zone,
                                             invalidation=head.price,
                                             note=f"Shoulders {ls.price:.4f} / {rs.price:.4f}, head {head.price:.4f}")

    # Inverse H&S — bullish
    if len(lows) >= 3 and len(highs) >= 2:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head.price < ls.price and head.price < rs.price:
            shoulder_diff = abs(ls.price - rs.price) / max(ls.price, rs.price)
            if shoulder_diff <= cfg.B_HS_SHOULDER_TOL_PCT:
                peaks = [p for p in highs if ls.idx < p.idx < rs.idx]
                if len(peaks) >= 2:
                    neckline = float(np.mean([peaks[0].price, peaks[-1].price]))
                    if last_close > neckline:
                        zone = Zone(low=neckline * 0.997, high=neckline * 1.003, kind="neckline",
                                    direction="bullish", at=df.index[-1],
                                    note=f"Inverse H&S neckline {neckline:.6f}")
                        return PatternResult("Inverse Head and Shoulders", "reversal", "long", zone,
                                             invalidation=head.price,
                                             note=f"Shoulders {ls.price:.4f} / {rs.price:.4f}, head {head.price:.4f}")
    return None


def _rounding(df: pd.DataFrame) -> Optional[PatternResult]:
    """Gradual curved arc across >= 8 candles; volume declines through arc, expands at break."""
    if len(df) < cfg.B_ROUNDING_MIN_CANDLES + 3:
        return None
    window = df.iloc[-cfg.B_ROUNDING_MIN_CANDLES - 2 : -1]
    closes = window["close"].values
    vols = window["volume"].values
    if len(closes) < 8:
        return None

    # Quadratic fit; positive coeff = bowl (bullish rounding bottom), negative = cap (bearish)
    coeffs = np.polyfit(range(len(closes)), closes, 2)
    a = coeffs[0]
    # Volume must decline through arc (early vols higher than late vols, before breakout)
    if len(vols) < 6:
        return None
    if vols[: len(vols) // 2].mean() < vols[len(vols) // 2 :].mean():
        return None

    last = df.iloc[-1]
    arc_high = float(window["high"].max())
    arc_low = float(window["low"].min())
    vol_avg = float(window["volume"].mean())
    vol_now = float(last["volume"])
    if vol_avg <= 0 or vol_now < 1.5 * vol_avg:
        return None

    last_close = float(last["close"])
    if a > 0 and last_close > arc_high:
        zone = Zone(low=arc_low, high=arc_high, kind="boundary", direction="bullish",
                    at=df.index[-1], note="Rounding Bottom arc")
        return PatternResult("Rounding Bottom", "reversal", "long", zone,
                             invalidation=arc_low, confidence_cap=6,
                             note="Curved bowl arc with volume expansion at break")
    if a < 0 and last_close < arc_low:
        zone = Zone(low=arc_low, high=arc_high, kind="boundary", direction="bearish",
                    at=df.index[-1], note="Rounding Top arc")
        return PatternResult("Rounding Top", "reversal", "short", zone,
                             invalidation=arc_high, confidence_cap=6,
                             note="Curved cap arc with volume expansion at break")
    return None


def _unclassified(df: pd.DataFrame) -> Optional[PatternResult]:
    """Detection layer fired (compression present) but no classifier matched."""
    if not _has_compression_or_formation(df):
        return None
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    prior = df.iloc[-20:-1]
    high_band = float(prior["high"].max())
    low_band = float(prior["low"].min())
    close = float(last["close"])
    if close > high_band and float(last["close"]) > float(last["open"]):
        zone = Zone(low=low_band, high=high_band, kind="boundary", direction="bullish",
                    at=df.index[-1], note="Unclassified compression")
        return PatternResult("Pattern Breakout — Unclassified", "unclassified", "long", zone,
                             invalidation=low_band, confidence_cap=6, note="No classifier matched")
    if close < low_band and float(last["close"]) < float(last["open"]):
        zone = Zone(low=low_band, high=high_band, kind="boundary", direction="bearish",
                    at=df.index[-1], note="Unclassified compression")
        return PatternResult("Pattern Breakout — Unclassified", "unclassified", "short", zone,
                             invalidation=high_band, confidence_cap=6, note="No classifier matched")
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_DETECTORS = [
    _bull_bear_flag,
    _pennant,
    _rectangle,
    _ascending_descending_triangle,
    _broadening,
    _double_top_bottom,
    _head_shoulders,
    _rounding,
    _unclassified,
]


def detect_condition_b(df: pd.DataFrame) -> Optional[PatternResult]:
    if df is None or len(df) < 15:
        return None
    for fn in _DETECTORS:
        try:
            res = fn(df)
            if res:
                return res
        except Exception:
            continue
    return None
