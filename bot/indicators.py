"""Technical indicators — ADX, ATR, swing pivots, structure tracking.

Hand-rolled (no ta-lib dependency) for portability. Vectorized pandas/numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_close = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_close).abs(), (l - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder smoothing == EMA with alpha = 1/period
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder)."""
    h, l, c = df["high"], df["low"], df["close"]
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr_val = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_val
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_val
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Swing pivots
# ---------------------------------------------------------------------------

@dataclass
class Pivot:
    idx: int
    ts: pd.Timestamp
    price: float
    kind: str  # "high" | "low"


def swing_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> List[Pivot]:
    """Fractal pivots. `left`/`right` = number of neighbour candles each side."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    out: List[Pivot] = []
    for i in range(left, n - right):
        wh = highs[i - left : i + right + 1]
        wl = lows[i - left : i + right + 1]
        if highs[i] == wh.max() and (wh == highs[i]).sum() == 1:
            out.append(Pivot(i, df.index[i], float(highs[i]), "high"))
        if lows[i] == wl.min() and (wl == lows[i]).sum() == 1:
            out.append(Pivot(i, df.index[i], float(lows[i]), "low"))
    out.sort(key=lambda p: p.idx)
    return out


def last_n_swings(pivots: List[Pivot], kind: str, n: int) -> List[Pivot]:
    return [p for p in pivots if p.kind == kind][-n:]


# ---------------------------------------------------------------------------
# Candle body / displacement helpers
# ---------------------------------------------------------------------------

def body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def avg_body(df: pd.DataFrame, lookback: int = 5) -> float:
    """Mean of last `lookback` candle bodies (excluding the last)."""
    if len(df) < lookback + 1:
        return 0.0
    bodies = (df["close"].astype(float) - df["open"].astype(float)).abs()
    return float(bodies.iloc[-(lookback + 1) : -1].mean())


def is_displacement(row: pd.Series, avg_body_val: float, atr_val: float, atr_mult: float = 1.0) -> bool:
    """True if candle body > 5-bar avg AND range > 1 ATR."""
    if avg_body_val <= 0 or atr_val <= 0:
        return False
    return body(row) > avg_body_val and (float(row["high"]) - float(row["low"])) > atr_mult * atr_val
