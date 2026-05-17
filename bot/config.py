"""Central configuration — universe, tiers, thresholds, scoring weights.

All thresholds come straight from Trading_Bot_Final_Prompt_v5.docx. Anything
labelled "TUNE" is a value we may adjust during the tuning phase; everything
else is locked to the spec.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Coin universe — tiered per v5 spec
# ---------------------------------------------------------------------------

TIER_1: List[str] = ["BTC", "ETH", "SOL", "BNB", "XRP"]
TIER_2: List[str] = ["ADA", "AVAX", "LINK", "DOT", "POL", "ATOM", "NEAR", "LTC"]  # MATIC -> POL
TIER_3: List[str] = ["ONDO", "INJ", "SUI", "SEI", "TIA", "AAVE", "UNI", "ARB", "TRX", "OP"]
# Tier 4 — Commodity-Linked Tokens (gold/silver/crude). Fully independent from
# crypto: no BTC alignment, exempt from ADX-based regime classification.
# XAUT (Tether Gold) is included as the gold proxy when an exchange lacks plain XAU.
TIER_4: List[str] = ["XAU", "XAUT", "XAG", "CL"]


def tier_of(base: str) -> int:
    if base in TIER_1:
        return 1
    if base in TIER_2:
        return 2
    if base in TIER_3:
        return 3
    if base in TIER_4:
        return 4
    return 0


def is_tier4(base: str) -> bool:
    return base in TIER_4


def all_symbols() -> List[str]:
    return TIER_1 + TIER_2 + TIER_3 + TIER_4


# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------

HTF_TIMEFRAMES: List[str] = ["1d", "4h"]   # setup detection
LTF_TIMEFRAMES: List[str] = ["1h", "15m"]  # retracement validation only


# ---------------------------------------------------------------------------
# Regime / volatility thresholds
# ---------------------------------------------------------------------------

ADX_PERIOD: int = 14
ADX_TRENDING: float = 25.0
ADX_RANGING: float = 20.0
ATR_PERIOD: int = 14
ATR_LOOKBACK_DAYS: int = 20

# Volatility regime filter — suppress all alerts if Daily ATR > 2.5x 20-day avg
VOL_SUPPRESS_ATR_MULT: float = 2.5


# ---------------------------------------------------------------------------
# Condition thresholds (v5 spec verbatim)
# ---------------------------------------------------------------------------

# Condition A — HTF Key Level Reaction
A_LEVEL_TOLERANCE_PCT: float = 0.005          # 0.5%
A_LEVEL_MIN_REACTIONS: int = 2                # 2 prior reactions

# Condition C — HTF Market Structure Shift
C_DISPLACEMENT_LOOKBACK: int = 5              # body > prior 5-candle average
C_ATR_DISPLACEMENT_MULT: float = 1.0          # min 1 ATR expansion through level

# Condition D — Liquidity tolerance for EQH/EQL clustering
D_EQ_TOLERANCE_PCT: float = 0.0015            # 0.15% — tight cluster
D_SWEEP_ACCEPT_CANDLES: int = 2               # min 2 candles to hold beyond level

# Condition B — Pattern detection (see bot/conditions/b_patterns/*.py)
B_FLAG_IMPULSE_ATR_MULT: float = 3.0
B_FLAG_MIN_CANDLES: int = 3
B_FLAG_MAX_CANDLES: int = 10
B_PENNANT_MIN_CANDLES: int = 4
B_RECTANGLE_HORIZ_TOL: float = 0.01           # 1% slope tolerance
B_RECTANGLE_MIN_TOUCHES: int = 4              # 2 highs + 2 lows
B_TRIANGLE_FLAT_TOUCHES: int = 3
B_TRIANGLE_CONV_TOUCHES: int = 2
B_BROADENING_MIN_CANDLES: int = 4
B_DOUBLE_TOL_PCT: float = 0.005               # 0.5% between peaks/troughs
B_DOUBLE_MIN_SEPARATION: int = 5
B_HS_SHOULDER_TOL_PCT: float = 0.01           # 1% between shoulders
B_ROUNDING_MIN_CANDLES: int = 8


# ---------------------------------------------------------------------------
# Scoring & delivery
# ---------------------------------------------------------------------------

MIN_SCORE_DELIVER: int = 7   # relaxed from spec's 8 to hit 0–5 alerts/day target
PRIORITY_SCORE: int = 8      # spec's institutional threshold


# ---------------------------------------------------------------------------
# Cooldown / timeout
# ---------------------------------------------------------------------------

COOLDOWN_HOURS: int = 48
TIMEOUT_CANDLES: int = 5            # cancel if no zone entry within 5 candles
TIMEOUT_EXTEND: int = 2             # +2 if compressing near zone
TIMEOUT_HARD_MAX: int = 7           # absolute kill


# ---------------------------------------------------------------------------
# BTC correlation — required for Tier 1/2
# ---------------------------------------------------------------------------

BTC_SYMBOL: str = "BTC/USDT:USDT"


# ---------------------------------------------------------------------------
# Runtime / IO
# ---------------------------------------------------------------------------

@dataclass
class Runtime:
    # Exchange preference (auto-fallback at startup)
    exchange_preference: List[str] = field(
        default_factory=lambda: _csv(os.getenv(
            "EXCHANGE_PREFERENCE",
            "binanceusdm,okx,kucoinfutures,bitget",
        ))
    )

    # Email
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_to: str = os.getenv("EMAIL_TO", "")

    # Run mode
    run_mode: str = os.getenv("RUN_MODE", "loop").lower()   # loop | once
    loop_interval_sec: int = int(os.getenv("LOOP_INTERVAL_SEC", "60"))  # 1-min poll on Oracle

    # State file
    state_path: str = os.getenv("STATE_PATH", "state/v5_state.json")


def _csv(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


RUNTIME = Runtime()
