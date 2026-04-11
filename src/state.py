"""Simple JSON-backed dedupe state.

Stores {dedupe_key: iso_timestamp} so the same setup isn't re-sent within
the suppression window.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from .config import CONFIG

log = logging.getLogger(__name__)

_DEFAULT_SUPPRESS_HOURS = 6


def _load() -> Dict[str, str]:
    path = CONFIG.state_path
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("could not read state file: %s", e)
        return {}


def _save(data: Dict[str, str]) -> None:
    path = CONFIG.state_path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def should_send(key: str, suppress_hours: int = _DEFAULT_SUPPRESS_HOURS) -> bool:
    data = _load()
    last = data.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt > timedelta(hours=suppress_hours)


def mark_sent(key: str) -> None:
    data = _load()
    data[key] = datetime.now(timezone.utc).isoformat()
    # prune old keys (>7 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    pruned = {}
    for k, v in data.items():
        try:
            if datetime.fromisoformat(v) > cutoff:
                pruned[k] = v
        except ValueError:
            continue
    _save(pruned)
