"""BTC structural alignment — required for Tier 1/2, optional for Tier 3.

v5 rule:
  Tier 1/2 alerts require BTC's 4H/Daily structure to align with the trade
  direction (long needs BTC bullish, short needs BTC bearish).
  Tier 3 may bypass IF asset shows materially stronger relative strength.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from . import config as cfg
from .indicators import last_n_swings, swing_pivots


@dataclass
class BTCContext:
    htf_bias: Literal["bullish", "bearish", "ranging"]
    summary: str  # one-line for the alert output


def classify_btc_bias(btc_4h: pd.DataFrame, btc_1d: pd.DataFrame) -> BTCContext:
    """Classify BTC bias by combining 4H and Daily HH/HL vs LH/LL."""
    bias_4h = _bias_from_swings(btc_4h)
    bias_1d = _bias_from_swings(btc_1d)

    if bias_4h == bias_1d and bias_4h != "ranging":
        bias = bias_4h
        summary = f"BTC {bias} on both Daily and 4H, aligned"
    elif "ranging" in (bias_4h, bias_1d):
        bias = bias_4h if bias_1d == "ranging" else bias_1d
        summary = f"BTC mixed (4H {bias_4h} / Daily {bias_1d}), partial alignment"
    else:
        # opposing
        bias = "ranging"
        summary = f"BTC conflicting (4H {bias_4h} / Daily {bias_1d}), no clear bias"

    return BTCContext(htf_bias=bias, summary=summary)  # type: ignore[arg-type]


def _bias_from_swings(df: pd.DataFrame) -> str:
    pivots = swing_pivots(df)
    hs = last_n_swings(pivots, "high", 2)
    ls = last_n_swings(pivots, "low", 2)
    if len(hs) < 2 or len(ls) < 2:
        return "ranging"
    if hs[-1].price > hs[-2].price and ls[-1].price > ls[-2].price:
        return "bullish"
    if hs[-1].price < hs[-2].price and ls[-1].price < ls[-2].price:
        return "bearish"
    return "ranging"


def relative_strength(asset_df: pd.DataFrame, btc_df: pd.DataFrame, lookback: int = 20) -> float:
    """Asset return - BTC return over `lookback` bars. Positive = stronger than BTC."""
    if len(asset_df) < lookback + 1 or len(btc_df) < lookback + 1:
        return 0.0
    a_ret = float(asset_df["close"].iloc[-1] / asset_df["close"].iloc[-lookback] - 1)
    b_ret = float(btc_df["close"].iloc[-1] / btc_df["close"].iloc[-lookback] - 1)
    return a_ret - b_ret


def direction_allowed(
    tier: int,
    direction: str,
    btc: BTCContext,
    asset_4h: pd.DataFrame,
    btc_4h: pd.DataFrame,
) -> tuple[bool, str]:
    """Gate per v5 tier rules. Returns (allowed, reason)."""
    if tier in (1, 2):
        want = "bullish" if direction == "long" else "bearish"
        if btc.htf_bias != want:
            return False, f"BTC bias {btc.htf_bias} blocks {direction} on Tier {tier} asset"
        return True, btc.summary

    if tier == 3:
        # base allow; relative-strength bonus is informational
        rs = relative_strength(asset_4h, btc_4h)
        if rs > 0.05:
            return True, f"Tier 3 / strong RS +{rs:.1%} vs BTC; BTC alignment not required"
        return True, f"Tier 3 / RS {rs:+.1%} vs BTC; alignment not required"

    if tier == 4:
        # Commodity-linked — fully independent. No BTC alignment required or applied.
        return True, "Tier 4 commodity asset — independent of crypto regime; macro/commodity drivers govern"

    if tier == 5:
        # Dynamic volume-filtered universe. No BTC alignment required (BOT-011).
        # Score is capped at 7 elsewhere unless A or C also fires.
        rs = relative_strength(asset_4h, btc_4h)
        return True, f"Tier 5 / dynamic volume-filtered; RS {rs:+.1%} vs BTC"

    return False, "unknown tier"
