"""Two-stage setup state machine.

States per setup:
  ZONE_ALERTED      Stage 1 fired, awaiting price to enter zone
  AWAITING_LTF      price has entered zone, awaiting LTF validation
  EXECUTED          Stage 2 fired
  EXPIRED           timed out or opposing structure formed
  INVALIDATED       SL hit before zone entry

Persisted to disk as JSON so restarts don't lose context.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from . import config as cfg
from .conditions.zones import Zone

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Setup:
    id: str
    symbol: str
    base: str
    tier: int
    direction: str             # "long" | "short"
    timeframe: str             # "1d" | "4h"
    conditions_fired: List[str]
    zone_low: float
    zone_high: float
    zone_kind: str
    invalidation: float
    key_level: float
    pattern_name: Optional[str]
    pattern_category: Optional[str]
    liquidity_note: str
    btc_context: str
    market_regime: str
    confidence: int
    created_at: str
    state: str = "ZONE_ALERTED"
    last_update: str = field(default_factory=_now)
    candles_elapsed: int = 0
    extended: bool = False
    stage2_fired_at: Optional[str] = None
    ltf_trigger: Optional[str] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    rr_to_tp2: Optional[float] = None

    def in_zone(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high

    def sl_hit(self, candle_close: float) -> bool:
        if self.direction == "long":
            return candle_close < self.invalidation
        return candle_close > self.invalidation


class StateStore:
    def __init__(self, path: str = None):
        self.path = path or cfg.RUNTIME.state_path
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception as e:
            log.warning("state load failed: %s", e)
            self._data = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    # CRUD ----
    def all_active(self) -> List[Setup]:
        out = []
        for sid, d in self._data.items():
            if d.get("state") in ("ZONE_ALERTED", "AWAITING_LTF"):
                out.append(Setup(**d))
        return out

    def for_asset(self, base: str) -> List[Setup]:
        return [s for s in self.all_active() if s.base == base]

    def add(self, setup: Setup):
        self._data[setup.id] = asdict(setup)
        self._save()

    def update(self, setup: Setup):
        setup.last_update = _now()
        self._data[setup.id] = asdict(setup)
        self._save()

    def archive(self, setup: Setup, final_state: str):
        setup.state = final_state
        setup.last_update = _now()
        self._data[setup.id] = asdict(setup)
        self._save()


def new_setup_id() -> str:
    return uuid.uuid4().hex[:12]


def tick_setup(setup: Setup, latest_close: float, opposing_structure: bool) -> str:
    """Advance the state of a setup given the latest candle on its TF.

    Returns the new state.
    """
    # Hard invalidation — SL hit
    if setup.sl_hit(latest_close):
        return "INVALIDATED"

    # Opposing HTF structure formed
    if opposing_structure:
        return "EXPIRED"

    # Timeout enforcement
    setup.candles_elapsed += 1
    if setup.state == "ZONE_ALERTED":
        # Price not yet in zone — check timeout
        if setup.candles_elapsed >= cfg.TIMEOUT_HARD_MAX:
            return "EXPIRED"
        if setup.candles_elapsed >= cfg.TIMEOUT_CANDLES:
            if not setup.extended:
                # Compressing near zone? — we approximate by allowing one extension.
                setup.extended = True
                return "ZONE_ALERTED"
            return "EXPIRED"
        if setup.in_zone(latest_close):
            return "AWAITING_LTF"
    elif setup.state == "AWAITING_LTF":
        # Stay until LTF confirms or hard timeout
        if setup.candles_elapsed >= cfg.TIMEOUT_HARD_MAX:
            return "EXPIRED"

    return setup.state
