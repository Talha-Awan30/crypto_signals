"""Cooldown registry — 48h per-asset gate plus invalidation/reset bypass.

v5 rule:
  Minimum 48-hour cooldown between alerts on the same asset UNLESS ALL of:
    1. Full invalidation of prior setup
    2. Structural reset on 4H or Daily
    3. New HTF confirmation forms independently

This module only enforces (1)-style. The state machine handles (1) & (3);
this registry just tracks "last alert time per asset".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from . import config as cfg


def _path() -> str:
    return cfg.RUNTIME.state_path.replace("v5_state.json", "v5_cooldown.json")


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


def in_cooldown(asset: str) -> bool:
    data = _load()
    last = data.get(asset)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_dt < timedelta(hours=cfg.COOLDOWN_HOURS)


def mark_alert(asset: str) -> None:
    data = _load()
    data[asset] = datetime.now(timezone.utc).isoformat()
    _save(data)


def reset(asset: str) -> None:
    """Called when a setup is fully invalidated and structurally reset."""
    data = _load()
    data.pop(asset, None)
    _save(data)
