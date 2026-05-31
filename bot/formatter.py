"""Alert formatter — v7.

Stage 1: [STAGE 1] ZONE ALERT | ASSET | DIR | TF | Score: X/10 | B / D / B+D
Stage 2: [STAGE 2] EXECUTION ALERT | ASSET | DIR | TF | Score: X/10 | ENTRY READY
Consolidation Watch: [WATCH] CONSOLIDATION | ASSET | HTF BASE | COMPRESSION DETECTED
Liquidity Approach:  [WATCH] LIQUIDITY APPROACH | ASSET | EQH/EQL | price
"""
from __future__ import annotations

from typing import Optional

from . import config as cfg
from .state_machine import Setup
from .watches import ConsolidationWatch, LiquidityApproachWatch


def _tier_label(tier: int) -> str:
    return {1: "Tier 1 — BTC Core",
            2: "Tier 2 — BTC-Correlated Mid/Large Cap",
            3: "Tier 3 — Relative Strength / Narrative",
            4: "Tier 4 — Commodity-Linked Token",
            5: "Tier 5 — Dynamic Volume-Filtered"}.get(tier, "Tier ?")


def _format_pd(label: Optional[str], pct: Optional[float]) -> str:
    if not label or pct is None:
        return "n/a"
    if label == "EQUILIBRIUM":
        return "EQUILIBRIUM"
    return f"{label} at {pct:.1f}% from EQ"


def format_stage1(setup: Setup) -> tuple[str, str]:
    title = (f"[STAGE 1] ZONE ALERT | {setup.base} | {setup.direction.upper()} | "
             f"{setup.timeframe} | Score: {setup.confidence}/10 | {setup.condition}")

    pattern_str = f"{setup.pattern_name}" if setup.pattern_name else "n/a"
    if setup.pattern_name and setup.pattern_category:
        pattern_str += f" ({setup.pattern_category})"
    liquidity_str = "n/a"
    if setup.liquidity_event_kind and setup.liquidity_event_price:
        liquidity_str = f"{setup.liquidity_event_kind} @ {setup.liquidity_event_price:.6f}"

    cons_str = "n/a"
    if setup.consolidation_watch_flag:
        cons_str = f"CONSOLIDATION WATCH — {setup.base} at HTF base. Compression detected."

    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Current Price:          {setup.current_price:.6f}",
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe}",
        f"Condition Fired:        {setup.condition}",
        f"Pattern Detected:       {pattern_str}",
        f"Liquidity Event:        {liquidity_str}",
        f"Pending Order Zone:     {setup.pending_zone_levels or 'n/a'}",
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"Premium / Discount:     {_format_pd(setup.premium_discount_label, setup.premium_discount_pct)}",
        f"SL:                     {setup.sl:.6f} (candle close basis)",
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        f"Consolidation Watch:    {cons_str}",
        f"Source Exchange:        {setup.exchange_id or 'unknown'}",
        f"Prompt Version:         {cfg.PROMPT_VERSION}",
        "",
        "Status: STANDBY — awaiting price into zone, then 30M/15M LTF validation.",
        f"Timeout: {cfg.TIMEOUT_CANDLES} candles (+{cfg.TIMEOUT_EXTEND} extension; hard {cfg.TIMEOUT_HARD_MAX}).",
    ]
    return title, "\n".join(lines)


def format_stage2(setup: Setup) -> tuple[str, str]:
    title = (f"[STAGE 2] EXECUTION ALERT | {setup.base} | {setup.direction.upper()} | "
             f"{setup.timeframe} | Score: {setup.confidence}/10 | ENTRY READY")

    pattern_str = f"{setup.pattern_name}" if setup.pattern_name else "n/a"
    if setup.pattern_name and setup.pattern_category:
        pattern_str += f" ({setup.pattern_category})"
    liquidity_str = "n/a"
    if setup.liquidity_event_kind and setup.liquidity_event_price:
        liquidity_str = f"{setup.liquidity_event_kind} @ {setup.liquidity_event_price:.6f}"
    ltf_tf = setup.ltf_trigger.split(":")[0] if setup.ltf_trigger else "30m"
    entry_lo = setup.entry_zone_low if setup.entry_zone_low is not None else setup.zone_low
    entry_hi = setup.entry_zone_high if setup.entry_zone_high is not None else setup.zone_high

    lines = [
        f"Coin / Tier:            {setup.base} ({_tier_label(setup.tier)})",
        f"Current Price:          {setup.current_price:.6f}",
        f"Direction:              {setup.direction.upper()}",
        f"Market Regime:          {setup.market_regime}",
        f"Timeframe:              HTF {setup.timeframe} / LTF {ltf_tf}",
        f"Condition Fired:        {setup.condition}",
        f"Pattern Detected:       {pattern_str}",
        f"Liquidity Event:        {liquidity_str}",
        f"Pending Order Zone:     {setup.pending_zone_levels or 'n/a'}",
        f"Key Level:              {setup.key_level:.6f}",
        f"Retracement Zone:       {setup.zone_low:.6f} – {setup.zone_high:.6f} ({setup.zone_kind})",
        f"Premium / Discount:     {_format_pd(setup.premium_discount_label, setup.premium_discount_pct)}",
        f"LTF Confirmation:       {setup.ltf_trigger or 'n/a'}",
        f"Entry Zone:             {entry_lo:.6f} – {entry_hi:.6f}",
        f"TP1:                    {setup.tp1:.6f}" if setup.tp1 is not None else "TP1: n/a",
        f"TP2:                    {setup.tp2:.6f}" if setup.tp2 is not None else "TP2: n/a",
        f"TP3:                    {setup.tp3:.6f}" if setup.tp3 is not None else "TP3: n/a",
        f"SL:                     {setup.sl:.6f} (candle close basis)",
        f"R:R to TP1:             {setup.rr_to_tp1 if setup.rr_to_tp1 is not None else 'n/a'}",
        f"R:R to TP2:             {setup.rr_to_tp2 if setup.rr_to_tp2 is not None else 'n/a'}",
        f"BTC Correlation:        {setup.btc_context}",
        f"Confidence Score:       {setup.confidence}/10",
        f"Source Exchange:        {setup.exchange_id or 'unknown'}",
        f"Prompt Version:         {cfg.PROMPT_VERSION}",
        "",
        f"Reasoning — Structure:  {setup.reason_structure or '—'}",
        f"Reasoning — Zone:       {setup.reason_zone or '—'}",
        f"Reasoning — Execution:  {setup.reason_execution or '—'}",
        "",
        "Disclaimer: HTF defines the opportunity. LTF confirms execution.",
        "Final entry is at the trader's discretion.",
    ]
    return title, "\n".join(lines)


def format_consolidation_watch(cw: ConsolidationWatch, tier: int) -> tuple[str, str]:
    title = f"[WATCH] CONSOLIDATION | {cw.base} | HTF BASE | COMPRESSION DETECTED"
    lines = [
        f"Coin / Tier:            {cw.base} ({_tier_label(tier)})",
        f"Current Price:          {cw.current_price:.6f}",
        f"HTF Base Level:         {cw.htf_base_level:.6f}",
        f"Volume vs 20-bar avg:   -{cw.vol_pct_below_avg:.1f}%",
        f"ATR vs 20-bar avg:      -{cw.atr_pct_below_avg:.1f}%",
        f"ADX:                    {cw.adx_value:.1f}",
        f"Observation:            {cw.note}",
        f"Prompt Version:         {cfg.PROMPT_VERSION}",
        "",
        "Monitor for breakout trigger. No confidence score required for Consolidation Watch.",
    ]
    return title, "\n".join(lines)


def format_liquidity_approach_watch(ap: LiquidityApproachWatch, tier: int) -> tuple[str, str]:
    title = (f"[WATCH] LIQUIDITY APPROACH | {ap.base} | {ap.side} | "
             f"{ap.approaching_level:.6f}")
    lines = [
        f"Coin / Tier:            {ap.base} ({_tier_label(tier)})",
        f"Current Price:          {ap.current_price:.6f}",
        f"Approaching Level:      {ap.approaching_level:.6f} ({ap.side})",
        f"Distance from level:    {ap.distance_pct * 100:.2f}%",
        f"Historical Reactions:   {ap.historical_reactions}",
        f"Prompt Version:         {cfg.PROMPT_VERSION}",
        "",
        "Sweep potential within current candle. Trader monitors for sweep and reclaim manually.",
    ]
    return title, "\n".join(lines)
