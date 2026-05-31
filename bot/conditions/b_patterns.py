"""Condition B (v7) — HTF Pattern Breakout / Breakdown (INDEPENDENT trigger).

Detection-then-classification. Trap detection runs BEFORE classification.
Each pattern has its own validation rules per v7. The dispatcher returns
the FIRST matching pattern.

Classifiers (14 total):
  Continuation: Bull Flag, Bear Flag, Pennant (bull/bear),
                Rectangle Up/Down, Ascending Triangle, Descending Triangle,
                Ascending Channel, Descending Channel,
                Broadening Formation, Ascending Broadening Wedge, Descending Broadening Wedge
  Reversal:    Double Top, Double Bottom, Head & Shoulders, Inverse H&S,
               Rounding Top, Rounding Bottom
  Fallback:    Pattern Breakout — Unclassified
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import List, Literal, Optional

import numpy as np
import pandas as pd

from .. import config as cfg
from ..indicators import atr, last_n_swings, swing_pivots
from .zones import Zone


@dataclass
class TrapAnalysis:
    flags: List[str]
    is_trap: bool


@dataclass
class PatternResult:
    name: str
    category: Literal["continuation", "reversal", "unclassified"]
    direction: str
    zone: Zone
    swing_high: float                 # for premium/discount calc
    swing_low: float
    confidence_cap: Optional[int] = None
    note: str = ""
    trap: Optional[TrapAnalysis] = None


# ---------------------------------------------------------------------------
# Trap detection — v7
# ---------------------------------------------------------------------------

def detect_trap(df: pd.DataFrame, boundary_low: float, boundary_high: float) -> TrapAnalysis:
    flags: List[str] = []
    if len(df) < 7:
        return TrapAnalysis(flags=[], is_trap=False)
    breakout = df.iloc[-1]
    o, h, l, c, v = (float(breakout["open"]), float(breakout["high"]),
                     float(breakout["low"]), float(breakout["close"]),
                     float(breakout["volume"]))
    rng = h - l
    body = abs(c - o)

    # 1. Body < 50% of total range
    if rng > 0 and body / rng < cfg.B_TRAP_BODY_MIN_RATIO:
        flags.append("WICK_HEAVY")

    # 2. Volume declining vs prior-5 average
    prior5_vol = df["volume"].iloc[-6:-1].mean() if len(df) >= 6 else 0
    if prior5_vol > 0 and v < prior5_vol:
        flags.append("VOL_DECLINING")

    # 3. Reclose inside boundary within 2 candles — checked by caller after
    # subsequent candles close; here we approximate with the next candle if
    # available. For initial detection (breakout candle itself), skip.

    # 4. Breakout occurs 23:00-23:30 UTC with no prior daily structural context
    try:
        ts = df.index[-1]
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        hour_utc = ts.hour
        minute_utc = ts.minute
        if hour_utc == 23 and minute_utc <= 30:
            flags.append("LATE_UTC")
    except Exception:
        pass

    return TrapAnalysis(flags=flags, is_trap=len(flags) >= 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slope_per_bar(prices: np.ndarray) -> float:
    if len(prices) < 2:
        return 0.0
    return float(np.polyfit(range(len(prices)), prices, 1)[0])


def _slope_deg(slope_per_bar: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    import math
    return abs(math.degrees(math.atan(slope_per_bar / scale)))


# ---------------------------------------------------------------------------
# Continuation patterns
# ---------------------------------------------------------------------------

def _bull_bear_flag(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    for cons_len in range(cfg.B_FLAG_MIN_CANDLES, cfg.B_FLAG_MAX_CANDLES + 1):
        if len(df) < cons_len + 6:
            continue
        cons = df.iloc[-cons_len - 1 : -1]
        last = df.iloc[-1]
        impulse_window = df.iloc[-cons_len - 6 : -cons_len - 1]
        if len(impulse_window) < 3:
            continue
        impulse_range = float(impulse_window["close"].iloc[-1] - impulse_window["close"].iloc[0])
        if abs(impulse_range) < cfg.B_FLAG_IMPULSE_ATR_MULT * atr_now:
            continue
        if cons["volume"].iloc[0] <= cons["volume"].iloc[-1]:
            continue  # volume must be declining

        cons_highs = cons["high"].values.astype(float)
        cons_lows = cons["low"].values.astype(float)
        ch_high = float(cons_highs.max())
        ch_low = float(cons_lows.min())

        # channel slope vs impulse direction
        mid_slope = _slope_per_bar((cons_highs + cons_lows) / 2)
        scale = atr_now
        angle = _slope_deg(mid_slope, scale)
        if angle > cfg.B_FLAG_MAX_CHANNEL_ANGLE_DEG if hasattr(cfg, 'B_FLAG_MAX_CHANNEL_ANGLE_DEG') else cfg.B_FLAG_CHANNEL_MAX_ANGLE_DEG:
            continue

        close = float(last["close"])
        if impulse_range > 0 and mid_slope < 0 and close > ch_high:
            return PatternResult(
                name="Bull Flag", category="continuation", direction="long",
                zone=Zone(low=ch_low, high=ch_high, kind="boundary",
                          direction="bullish", at=df.index[-2],
                          note="Bull Flag compression boundary"),
                swing_high=float(df["high"].iloc[-cons_len - 6:].max()),
                swing_low=ch_low,
                note=f"Impulse {impulse_range:.4f} >= 3xATR; channel down; close > {ch_high:.4f}",
            )
        if impulse_range < 0 and mid_slope > 0 and close < ch_low:
            return PatternResult(
                name="Bear Flag", category="continuation", direction="short",
                zone=Zone(low=ch_low, high=ch_high, kind="boundary",
                          direction="bearish", at=df.index[-2],
                          note="Bear Flag compression boundary"),
                swing_high=ch_high,
                swing_low=float(df["low"].iloc[-cons_len - 6:].min()),
                note=f"Impulse {impulse_range:.4f} <= -3xATR; channel up; close < {ch_low:.4f}",
            )
    return None


def _pennant(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    for cons_len in range(cfg.B_PENNANT_MIN_CANDLES, 10):
        if len(df) < cons_len + 6:
            continue
        cons = df.iloc[-cons_len - 1 : -1]
        last = df.iloc[-1]
        impulse_window = df.iloc[-cons_len - 6 : -cons_len - 1]
        impulse_range = float(impulse_window["close"].iloc[-1] - impulse_window["close"].iloc[0])
        if abs(impulse_range) < cfg.B_FLAG_IMPULSE_ATR_MULT * atr_now:
            continue

        highs = cons["high"].values.astype(float)
        lows = cons["low"].values.astype(float)
        if len(highs) < 4:
            continue
        h_slope = _slope_per_bar(highs)
        l_slope = _slope_per_bar(lows)
        if not (h_slope < 0 and l_slope > 0):
            continue

        # 20% contraction
        start_width = float(highs[0] - lows[0])
        end_width = float(highs[-1] - lows[-1])
        if start_width <= 0 or (start_width - end_width) / start_width < cfg.B_PENNANT_CONTRACTION_PCT:
            continue

        ch_high = float(highs.max())
        ch_low = float(lows.min())
        close = float(last["close"])
        if impulse_range > 0 and close > ch_high:
            return PatternResult("Bull Pennant", "continuation", "long",
                                 Zone(low=ch_low, high=ch_high, kind="boundary",
                                      direction="bullish", at=df.index[-2],
                                      note="Pennant boundary"),
                                 swing_high=ch_high, swing_low=ch_low,
                                 note="Converging pennant after up-impulse")
        if impulse_range < 0 and close < ch_low:
            return PatternResult("Bear Pennant", "continuation", "short",
                                 Zone(low=ch_low, high=ch_high, kind="boundary",
                                      direction="bearish", at=df.index[-2],
                                      note="Pennant boundary"),
                                 swing_high=ch_high, swing_low=ch_low,
                                 note="Converging pennant after down-impulse")
    return None


def _rectangle(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    if len(df) < 20:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    h_prices = np.array([p.price for p in highs[-2:]])
    l_prices = np.array([p.price for p in lows[-2:]])
    # v7: deviation between boundary touches must not exceed 0.25x ATR
    if abs(h_prices[1] - h_prices[0]) > cfg.B_RECTANGLE_BOUNDARY_DEV_ATR * atr_now:
        return None
    if abs(l_prices[1] - l_prices[0]) > cfg.B_RECTANGLE_BOUNDARY_DEV_ATR * atr_now:
        return None

    ch_high = float(h_prices.mean())
    ch_low = float(l_prices.mean())
    last = df.iloc[-1]
    close = float(last["close"])
    open_ = float(last["open"])

    if close > ch_high and close > open_:
        return PatternResult("Rectangle Breakout", "continuation", "long",
                             Zone(low=ch_low, high=ch_high, kind="boundary",
                                  direction="bullish", at=df.index[-1],
                                  note="Rectangle range"),
                             swing_high=ch_high, swing_low=ch_low,
                             note="Horizontal range broken upside")
    if close < ch_low and close < open_:
        return PatternResult("Rectangle Breakdown", "continuation", "short",
                             Zone(low=ch_low, high=ch_high, kind="boundary",
                                  direction="bearish", at=df.index[-1],
                                  note="Rectangle range"),
                             swing_high=ch_high, swing_low=ch_low,
                             note="Horizontal range broken downside")
    return None


def _triangle(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    if len(df) < 20 or atr_now <= 0:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    if len(highs) < cfg.B_TRIANGLE_FLAT_TOUCHES or len(lows) < cfg.B_TRIANGLE_CONV_TOUCHES:
        return None
    h_prices = np.array([p.price for p in highs[-cfg.B_TRIANGLE_FLAT_TOUCHES:]])
    l_prices = np.array([p.price for p in lows[-cfg.B_TRIANGLE_FLAT_TOUCHES:]])
    last_close = float(df["close"].iloc[-1])

    # Ascending: flat highs, rising lows where each step >= 0.15 ATR
    flat_h_dev = float(h_prices.max() - h_prices.min())
    if flat_h_dev <= cfg.B_RECTANGLE_BOUNDARY_DEV_ATR * atr_now:
        steps_ok = all(
            (l_prices[i + 1] - l_prices[i]) >= cfg.B_TRIANGLE_MIN_STEP_ATR * atr_now
            for i in range(len(l_prices) - 1)
        )
        if steps_ok and last_close > float(h_prices.mean()):
            return PatternResult("Ascending Triangle", "continuation", "long",
                                 Zone(low=float(l_prices[-1]), high=float(h_prices.mean()),
                                      kind="boundary", direction="bullish",
                                      at=df.index[-1], note="Ascending Triangle boundary"),
                                 swing_high=float(h_prices.mean()), swing_low=float(l_prices[-1]),
                                 note=f"Flat resistance {float(h_prices.mean()):.4f}, rising lows step>=0.15xATR")

    # Descending: flat lows, falling highs
    flat_l_dev = float(l_prices.max() - l_prices.min())
    if flat_l_dev <= cfg.B_RECTANGLE_BOUNDARY_DEV_ATR * atr_now:
        steps_ok = all(
            (h_prices[i] - h_prices[i + 1]) >= cfg.B_TRIANGLE_MIN_STEP_ATR * atr_now
            for i in range(len(h_prices) - 1)
        )
        if steps_ok and last_close < float(l_prices.mean()):
            return PatternResult("Descending Triangle", "continuation", "short",
                                 Zone(low=float(l_prices.mean()), high=float(h_prices[-1]),
                                      kind="boundary", direction="bearish",
                                      at=df.index[-1], note="Descending Triangle boundary"),
                                 swing_high=float(h_prices[-1]), swing_low=float(l_prices.mean()),
                                 note=f"Flat support {float(l_prices.mean()):.4f}, falling highs step>=0.15xATR")
    return None


def _channel(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    """Ascending / Descending Channel — parallel sloping trendlines."""
    if len(df) < 25 or atr_now <= 0:
        return None
    pivots = swing_pivots(df.iloc[-40:])
    highs = [p for p in pivots if p.kind == "high"][-3:]
    lows = [p for p in pivots if p.kind == "low"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return None

    h_arr = np.array([p.price for p in highs])
    l_arr = np.array([p.price for p in lows])
    h_idx = np.array([p.idx for p in highs])
    l_idx = np.array([p.idx for p in lows])

    h_slope = _slope_per_bar(h_arr) if len(h_arr) >= 2 else 0.0
    l_slope = _slope_per_bar(l_arr) if len(l_arr) >= 2 else 0.0

    # Same direction slopes, and slopes broadly parallel (within 50% relative)
    if h_slope == 0 or l_slope == 0:
        return None
    if (h_slope > 0) != (l_slope > 0):
        return None
    if abs(h_slope - l_slope) / max(abs(h_slope), abs(l_slope)) > 0.5:
        return None

    # Width stability
    widths = []
    n = len(df)
    for i in range(n - 10, n):
        # estimate boundary prices at this bar (linear extrap from last pivots)
        upper = h_arr[-1] + h_slope * (i - h_idx[-1])
        lower = l_arr[-1] + l_slope * (i - l_idx[-1])
        widths.append(upper - lower)
    width_range = max(widths) - min(widths)
    if width_range > cfg.B_CHANNEL_WIDTH_DEV_ATR * atr_now:
        return None

    last_close = float(df["close"].iloc[-1])
    upper_now = h_arr[-1] + h_slope * (n - 1 - h_idx[-1])
    lower_now = l_arr[-1] + l_slope * (n - 1 - l_idx[-1])

    if h_slope > 0:
        # Ascending Channel — breakout above upper => long, below lower => short
        if last_close > upper_now:
            return PatternResult("Ascending Channel Breakout", "continuation", "long",
                                 Zone(low=float(lower_now), high=float(upper_now),
                                      kind="boundary", direction="bullish",
                                      at=df.index[-1], note="Ascending Channel"),
                                 swing_high=float(upper_now), swing_low=float(lower_now),
                                 note="Breakout above ascending channel upper boundary")
        if last_close < lower_now:
            return PatternResult("Ascending Channel Breakdown", "continuation", "short",
                                 Zone(low=float(lower_now), high=float(upper_now),
                                      kind="boundary", direction="bearish",
                                      at=df.index[-1], note="Ascending Channel"),
                                 swing_high=float(upper_now), swing_low=float(lower_now),
                                 note="Breakdown below ascending channel lower boundary")
    else:
        # Descending Channel
        if last_close > upper_now:
            return PatternResult("Descending Channel Breakout", "continuation", "long",
                                 Zone(low=float(lower_now), high=float(upper_now),
                                      kind="boundary", direction="bullish",
                                      at=df.index[-1], note="Descending Channel"),
                                 swing_high=float(upper_now), swing_low=float(lower_now),
                                 note="Breakout above descending channel upper boundary")
        if last_close < lower_now:
            return PatternResult("Descending Channel Breakdown", "continuation", "short",
                                 Zone(low=float(lower_now), high=float(upper_now),
                                      kind="boundary", direction="bearish",
                                      at=df.index[-1], note="Descending Channel"),
                                 swing_high=float(upper_now), swing_low=float(lower_now),
                                 note="Breakdown below descending channel lower boundary")
    return None


def _broadening(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    if len(df) < 20 or atr_now <= 0:
        return None
    pivots = swing_pivots(df.iloc[-30:])
    highs = [p for p in pivots if p.kind == "high"][-3:]
    lows = [p for p in pivots if p.kind == "low"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return None

    # v7: lower lows AND higher highs simultaneously, each step exceeding prior by >= 0.25 ATR
    if highs[-1].price <= highs[-2].price:
        return None
    if lows[-1].price >= lows[-2].price:
        return None
    if (highs[-1].price - highs[-2].price) < cfg.B_BROADENING_EXPANSION_STEP_ATR * atr_now:
        return None
    if (lows[-2].price - lows[-1].price) < cfg.B_BROADENING_EXPANSION_STEP_ATR * atr_now:
        return None

    last = df.iloc[-1]
    last_close = float(last["close"])
    # volume expansion check
    if len(df) >= 21:
        vol_avg = float(df["volume"].iloc[-21:-1].mean())
        if vol_avg > 0 and float(last["volume"]) < 1.3 * vol_avg:
            return None

    if last_close > highs[-1].price:
        return PatternResult("Broadening Formation", "continuation", "long",
                             Zone(low=lows[-1].price, high=highs[-1].price,
                                  kind="boundary", direction="bullish",
                                  at=df.index[-1], note="Broadening boundary"),
                             swing_high=highs[-1].price, swing_low=lows[-1].price,
                             confidence_cap=6,
                             note="Megaphone — confidence capped at 6")
    if last_close < lows[-1].price:
        return PatternResult("Broadening Formation", "continuation", "short",
                             Zone(low=lows[-1].price, high=highs[-1].price,
                                  kind="boundary", direction="bearish",
                                  at=df.index[-1], note="Broadening boundary"),
                             swing_high=highs[-1].price, swing_low=lows[-1].price,
                             confidence_cap=6,
                             note="Megaphone — confidence capped at 6")
    return None


def _broadening_wedge(df: pd.DataFrame, atr_now: float) -> Optional[PatternResult]:
    """Ascending / Descending Broadening Wedge — diverging trendlines."""
    if len(df) < 25 or atr_now <= 0:
        return None
    pivots = swing_pivots(df.iloc[-40:])
    highs = [p for p in pivots if p.kind == "high"][-3:]
    lows = [p for p in pivots if p.kind == "low"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return None

    # Expanding swings progressively (step >= 0.25 ATR)
    h_diff = highs[-1].price - highs[-2].price
    l_diff = lows[-1].price - lows[-2].price
    step_thresh = cfg.B_WEDGE_EXPANSION_STEP_ATR * atr_now

    # Ascending wedge: both boundaries slope up, expansion widening
    if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
        # diverging? upper rises faster than lower (or vice versa)
        if h_diff > step_thresh and abs(h_diff) > abs(l_diff) * 1.1:
            last_close = float(df["close"].iloc[-1])
            if last_close > highs[-1].price:
                return PatternResult("Ascending Broadening Wedge Breakout", "continuation", "long",
                                     Zone(low=lows[-1].price, high=highs[-1].price,
                                          kind="boundary", direction="bullish",
                                          at=df.index[-1], note="Ascending Broadening Wedge"),
                                     swing_high=highs[-1].price, swing_low=lows[-1].price,
                                     confidence_cap=6,
                                     note="Diverging upward wedge — capped 6 unless D confluence")
            if last_close < lows[-1].price:
                return PatternResult("Ascending Broadening Wedge Breakdown", "continuation", "short",
                                     Zone(low=lows[-1].price, high=highs[-1].price,
                                          kind="boundary", direction="bearish",
                                          at=df.index[-1], note="Ascending Broadening Wedge"),
                                     swing_high=highs[-1].price, swing_low=lows[-1].price,
                                     confidence_cap=6,
                                     note="Diverging upward wedge — capped 6 unless D confluence")
    # Descending wedge: both slope down, expansion widening
    if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
        if abs(l_diff) > step_thresh and abs(l_diff) > abs(h_diff) * 1.1:
            last_close = float(df["close"].iloc[-1])
            if last_close > highs[-1].price:
                return PatternResult("Descending Broadening Wedge Breakout", "continuation", "long",
                                     Zone(low=lows[-1].price, high=highs[-1].price,
                                          kind="boundary", direction="bullish",
                                          at=df.index[-1], note="Descending Broadening Wedge"),
                                     swing_high=highs[-1].price, swing_low=lows[-1].price,
                                     confidence_cap=6,
                                     note="Diverging downward wedge — capped 6 unless D confluence")
            if last_close < lows[-1].price:
                return PatternResult("Descending Broadening Wedge Breakdown", "continuation", "short",
                                     Zone(low=lows[-1].price, high=highs[-1].price,
                                          kind="boundary", direction="bearish",
                                          at=df.index[-1], note="Descending Broadening Wedge"),
                                     swing_high=highs[-1].price, swing_low=lows[-1].price,
                                     confidence_cap=6,
                                     note="Diverging downward wedge — capped 6 unless D confluence")
    return None


# ---------------------------------------------------------------------------
# Reversal patterns
# ---------------------------------------------------------------------------

def _double_top_bottom(df: pd.DataFrame) -> Optional[PatternResult]:
    pivots = swing_pivots(df)
    last_close = float(df["close"].iloc[-1])

    highs = [p for p in pivots if p.kind == "high"][-3:]
    lows = [p for p in pivots if p.kind == "low"][-3:]
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        if (h2.idx - h1.idx) >= cfg.B_DOUBLE_MIN_SEPARATION:
            diff = abs(h1.price - h2.price) / max(h1.price, h2.price)
            if diff <= cfg.B_DOUBLE_TOL_PCT:
                between = [p for p in pivots if p.kind == "low" and h1.idx < p.idx < h2.idx]
                if between:
                    neckline = min(between, key=lambda p: p.price).price
                    if last_close < neckline:
                        return PatternResult("Double Top", "reversal", "short",
                                             Zone(low=neckline * 0.997, high=neckline * 1.003,
                                                  kind="neckline", direction="bearish",
                                                  at=df.index[-1],
                                                  note=f"Double Top neckline {neckline:.6f}"),
                                             swing_high=max(h1.price, h2.price),
                                             swing_low=neckline,
                                             note=f"Tops at {h1.price:.4f}, {h2.price:.4f}")
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        if (l2.idx - l1.idx) >= cfg.B_DOUBLE_MIN_SEPARATION:
            diff = abs(l1.price - l2.price) / max(l1.price, l2.price)
            if diff <= cfg.B_DOUBLE_TOL_PCT:
                between = [p for p in pivots if p.kind == "high" and l1.idx < p.idx < l2.idx]
                if between:
                    neckline = max(between, key=lambda p: p.price).price
                    if last_close > neckline:
                        return PatternResult("Double Bottom", "reversal", "long",
                                             Zone(low=neckline * 0.997, high=neckline * 1.003,
                                                  kind="neckline", direction="bullish",
                                                  at=df.index[-1],
                                                  note=f"Double Bottom neckline {neckline:.6f}"),
                                             swing_high=neckline,
                                             swing_low=min(l1.price, l2.price),
                                             note=f"Bottoms at {l1.price:.4f}, {l2.price:.4f}")
    return None


def _head_shoulders(df: pd.DataFrame) -> Optional[PatternResult]:
    pivots = swing_pivots(df)
    last_close = float(df["close"].iloc[-1])
    highs = [p for p in pivots if p.kind == "high"][-4:]
    lows = [p for p in pivots if p.kind == "low"][-4:]

    if len(highs) >= 3 and len(lows) >= 2:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head.price > ls.price and head.price > rs.price:
            sd = abs(ls.price - rs.price) / max(ls.price, rs.price)
            if sd <= cfg.B_HS_SHOULDER_TOL_PCT:
                troughs = [p for p in lows if ls.idx < p.idx < rs.idx]
                if len(troughs) >= 2:
                    neckline = float(np.mean([troughs[0].price, troughs[-1].price]))
                    if last_close < neckline:
                        return PatternResult("Head and Shoulders", "reversal", "short",
                                             Zone(low=neckline * 0.997, high=neckline * 1.003,
                                                  kind="neckline", direction="bearish",
                                                  at=df.index[-1],
                                                  note=f"H&S neckline {neckline:.6f}"),
                                             swing_high=head.price, swing_low=neckline,
                                             note=f"LS {ls.price:.4f} / Head {head.price:.4f} / RS {rs.price:.4f}")

    if len(lows) >= 3 and len(highs) >= 2:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head.price < ls.price and head.price < rs.price:
            sd = abs(ls.price - rs.price) / max(ls.price, rs.price)
            if sd <= cfg.B_HS_SHOULDER_TOL_PCT:
                peaks = [p for p in highs if ls.idx < p.idx < rs.idx]
                if len(peaks) >= 2:
                    neckline = float(np.mean([peaks[0].price, peaks[-1].price]))
                    if last_close > neckline:
                        return PatternResult("Inverse Head and Shoulders", "reversal", "long",
                                             Zone(low=neckline * 0.997, high=neckline * 1.003,
                                                  kind="neckline", direction="bullish",
                                                  at=df.index[-1],
                                                  note=f"IH&S neckline {neckline:.6f}"),
                                             swing_high=neckline, swing_low=head.price,
                                             note=f"LS {ls.price:.4f} / Head {head.price:.4f} / RS {rs.price:.4f}")
    return None


def _rounding(df: pd.DataFrame) -> Optional[PatternResult]:
    if len(df) < cfg.B_ROUNDING_MIN_CANDLES + 3:
        return None
    window = df.iloc[-cfg.B_ROUNDING_MIN_CANDLES - 2 : -1]
    closes = window["close"].values.astype(float)
    vols = window["volume"].values.astype(float)
    if len(closes) < 8:
        return None

    coeffs = np.polyfit(range(len(closes)), closes, 2)
    a = coeffs[0]
    if vols[: len(vols) // 2].mean() < vols[len(vols) // 2 :].mean():
        return None  # volume not declining through arc

    # Counter-swing rule: no single counter-swing >= 50% of total arc depth
    arc_depth = float(closes.max() - closes.min())
    if arc_depth <= 0:
        return None
    # Approximate: max single-bar reversal magnitude vs arc depth
    diffs = np.abs(np.diff(closes))
    max_counter = float(diffs.max())
    if max_counter > cfg.B_ROUNDING_MAX_COUNTERSWING_PCT * arc_depth:
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
        return PatternResult("Rounding Bottom", "reversal", "long",
                             Zone(low=arc_low, high=arc_high, kind="boundary",
                                  direction="bullish", at=df.index[-1],
                                  note="Rounding Bottom arc"),
                             swing_high=arc_high, swing_low=arc_low,
                             confidence_cap=6,
                             note="Curved bowl arc with volume expansion at break")
    if a < 0 and last_close < arc_low:
        return PatternResult("Rounding Top", "reversal", "short",
                             Zone(low=arc_low, high=arc_high, kind="boundary",
                                  direction="bearish", at=df.index[-1],
                                  note="Rounding Top arc"),
                             swing_high=arc_high, swing_low=arc_low,
                             confidence_cap=6,
                             note="Curved cap arc with volume expansion at break")
    return None


def _unclassified(df: pd.DataFrame) -> Optional[PatternResult]:
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    prior = df.iloc[-20:-1]
    hb = float(prior["high"].max())
    lb = float(prior["low"].min())
    close = float(last["close"])
    if close > hb and float(last["close"]) > float(last["open"]):
        return PatternResult("Pattern Breakout — Unclassified", "unclassified", "long",
                             Zone(low=lb, high=hb, kind="boundary",
                                  direction="bullish", at=df.index[-1],
                                  note="Unclassified compression"),
                             swing_high=hb, swing_low=lb,
                             confidence_cap=6, note="No classifier matched")
    if close < lb and float(last["close"]) < float(last["open"]):
        return PatternResult("Pattern Breakout — Unclassified", "unclassified", "short",
                             Zone(low=lb, high=hb, kind="boundary",
                                  direction="bearish", at=df.index[-1],
                                  note="Unclassified compression"),
                             swing_high=hb, swing_low=lb,
                             confidence_cap=6, note="No classifier matched")
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def detect_condition_b(df: pd.DataFrame) -> Optional[PatternResult]:
    """V7-007 + V7-003 sequence:
      1. Trap Detection runs FIRST on the latest candle.
         If 2+ trap signals: cap confidence at 5/10 and label POTENTIAL TRAP.
         Pattern classification still runs (so we can still emit a label),
         but the cap and label propagate to the alert.
      2. Sequential waterfall classification — first match wins:
            a. Reversal patterns (H&S, IH&S, Double T/B, Rounding)
            b. Continuation patterns (Flag, Pennant, Triangle, Rectangle, Channel)
            c. Broadening / Wedge
            d. Unclassified
    """
    if df is None or len(df) < 20:
        return None
    atr_series = atr(df, cfg.ATR_PERIOD)
    if len(atr_series) == 0:
        return None
    atr_now = float(atr_series.iloc[-2]) if len(atr_series) >= 2 else float(atr_series.iloc[-1])
    if atr_now <= 0:
        return None

    # V7-007: trap detection BEFORE classification. Boundary args unused so we
    # pass placeholders; the function only inspects the breakout candle itself.
    trap = detect_trap(df, 0.0, 0.0)

    # V7-003: sequential waterfall — reversal -> continuation -> broadening -> unclassified
    detectors = [
        # Reversal first
        lambda: _head_shoulders(df),
        lambda: _double_top_bottom(df),
        lambda: _rounding(df),
        # Continuation second
        lambda: _bull_bear_flag(df, atr_now),
        lambda: _pennant(df, atr_now),
        lambda: _triangle(df, atr_now),
        lambda: _rectangle(df, atr_now),
        lambda: _channel(df, atr_now),
        # Broadening / Wedge third
        lambda: _broadening(df, atr_now),
        lambda: _broadening_wedge(df, atr_now),
        # Unclassified last
        lambda: _unclassified(df),
    ]

    for fn in detectors:
        try:
            res = fn()
        except Exception:
            continue
        if not res:
            continue
        # Attach trap analysis (already computed before classification)
        res.trap = trap
        if trap.is_trap:
            cap = cfg.B_TRAP_SCORE_CAP
            res.confidence_cap = cap if res.confidence_cap is None else min(res.confidence_cap, cap)
            res.note += f" | TRAP WARNING ({', '.join(trap.flags)})"
        return res
    return None
