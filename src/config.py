"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


@dataclass
class Config:
    # Notifier
    notifier: str = os.getenv("NOTIFIER", "email").lower()

    # Email
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_to: str = os.getenv("EMAIL_TO", "")

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Scanner
    core_symbols: List[str] = field(
        default_factory=lambda: _split_csv(
            os.getenv(
                "CORE_SYMBOLS",
                "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT,XRP/USDT:USDT,ADA/USDT:USDT,ONDO/USDT:USDT",
            )
        )
    )
    broader_top_n: int = int(os.getenv("BROADER_TOP_N", "20"))
    exchange_id: str = os.getenv("EXCHANGE_ID", "kucoinfutures")

    # Run mode
    run_mode: str = os.getenv("RUN_MODE", "once").lower()
    loop_interval_min: int = int(os.getenv("LOOP_INTERVAL_MIN", "5"))

    # News
    cryptopanic_token: str = os.getenv("CRYPTOPANIC_TOKEN", "")

    # State
    state_path: str = os.getenv("STATE_PATH", "state/last_signals.json")


CONFIG = Config()
