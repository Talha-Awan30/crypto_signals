"""Per-watch dedupe registry — prevents WATCH emails from re-firing every cycle.

Each watch type (liquidity_approach, consolidation) gets its own suppression
window. Once an alert fires for a given key, it cannot re-fire until the
window expires.

Key format:
  liquidity_approach: f"{asset}|{side}|{round(level, 3)}"
  consolidation:      f"{asset}|consolidation"
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from . import config as cfg

log = logging.getLogger(__name__)

# Suppression windows
LIQUIDITY_APPROACH_WINDOW_HOURS = 4
CONSOLIDATION_WINDOW_HOURS = 24


def _path() -> str:
    return cfg.RUNTIME.state_path.replace("v7_state.json", "v7_watch_dedupe.json")


def _load() -> Dict[str, str]:
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: Dict[str, str]) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _prune(data: Dict[str, str]) -> Dict[str, str]:
    """Remove entries older than 48h. Robust to malformed values."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    out = {}
    for k, v in data.items():
        if not isinstance(v, str):
            continue
        try:
            if datetime.fromisoformat(v) > cutoff:
                out[k] = v
        except (ValueError, TypeError):
            continue
    return out


def should_fire(key: str, window_hours: int) -> bool:
    data = _load()
    last = data.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt > timedelta(hours=window_hours)


def mark_fired(key: str) -> None:
    data = _load()
    data[key] = datetime.now(timezone.utc).isoformat()
    data = _prune(data)
    _save(data)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def liquidity_approach_key(asset: str, side: str, level: float) -> str:
    return f"{asset}|{side}|{round(float(level), 3)}"


def liquidity_approach_should_fire(asset: str, side: str, level: float) -> bool:
    return should_fire(liquidity_approach_key(asset, side, level),
                       LIQUIDITY_APPROACH_WINDOW_HOURS)


def liquidity_approach_mark(asset: str, side: str, level: float) -> None:
    mark_fired(liquidity_approach_key(asset, side, level))


def consolidation_key(asset: str) -> str:
    return f"{asset}|consolidation"


def consolidation_should_fire(asset: str) -> bool:
    return should_fire(consolidation_key(asset), CONSOLIDATION_WINDOW_HOURS)


def consolidation_mark(asset: str) -> None:
    mark_fired(consolidation_key(asset))
