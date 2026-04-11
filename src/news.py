"""Lightweight news/sentiment via CryptoPanic public feed.

We use the free public endpoint; if a token is configured we pass it for
higher limits. Sentiment is derived from the `votes` object when present.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import requests

from .config import CONFIG

log = logging.getLogger(__name__)

_BASE = "https://cryptopanic.com/api/v1/posts/"


def fetch_news(currency_code: str, limit: int = 5) -> List[dict]:
    params = {"currencies": currency_code, "public": "true", "kind": "news"}
    if CONFIG.cryptopanic_token:
        params["auth_token"] = CONFIG.cryptopanic_token
    try:
        r = requests.get(_BASE, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("results", [])
        return data[:limit]
    except Exception as e:
        log.debug("news fetch failed for %s: %s", currency_code, e)
        return []


def summarize(posts: List[dict]) -> Optional[str]:
    if not posts:
        return None
    lines: List[str] = []
    for p in posts[:3]:
        title = p.get("title", "")
        votes = p.get("votes") or {}
        tone_parts = []
        if votes.get("positive"):
            tone_parts.append(f"+{votes['positive']}")
        if votes.get("negative"):
            tone_parts.append(f"-{votes['negative']}")
        tone = f" ({', '.join(tone_parts)})" if tone_parts else ""
        lines.append(f"• {title}{tone}")
    return "\n".join(lines)
