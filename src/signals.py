"""Signal engine — composes SMC primitives into high-probability setups.

A signal is only emitted when multiple conditions align:
  Required: a recent BOS or MSS on 1H or 4H (structure event)
  Plus at least one confluence from:
      - Unfilled FVG in the direction of the break
      - Order Block in the direction of the break
      - Equal highs/lows liquidity pool swept or nearby
      - Volume expansion on the breaking candle
  Plus funding-rate sanity check (extreme funding against direction weakens signal)

Each signal carries:
  - direction (long/short)
  - entry zone (retracement into OB/FVG)
  - invalidation level
  - reasoning bullets
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

import pandas as pd

from . import smc

log = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str                     # "long" | "short"
    setup_type: str                    # e.g. "BOS + OB retrace"
    entry_zone: tuple[float, float]
    invalidation: float
    price_now: float
    reasoning: List[str] = field(default_factory=list)
    funding_rate: Optional[float] = None
    news_context: Optional[str] = None
    generated_at: Optional[pd.Timestamp] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_zone"] = list(self.entry_zone)
        d["generated_at"] = self.generated_at.isoformat() if self.generated_at is not None else None
        return d

    def dedupe_key(self) -> str:
        return f"{self.symbol}|{self.timeframe}|{self.direction}|{self.setup_type}|{round(self.entry_zone[0], 4)}"


def analyze(symbol: str, timeframe: str, df: pd.DataFrame, funding: Optional[float]) -> Optional[Signal]:
    if df is None or len(df) < 60:
        return None

    swings = smc.swing_points(df)
    structure = smc.market_structure(swings)

    bos = smc.detect_bos(df, swings)
    mss = smc.detect_mss(df, swings)
    structure_event = mss or bos
    if not structure_event:
        return None

    direction = "long" if structure_event["direction"] == "bullish" else "short"

    fvgs = [f for f in smc.detect_fvg(df) if f["direction"] == structure_event["direction"]]
    obs = [o for o in smc.detect_order_blocks(df) if o["direction"] == structure_event["direction"]]
    eqs = smc.detect_equal_levels(swings)
    vol = smc.detect_volume_expansion(df)

    confluences: List[str] = []
    if fvgs:
        confluences.append("FVG")
    if obs:
        confluences.append("OB")
    if eqs:
        confluences.append("Liquidity pool (EQH/EQL)")
    if vol and vol["direction"] == structure_event["direction"]:
        confluences.append(f"Volume expansion x{vol['mult']}")

    if not confluences:
        return None

    # Entry zone: prefer nearest OB, then FVG, else break level itself
    entry_zone: tuple[float, float]
    invalidation: float
    last_close = float(df["close"].iloc[-1])
    if obs:
        ob = obs[-1]
        entry_zone = (ob["low"], ob["high"])
        invalidation = ob["low"] * 0.995 if direction == "long" else ob["high"] * 1.005
    elif fvgs:
        fvg = fvgs[-1]
        entry_zone = (fvg["low"], fvg["high"])
        invalidation = fvg["low"] * 0.995 if direction == "long" else fvg["high"] * 1.005
    else:
        lvl = structure_event["broken_level"]
        entry_zone = (lvl * 0.998, lvl * 1.002)
        invalidation = lvl * 0.99 if direction == "long" else lvl * 1.01

    reasoning: List[str] = []
    reasoning.append(
        f"Structure: {structure} on {timeframe}; {structure_event['type']} {structure_event['direction']} at {structure_event['broken_level']:.4f}"
    )
    if obs:
        ob = obs[-1]
        reasoning.append(f"Order Block zone {ob['low']:.4f}–{ob['high']:.4f}")
    if fvgs:
        fvg = fvgs[-1]
        reasoning.append(f"Unfilled FVG {fvg['low']:.4f}–{fvg['high']:.4f}")
    if eqs:
        last_eq = eqs[-1]
        reasoning.append(f"{last_eq['type']} liquidity near {last_eq['price']:.4f}")
    if vol:
        reasoning.append(f"Volume {vol['mult']}x the {20}-bar average")
    if funding is not None:
        fr_pct = funding * 100
        reasoning.append(f"Funding rate: {fr_pct:.4f}%")
        # Warn on extremes against direction
        if direction == "long" and funding > 0.0005:
            reasoning.append("⚠ Funding heavily positive — longs crowded; caution")
        if direction == "short" and funding < -0.0005:
            reasoning.append("⚠ Funding heavily negative — shorts crowded; caution")

    return Signal(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        setup_type=f"{structure_event['type']} + {' + '.join(confluences)}",
        entry_zone=entry_zone,
        invalidation=float(invalidation),
        price_now=last_close,
        reasoning=reasoning,
        funding_rate=funding,
        generated_at=df.index[-1],
    )
