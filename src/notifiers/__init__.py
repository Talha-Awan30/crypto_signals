"""Notifier factory — pick implementation from config."""
from __future__ import annotations

from ..config import CONFIG
from .base import Notifier
from .email_smtp import EmailNotifier
from .telegram import TelegramNotifier


def get_notifier() -> Notifier:
    if CONFIG.notifier == "telegram":
        return TelegramNotifier()
    return EmailNotifier()
