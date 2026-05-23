"""Alert formatter — Stage 1 and Stage 2 templates per BOT-001..010.

Subject-line format (BOT-009):
  Stage 1: [STAGE 1] ZONE ALERT | ASSET | DIRECTION | TF | Score: X/10 | A+B
  Stage 2: [STAGE 2] EXECUTION ALERT | ASSET | DIRECTION | TF | Score: X/10 | ENTRY READY

Field labels are identical between stages (BOT-005); only the set of fields
differs (Stage 1 omits Entry Zone / LTF / TP1 / TP2 / R:R).
"""
from __future__ import annotations

from typing import Optional

from . import config as cfg
from .state_machine import Setup


def _tier_label(tier: int) -> str:
    return {1: "Tier 1 — BTC-Correlated Major",
            2: "Tier 2 — Semi-Correlated Mid-Cap",
            3: "Tier 3 — Narrative / Relative-Strength",
            4: "Tier 4 — Commodity-Linked Token",
            5: "Tier 5 — Dynamic Volume-Filtered"}.get(tier, "Tier ?")


def _liquidity_line(setup: Setup) -> str:
    parts = []
    if setup.liquidity_primary:
        parts.append(setup.liquidity_primary)
    if setup.liquidity_untapped_above is not None:
        parts.append(f"Untapped Above: {setup.liquidity_untapped_above:.6f}")
    if setup.liquidity_untapped_below is not None:
        parts.append(f"Untapped Below: {setup.liquidity_untapped_below:.6f}")
    return " | ".join(parts) if parts else (setup.liquidity_note or "n/a")


def _conditions_str(setup: Setup) -> str:
    return " + ".join(setup.conditions_fired) if setup.conditions_fired else "—"


def format_stage1(setup: Setup) -> tuple[str, str]:
    # BOT-009 subject
    title = (f"[STAGE 1] ZONE ALERT | {setup.base} | {setup.direction.upper()} | "
             f"{setup.timeframe} | Score: {setup.confidence}/10 | {_conditions_str(setup)}")

    cp = f"{setup.current_price:.6f}" if setup.current_price else "n/a"
    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Current Price:          {cp}",                                         # BOT-001
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe}",
        f"Conditions Met:         {_conditions_str(setup)}",
        f"Pattern Detected:       {setup.pattern_name or 'n/a'}"
        + (f" ({setup.pattern_category})" if setup.pattern_category else ""),
        f"Liquidity Context:      {_liquidity_line(setup)}",                     # BOT-003
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"SL:                     {setup.invalidation:.6f} (candle close basis)",  # BOT-005
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        f"Source Exchange:        {setup.exchange_id or 'unknown'}",             # BOT-008 spirit
        "",
        "Status: STANDBY — awaiting price into zone, then LTF validation.",
        f"Timeout: {cfg.TIMEOUT_CANDLES} candles (+{cfg.TIMEOUT_EXTEND} extension; hard {cfg.TIMEOUT_HARD_MAX}).",
    ]
    return title, "\n".join(lines)


def format_stage2(setup: Setup) -> tuple[str, str]:
    # BOT-009 subject
    title = (f"[STAGE 2] EXECUTION ALERT | {setup.base} | {setup.direction.upper()} | "
             f"{setup.timeframe} | Score: {setup.confidence}/10 | ENTRY READY")

    cp = f"{setup.current_price:.6f}" if setup.current_price else "n/a"
    ltf_tf = setup.ltf_trigger.split(':')[0] if setup.ltf_trigger else "1h"
    entry_lo = setup.entry_zone_low if setup.entry_zone_low is not None else setup.zone_low
    entry_hi = setup.entry_zone_high if setup.entry_zone_high is not None else setup.zone_high

    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Current Price:          {cp}",                                         # BOT-001
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe} / LTF {ltf_tf}",
        f"Conditions Met:         {_conditions_str(setup)}",
        f"Pattern Detected:       {setup.pattern_name or 'n/a'}"
        + (f" ({setup.pattern_category})" if setup.pattern_category else ""),
        f"Liquidity Context:      {_liquidity_line(setup)}",                     # BOT-003
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"LTF Confirmation:       {setup.ltf_trigger or 'n/a'}",
        f"Entry Zone:             {entry_lo:.6f} – {entry_hi:.6f}",
        f"TP1:                    {setup.tp1:.6f}",
        f"TP2:                    {setup.tp2:.6f}",
        f"SL:                     {setup.invalidation:.6f} (candle close basis)",  # BOT-005
        f"R:R to TP2:             {setup.rr_to_tp2}",
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        f"Source Exchange:        {setup.exchange_id or 'unknown'}",             # BOT-008 spirit
        "",
        # BOT-010: exact 3-line structured reasoning
        f"Reasoning — Structure:  {setup.reason_structure or '—'}",
        f"Reasoning — Zone:       {setup.reason_zone or '—'}",
        f"Reasoning — Execution:  {setup.reason_execution or setup.ltf_trigger or '—'}",
        "",
        "Rule: HTF defines the opportunity. LTF confirms execution.",
        "Final entry confirmation is at the trader's discretion.",
    ]
    return title, "\n".join(lines)
