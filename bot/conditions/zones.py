"""Retracement-zone primitives shared across conditions.

Each Condition outputs a candidate Zone (price range) plus metadata identifying
the zone type (FVG / OB / imbalance / boundary / neckline). The state machine
later validates LTF entry inside the Zone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import pandas as pd


ZoneKind = Literal["FVG", "OB", "imbalance", "boundary", "neckline", "key_level", "liquidity"]


@dataclass
class Zone:
    low: float
    high: float
    kind: ZoneKind
    direction: Literal["bullish", "bearish"]
    at: Optional[pd.Timestamp] = None
    note: str = ""

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high

    def width(self) -> float:
        return self.high - self.low
