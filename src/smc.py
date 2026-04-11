"""Smart Money Concepts detection primitives.

This module is intentionally pragmatic, not academic. All detectors operate on
a pandas OHLCV DataFrame (UTC index) and return simple dicts that the signal
engine can compose into human-readable reasoning.

Detectors implemented:
  - swing_points: fractal-style swing highs/lows
  - market_structure: determines current structure (HH/HL vs LH/LL)
  - detect_bos: Break of Structure (last broken swing in trend direction)
  - detect_mss: Market Structure Shift (first break against prior trend)
  - detect_fvg: Fair Value Gap (3-candle imbalance)
  - detect_order_blocks: last opposing candle before an impulsive move
  - detect_equal_levels: equal highs / equal lows clusters (liquidity pools)
  - detect_volume_expansion: rolling volume breakout
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Swing points
# ---------------------------------------------------------------------------

@dataclass
class Swing:
    idx: int           # positional index in the dataframe
    ts: pd.Timestamp
    price: float
    kind: str          # "high" or "low"


def swing_points(df: pd.DataFrame, left: int = 2, right: int = 2) -> List[Swing]:
    """Fractal swing points: a candle whose high (low) exceeds `left`/`right` neighbors."""
    highs = df["high"].values
    lows = df["low"].values
    out: List[Swing] = []
    n = len(df)
    for i in range(left, n - right):
        window_h = highs[i - left : i + right + 1]
        window_l = lows[i - left : i + right + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            out.append(Swing(i, df.index[i], float(highs[i]), "high"))
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            out.append(Swing(i, df.index[i], float(lows[i]), "low"))
    out.sort(key=lambda s: s.idx)
    return out


# ---------------------------------------------------------------------------
# Market structure
# ---------------------------------------------------------------------------

def market_structure(swings: List[Swing]) -> str:
    """Classify recent structure as 'bullish', 'bearish', or 'ranging'."""
    highs = [s for s in swings if s.kind == "high"][-3:]
    lows = [s for s in swings if s.kind == "low"][-3:]
    if len(highs) < 2 or len(lows) < 2:
        return "ranging"
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


# ---------------------------------------------------------------------------
# BOS / MSS
# ---------------------------------------------------------------------------

def detect_bos(df: pd.DataFrame, swings: List[Swing]) -> Optional[dict]:
    """Break of Structure — last close that took out a prior swing in trend direction."""
    if len(swings) < 4:
        return None
    structure = market_structure(swings)
    last_close = float(df["close"].iloc[-1])
    if structure == "bullish":
        prior_high = [s for s in swings[:-1] if s.kind == "high"]
        if prior_high and last_close > prior_high[-1].price:
            return {
                "type": "BOS",
                "direction": "bullish",
                "broken_level": prior_high[-1].price,
                "at": df.index[-1],
            }
    if structure == "bearish":
        prior_low = [s for s in swings[:-1] if s.kind == "low"]
        if prior_low and last_close < prior_low[-1].price:
            return {
                "type": "BOS",
                "direction": "bearish",
                "broken_level": prior_low[-1].price,
                "at": df.index[-1],
            }
    return None


def detect_mss(df: pd.DataFrame, swings: List[Swing]) -> Optional[dict]:
    """Market Structure Shift — first break against the prior dominant trend."""
    if len(swings) < 5:
        return None
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    prior_trend_bullish = highs[-2].price > highs[-3].price if len(highs) >= 3 else False
    prior_trend_bearish = lows[-2].price < lows[-3].price if len(lows) >= 3 else False
    last_close = float(df["close"].iloc[-1])

    if prior_trend_bullish and last_close < lows[-1].price:
        return {
            "type": "MSS",
            "direction": "bearish",
            "broken_level": lows[-1].price,
            "at": df.index[-1],
        }
    if prior_trend_bearish and last_close > highs[-1].price:
        return {
            "type": "MSS",
            "direction": "bullish",
            "broken_level": highs[-1].price,
            "at": df.index[-1],
        }
    return None


# ---------------------------------------------------------------------------
# FVG (3-candle imbalance)
# ---------------------------------------------------------------------------

def detect_fvg(df: pd.DataFrame, lookback: int = 30) -> List[dict]:
    """Find unfilled Fair Value Gaps within the last `lookback` candles."""
    out: List[dict] = []
    n = len(df)
    start = max(2, n - lookback)
    highs = df["high"].values
    lows = df["low"].values
    for i in range(start, n):
        # Bullish FVG: low[i] > high[i-2]
        if lows[i] > highs[i - 2]:
            gap_low, gap_high = float(highs[i - 2]), float(lows[i])
            # unfilled if subsequent lows never re-entered
            filled = any(lows[j] <= gap_high and highs[j] >= gap_low for j in range(i + 1, n))
            if not filled:
                out.append({
                    "type": "FVG",
                    "direction": "bullish",
                    "low": gap_low,
                    "high": gap_high,
                    "at": df.index[i],
                })
        # Bearish FVG: high[i] < low[i-2]
        if highs[i] < lows[i - 2]:
            gap_low, gap_high = float(highs[i]), float(lows[i - 2])
            filled = any(lows[j] <= gap_high and highs[j] >= gap_low for j in range(i + 1, n))
            if not filled:
                out.append({
                    "type": "FVG",
                    "direction": "bearish",
                    "low": gap_low,
                    "high": gap_high,
                    "at": df.index[i],
                })
    return out


# ---------------------------------------------------------------------------
# Order Blocks
# ---------------------------------------------------------------------------

def detect_order_blocks(df: pd.DataFrame, lookback: int = 50, impulse_mult: float = 1.5) -> List[dict]:
    """Last opposing candle before an impulsive move.

    Bullish OB: last bearish candle before an impulsive up move (> `impulse_mult` * ATR).
    Bearish OB: last bullish candle before an impulsive down move.
    """
    out: List[dict] = []
    n = len(df)
    if n < 20:
        return out
    tr = (df["high"] - df["low"]).rolling(14).mean()
    atr = tr.fillna(method="bfill").values
    o = df["open"].values
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    start = max(1, n - lookback)
    for i in range(start, n - 1):
        move = c[i + 1] - o[i + 1]
        if atr[i] == 0:
            continue
        if move > impulse_mult * atr[i] and c[i] < o[i]:
            out.append({
                "type": "OB",
                "direction": "bullish",
                "low": float(l[i]),
                "high": float(h[i]),
                "at": df.index[i],
            })
        elif move < -impulse_mult * atr[i] and c[i] > o[i]:
            out.append({
                "type": "OB",
                "direction": "bearish",
                "low": float(l[i]),
                "high": float(h[i]),
                "at": df.index[i],
            })
    return out


# ---------------------------------------------------------------------------
# Equal highs / lows (liquidity pools)
# ---------------------------------------------------------------------------

def detect_equal_levels(swings: List[Swing], tolerance_pct: float = 0.0015) -> List[dict]:
    """Cluster swing highs / lows that sit within `tolerance_pct` of each other."""
    out: List[dict] = []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    def cluster(points: List[Swing], kind: str) -> None:
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                a, b = points[i].price, points[j].price
                if abs(a - b) / max(a, b) <= tolerance_pct:
                    out.append({
                        "type": "EQH" if kind == "high" else "EQL",
                        "direction": "bearish" if kind == "high" else "bullish",
                        "price": (a + b) / 2,
                        "at": points[j].ts,
                        "note": "liquidity pool / resting stops",
                    })
                    break

    cluster(highs, "high")
    cluster(lows, "low")
    return out


# ---------------------------------------------------------------------------
# Volume expansion
# ---------------------------------------------------------------------------

def detect_volume_expansion(df: pd.DataFrame, window: int = 20, mult: float = 1.8) -> Optional[dict]:
    if len(df) < window + 2:
        return None
    vol = df["volume"]
    avg = vol.rolling(window).mean().iloc[-2]
    last = float(vol.iloc[-1])
    if avg and last > mult * avg:
        direction = "bullish" if df["close"].iloc[-1] > df["open"].iloc[-1] else "bearish"
        return {
            "type": "VOL_EXPANSION",
            "direction": direction,
            "last": last,
            "avg": float(avg),
            "mult": round(last / float(avg), 2),
            "at": df.index[-1],
        }
    return None
