"""Telegram Bot notifier."""
from __future__ import annotations

import logging

import requests

from ..config import CONFIG
from .base import Notifier

log = logging.getLogger(__name__)


class TelegramNotifier(Notifier):
    def send(self, title: str, body: str) -> bool:
        token = CONFIG.telegram_bot_token
        chat_id = CONFIG.telegram_chat_id
        if not token or not chat_id:
            log.error("Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID missing)")
            return False
        text = f"*{title}*\n\n{body}"
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
            r.raise_for_status()
            log.info("telegram sent: %s", title)
            return True
        except Exception as e:
            log.error("telegram send failed: %s", e)
            return False
