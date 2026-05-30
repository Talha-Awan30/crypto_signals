"""Two-stage setup lifecycle — v7.

States per setup:
  ZONE_ALERTED       Stage 1 fired, awaiting price into retracement zone
  AWAITING_LTF       price inside zone, awaiting LTF validation on 30M/15M
  EXECUTED           Stage 2 fired
  EXPIRED            timed out or opposing structure formed
  INVALIDATED        SL hit before zone entry
  INVALID_SL_GEOM    SL geometry violates direction rule (v7)
  INCOMPLETE_NO_TARGETS  TP1/TP2 unidentifiable (v7)
  BELOW_THRESHOLD    score < 8 after all modifiers
  CONFLICTING_SIGNAL same-candle opposing direction
  DUPLICATE          Setup hash already active (v7 cooldown)

Persisted to JSON. Auto-migrates from v6 schema by clearing on first load.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from . import config as cfg

log = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = "v7.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Setup:
    id: str
    symbol: str
    base: str
    tier: int
    direction: str
    timeframe: str
    condition: str                       # "B" | "D" | "B+D"
    zone_low: float
    zone_high: float
    zone_kind: str
    sl: float
    key_level: float
    pattern_name: Optional[str]
    pattern_category: Optional[str]
    liquidity_event_kind: Optional[str]
    liquidity_event_price: Optional[float]
    pending_zone_levels: Optional[str]   # text summary of approaching EQH/EQL
    premium_discount_label: Optional[str]
    premium_discount_pct: Optional[float]
    market_regime: str
    btc_context: str
    confidence: int
    current_price: float
    exchange_id: str
    setup_hash: str
    candle_id: str
    consolidation_watch_flag: bool
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
    tp3: Optional[float] = None
    rr_to_tp1: Optional[float] = None
    rr_to_tp2: Optional[float] = None
    reason_structure: Optional[str] = None
    reason_zone: Optional[str] = None
    reason_execution: Optional[str] = None

    def in_zone(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high

    def sl_hit(self, candle_close: float) -> bool:
        if self.direction == "long":
            return candle_close < self.sl
        return candle_close > self.sl


class StateStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or cfg.RUNTIME.state_path
        self._data: Dict[str, dict] = {}
        self._meta: Dict[str, str] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            # Support both legacy (flat dict of setups) and new (meta + setups) formats
            if isinstance(payload, dict) and "_schema_version" in payload:
                self._meta = {"_schema_version": payload.get("_schema_version", "unknown")}
                self._data = {k: v for k, v in payload.items() if not k.startswith("_")}
            else:
                self._meta = {"_schema_version": "v6"}
                self._data = payload if isinstance(payload, dict) else {}

            if self._meta.get("_schema_version") != STATE_SCHEMA_VERSION:
                log.warning("state schema mismatch (%s != %s) — clearing for v7 migration",
                            self._meta.get("_schema_version"), STATE_SCHEMA_VERSION)
                self._data = {}
                self._meta = {"_schema_version": STATE_SCHEMA_VERSION}
                self._save()
        except Exception as e:
            log.warning("state load failed: %s — starting fresh", e)
            self._data = {}
            self._meta = {"_schema_version": STATE_SCHEMA_VERSION}

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        out = {"_schema_version": STATE_SCHEMA_VERSION, **self._data}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    def all_active(self) -> List[Setup]:
        out: List[Setup] = []
        for sid, d in self._data.items():
            if d.get("state") in ("ZONE_ALERTED", "AWAITING_LTF"):
                try:
                    out.append(Setup(**d))
                except TypeError:
                    # Skip records with missing keys (older partial saves)
                    continue
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


def compute_setup_hash(symbol: str, direction: str, key_level: float, timeframe: str) -> str:
    raw = f"{symbol}|{direction}|{round(float(key_level), 3)}|{timeframe}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def has_active_setup_hash(store: StateStore, setup_hash: str) -> bool:
    for s in store.all_active():
        if s.setup_hash == setup_hash:
            return True
    return False


def has_opposing_active_in_candle(store: StateStore, base: str, candle_id: str, direction: str) -> bool:
    for s in store.all_active():
        if s.base == base and s.candle_id == candle_id and s.direction != direction:
            return True
    for sid, d in store._data.items():
        if (d.get("base") == base
            and d.get("candle_id") == candle_id
            and d.get("direction") != direction):
            return True
    return False


def has_recent_same_direction_alert(store: StateStore, base: str, direction: str) -> bool:
    """v7: 4-hour cooldown between same-direction alerts on same asset.
    Opposite direction can fire immediately if independently triggered.
    """
    cutoff = datetime.now(timezone.utc) - pd.Timedelta(hours=cfg.COOLDOWN_HOURS_SAME_DIR)
    for sid, d in store._data.items():
        if d.get("base") != base or d.get("direction") != direction:
            continue
        # Only count actually delivered Stage 1 alerts (not below-threshold etc.)
        if d.get("state") in ("INVALID_SL_GEOM", "INCOMPLETE_NO_TARGETS",
                              "BELOW_THRESHOLD", "CONFLICTING_SIGNAL", "DUPLICATE"):
            continue
        ts_raw = d.get("created_at")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts > cutoff:
            return True
    return False


def tick_setup(setup: Setup, latest_close: float, opposing_structure: bool) -> str:
    if setup.sl_hit(latest_close):
        return "INVALIDATED"
    if opposing_structure:
        return "EXPIRED"

    setup.candles_elapsed += 1
    if setup.state == "ZONE_ALERTED":
        if setup.candles_elapsed >= cfg.TIMEOUT_HARD_MAX:
            return "EXPIRED"
        if setup.candles_elapsed >= cfg.TIMEOUT_CANDLES:
            if not setup.extended:
                setup.extended = True
                return "ZONE_ALERTED"
            return "EXPIRED"
        if setup.in_zone(latest_close):
            return "AWAITING_LTF"
    elif setup.state == "AWAITING_LTF":
        if setup.candles_elapsed >= cfg.TIMEOUT_HARD_MAX:
            return "EXPIRED"

    return setup.state
