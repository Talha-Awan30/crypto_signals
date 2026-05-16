"""Strict 16-field alert formatter (v5 spec).

Both Stage 1 (Zone Alert) and Stage 2 (Execution Alert) share most fields;
Stage 1 omits LTF trigger, entry zone, TP1/TP2, SL precision, R:R, and
confidence-final.
"""
from __future__ import annotations

from typing import Optional

from . import config as cfg
from .state_machine import Setup


def _tier_label(tier: int) -> str:
    return {1: "Tier 1 — BTC-Correlated Major",
            2: "Tier 2 — Semi-Correlated Mid-Cap",
            3: "Tier 3 — Narrative / Relative-Strength"}.get(tier, "Tier ?")


def _priority_tag(score: int) -> str:
    if score >= cfg.PRIORITY_SCORE:
        return " [PRIORITY]"
    return ""


def format_stage1(setup: Setup) -> tuple[str, str]:
    title = f"ZONE ALERT — STANDBY · {setup.base} · {setup.direction.upper()} · {setup.timeframe}{_priority_tag(setup.confidence)}"
    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe}",
        f"Conditions Met:         {' + '.join(setup.conditions_fired)}",
        f"Pattern Detected:       {setup.pattern_name or 'n/a'}"
        + (f" ({setup.pattern_category})" if setup.pattern_category else ""),
        f"Liquidity Context:      {setup.liquidity_note or 'n/a'}",
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"Invalidation:           {setup.invalidation:.6f} (candle close basis)",
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        "",
        "Status: STANDBY — awaiting price into zone, then LTF validation.",
        f"Timeout: {cfg.TIMEOUT_CANDLES} candles (+{cfg.TIMEOUT_EXTEND} extension; hard {cfg.TIMEOUT_HARD_MAX}).",
    ]
    return title, "\n".join(lines)


def format_stage2(setup: Setup) -> tuple[str, str]:
    title = f"EXECUTION ALERT — ENTRY READY · {setup.base} · {setup.direction.upper()} · {setup.timeframe}{_priority_tag(setup.confidence)}"
    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe} / LTF {setup.ltf_trigger.split(':')[0] if setup.ltf_trigger else '1h'}",
        f"Conditions Met:         {' + '.join(setup.conditions_fired)}",
        f"Pattern Detected:       {setup.pattern_name or 'n/a'}"
        + (f" ({setup.pattern_category})" if setup.pattern_category else ""),
        f"Liquidity Context:      {setup.liquidity_note or 'n/a'}",
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"LTF Confirmation:       {setup.ltf_trigger or 'n/a'}",
        f"Entry Zone:             {(setup.entry_zone_low or setup.zone_low):.6f} – {(setup.entry_zone_high or setup.zone_high):.6f}",
        f"TP1:                    {setup.tp1:.6f}" if setup.tp1 else "TP1: n/a",
        f"TP2:                    {setup.tp2:.6f}" if setup.tp2 else "TP2: n/a",
        f"SL:                     {setup.invalidation:.6f} (candle close basis)",
        f"R:R to TP2:             {setup.rr_to_tp2 if setup.rr_to_tp2 else 'n/a'}",
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        "",
        "Reasoning:",
        f"  Structure → {setup.zone_kind} zone built from {', '.join(setup.conditions_fired)}.",
        f"  Confluence: {setup.liquidity_note or 'core conditions only'}.",
        f"  Execution: {setup.ltf_trigger or 'LTF trigger pending'}.",
        "",
        "Rule: HTF defines opportunity, LTF confirms execution.",
        "Final entry confirmation is at trader's discretion.",
    ]
    return title, "\n".join(lines)
