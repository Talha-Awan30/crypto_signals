"""SL placement and geometry validator — v7.

LONG  setups: SL = zone_low - 0.5 * ATR. Must be numerically below entry zone min.
SHORT setups: SL = zone_high + 0.5 * ATR. Must be numerically above entry zone max.

If SL violates the directional rule, the signal is structurally invalid and
must be suppressed (logged as INVALID SL GEOMETRY).
"""
from __future__ import annotations

from . import config as cfg


def compute_sl(direction: str, zone_low: float, zone_high: float, atr_value: float) -> float:
    pad = cfg.SL_ATR_PADDING * atr_value
    if direction == "long":
        return zone_low - pad
    return zone_high + pad


def is_sl_valid(direction: str, sl: float, zone_low: float, zone_high: float) -> bool:
    """v7: SL must be on the right side of the entry zone."""
    if direction == "long":
        return sl < zone_low
    return sl > zone_high


def premium_discount(price: float, swing_high: float, swing_low: float) -> tuple[str, float]:
    """Return ('PREMIUM'|'DISCOUNT'|'EQUILIBRIUM', pct_from_eq)."""
    if swing_high <= swing_low:
        return "EQUILIBRIUM", 0.0
    eq = (swing_high + swing_low) / 2
    if eq <= 0:
        return "EQUILIBRIUM", 0.0
    pct = (price - eq) / eq * 100
    if pct > cfg.PREMIUM_DISCOUNT_NEUTRAL_BAND_PCT:
        return "PREMIUM", pct
    if pct < -cfg.PREMIUM_DISCOUNT_NEUTRAL_BAND_PCT:
        return "DISCOUNT", abs(pct)
    return "EQUILIBRIUM", 0.0
