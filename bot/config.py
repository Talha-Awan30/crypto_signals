"""Central configuration — v7 spec (B and D independent triggers).

All thresholds are extracted directly from Trading_Bot_Prompt_v7.docx.
Anything noted as TUNE may be adjusted during the tuning phase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Coin universe — tiered per v7 spec
# ---------------------------------------------------------------------------

TIER_1: List[str] = ["BTC", "ETH", "SOL", "BNB", "XRP"]
TIER_2: List[str] = ["ADA", "AVAX", "LINK", "DOT", "POL", "ATOM", "NEAR", "LTC", "DASH"]  # MATIC -> POL
TIER_3: List[str] = ["ONDO", "INJ", "SUI", "SEI", "TIA", "AAVE", "UNI", "ARB", "TRX", "OP"]
TIER_4: List[str] = ["XAU", "XAUT", "XAG", "CL"]


def tier_of(base: str, tier5_set: set | None = None) -> int:
    if base in TIER_1:
        return 1
    if base in TIER_2:
        return 2
    if base in TIER_3:
        return 3
    if base in TIER_4:
        return 4
    if tier5_set and base in tier5_set:
        return 5
    return 0


def is_tier4(base: str) -> bool:
    return base in TIER_4


def all_symbols() -> List[str]:
    return TIER_1 + TIER_2 + TIER_3 + TIER_4


# ---------------------------------------------------------------------------
# Timeframes — v7
# ---------------------------------------------------------------------------

# Setup detection / Stage 1 triggers fire on these (1H weight -1, 2H/4H standard, 1D +2)
HTF_TIMEFRAMES: List[str] = ["1d", "4h", "2h", "1h"]
# Stage 2 LTF validation only
LTF_TIMEFRAMES: List[str] = ["30m", "15m"]


# Timeframe weight in scoring (v7 SIGNAL QUALITY SCORING)
TF_WEIGHT: Dict[str, int] = {"1d": 2, "4h": 0, "2h": 0, "1h": -1}


# ---------------------------------------------------------------------------
# Regime / volatility thresholds
# ---------------------------------------------------------------------------

ADX_PERIOD: int = 14
ADX_TRENDING: float = 25.0
ADX_RANGING: float = 20.0
ADX_COMPRESSION: float = 15.0          # v7 Compression / Pre-Expansion regime
ATR_PERIOD: int = 14
ATR_LOOKBACK_DAYS: int = 20

VOL_SUPPRESS_ATR_MULT: float = 2.5
NEWS_CANDLE_ATR_MULT: float = 3.0      # v7: single candle exceeding 3x ATR = news candle


# ---------------------------------------------------------------------------
# Condition B (v7) — pattern thresholds
# ---------------------------------------------------------------------------

B_FLAG_IMPULSE_ATR_MULT: float = 3.0
B_FLAG_MIN_CANDLES: int = 3
B_FLAG_MAX_CANDLES: int = 12          # v7: 3-12 (was 10 in v5)
B_FLAG_CHANNEL_MAX_ANGLE_DEG: float = 45.0

B_PENNANT_MIN_CANDLES: int = 4
B_PENNANT_CONTRACTION_PCT: float = 0.20  # 20% contraction from start to breakout

B_RECTANGLE_BOUNDARY_DEV_ATR: float = 0.25  # within 0.25x ATR
B_RECTANGLE_MIN_TOUCHES: int = 4

B_TRIANGLE_FLAT_TOUCHES: int = 3
B_TRIANGLE_CONV_TOUCHES: int = 2
B_TRIANGLE_MIN_STEP_ATR: float = 0.15   # each new HL/LH must exceed prior by 0.15x ATR

B_CHANNEL_MIN_TOUCHES: int = 4
B_CHANNEL_WIDTH_DEV_ATR: float = 0.5    # channel width must remain within 0.5x ATR

B_BROADENING_MIN_CANDLES: int = 5
B_BROADENING_EXPANSION_STEP_ATR: float = 0.25

B_WEDGE_MIN_CANDLES: int = 5
B_WEDGE_EXPANSION_STEP_ATR: float = 0.25

B_DOUBLE_TOL_PCT: float = 0.005
B_DOUBLE_MIN_SEPARATION: int = 5
B_HS_SHOULDER_TOL_PCT: float = 0.01
B_ROUNDING_MIN_CANDLES: int = 8
B_ROUNDING_MAX_COUNTERSWING_PCT: float = 0.50  # no counter-swing > 50% of arc depth


# Trap detection (v7)
B_TRAP_BODY_MIN_RATIO: float = 0.50            # body < 50% of total range = wick-heavy
B_TRAP_REENTRY_CANDLES: int = 2                # reclose inside boundary within 2 candles
B_TRAP_LATE_UTC_HOURS: range = range(23, 24)   # 23:00-23:30 UTC checked specially
B_TRAP_SCORE_CAP: int = 5                      # cap at 5/10 if 2+ trap signals


# Premium/Discount equilibrium
PREMIUM_DISCOUNT_NEUTRAL_BAND_PCT: float = 0.0  # treat >0% above EQ as premium, <0% as discount


# ---------------------------------------------------------------------------
# Condition D (v7) — liquidity thresholds
# ---------------------------------------------------------------------------

D_EQ_TOLERANCE_PCT: float = 0.003          # v7: EQH/EQL within 0.3%
D_SWEEP_ACCEPT_CANDLES: int = 2            # 2 closed candles beyond level for acceptance
D_EXT_SWEEP_VOL_MULT: float = 1.2          # volume expansion threshold
D_MIN_SWING_SEPARATION: int = 5            # HTF swing high/low needs 5-candle separation
D_PENDING_ZONE_APPROACH_PCT: float = 0.01  # 1% — Liquidity Approach alert range
D_FVG_EXCESSIVE_ATR_MULT: float = 1.5      # retracement zone > 1.5x ATR = excessive imbalance


# ---------------------------------------------------------------------------
# SL geometry — v7
# ---------------------------------------------------------------------------

SL_ATR_PADDING: float = 0.5   # SL = zone boundary ± 0.5 ATR on setup TF


# ---------------------------------------------------------------------------
# LTF validation thresholds — v7 Step 4 strict math
# ---------------------------------------------------------------------------

LTF_MSS_BREAK_ATR_MULT: float = 0.15        # close beyond pivot by >= 0.15x LTF ATR
LTF_DISP_BODY_MULT: float = 1.5             # body > 1.5x prior-5 avg
LTF_DISP_VOL_MULT: float = 1.2              # volume > 20-period MA by 20%+
LTF_REJECTION_WICK_BODY_RATIO: float = 1.5
LTF_REJECTION_CLOSE_PCTILE: float = 0.25    # top/bottom 25% of range
LTF_REJECTION_MIN_RANGE_ATR: float = 0.75


# ---------------------------------------------------------------------------
# Scoring & delivery — v7
# ---------------------------------------------------------------------------

MIN_SCORE_DELIVER: int = 8
PRIORITY_SCORE: int = 8
SCORE_BASE: int = 5  # neutral starting score before modifiers


# ---------------------------------------------------------------------------
# Cooldown / timeout — v7
# ---------------------------------------------------------------------------

COOLDOWN_HOURS_SAME_DIR: int = 4   # v7: 4h between same-direction alerts on same asset
TIMEOUT_CANDLES: int = 5
TIMEOUT_EXTEND: int = 2
TIMEOUT_HARD_MAX: int = 7


# ---------------------------------------------------------------------------
# Universe filters — Tier 5 dynamic (v7)
# ---------------------------------------------------------------------------

UNIVERSE_MIN_VOLUME_USD: float = 5_000_000.0     # v7: $5M min 24h volume
TIER_5_MIN_VOLUME_USD: float = 5_000_000.0
TIER_5_MAX_SYMBOLS: int = 50
TIER_5_MIN_HISTORY_DAYS: int = 90                # v7: min 90 days listing history
TIER_5_MAX_4H_ATR_PCT: float = 0.15              # v7: 4H ATR not > 15% of price
TIER_5_MAX_CANDLE_MOVE_PCT: float = 0.25         # v7: no single candle > 25% in last 10
TIER_5_LOOKBACK_CANDLES: int = 10

TIER_5_EXCLUDE_PATTERNS: List[str] = [
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP",
    "UP", "DOWN", "BULL", "BEAR",
    "WBTC", "WETH", "STETH", "WBETH",
]


# ---------------------------------------------------------------------------
# Consolidation Watch (v7) — 4 criteria all must be met
# ---------------------------------------------------------------------------

CONS_BASE_PROXIMITY_PCT: float = 0.03            # within 3% of HTF base
CONS_VOL_THRESHOLD_PCT: float = 0.50             # volume < 50% of 20-period avg
CONS_ATR_THRESHOLD_PCT: float = 0.50             # ATR < 50% of 20-period avg
CONS_LOOKBACK_CANDLES: int = 10                  # last 10 candles for compression check
CONS_ADX_MAX: float = 20.0                       # ADX below 20 = no clear trend


# ---------------------------------------------------------------------------
# BTC reference symbol
# ---------------------------------------------------------------------------

BTC_SYMBOL: str = "BTC/USDT:USDT"


# ---------------------------------------------------------------------------
# Runtime / IO
# ---------------------------------------------------------------------------

def _csv(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def _bool(value: str, default: bool = True) -> bool:
    return value.lower() in ("1", "true", "yes", "on") if value else default


@dataclass
class Runtime:
    exchange_preference: List[str] = field(
        default_factory=lambda: _csv(os.getenv(
            "EXCHANGE_PREFERENCE",
            "binanceusdm,okx,kucoinfutures,bitget",
        ))
    )

    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_to: str = os.getenv("EMAIL_TO", "")

    run_mode: str = os.getenv("RUN_MODE", "loop").lower()
    loop_interval_sec: int = int(os.getenv("LOOP_INTERVAL_SEC", "60"))

    state_path: str = os.getenv("STATE_PATH", "state/v7_state.json")

    # v7 standalone watch alerts — flippable via env
    enable_consolidation_watch: bool = _bool(os.getenv("ENABLE_CONSOLIDATION_WATCH", "true"))
    enable_liquidity_approach: bool = _bool(os.getenv("ENABLE_LIQUIDITY_APPROACH", "true"))


RUNTIME = Runtime()
