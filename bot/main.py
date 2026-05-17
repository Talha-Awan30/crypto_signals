"""v5 scanner entrypoint.

Loop:
  every LOOP_INTERVAL_SEC:
    1. Build/refresh exchange
    2. Pull BTC 4H + 1D + classify regime + BTC context
    3. Volatility filter — if BTC daily ATR > 2.5x avg, skip
    4. For each asset:
         - skip if cooldown active and no invalidation/reset
         - tick existing setups on this asset (advance state machine)
         - on Daily and 4H: detect A, B, C, D (no LTF here)
         - if (A or C) AND (B or D) confluence: build Setup, score, persist,
           fire Stage 1 if score >= MIN_SCORE_DELIVER
         - on 1H and 15M for each AWAITING_LTF setup:
              run LTF validation; if triggered, fire Stage 2

Usage:
    python -m bot.main                # honours RUN_MODE env (default loop)
    python -m bot.main --once         # single cycle
    python -m bot.main --loop         # forever
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import List, Optional

import pandas as pd

from . import config as cfg
from . import cooldown
from .btc_corr import BTCContext, classify_btc_bias, direction_allowed, relative_strength
from .conditions.a_key_level import detect_condition_a
from .conditions.b_patterns import detect_condition_b
from .conditions.c_mss import detect_condition_c
from .conditions.d_liquidity import detect_condition_d, directional_events, untapped_targets
from .email_notify import send_email
from .exchange import build_exchange, fetch_ohlcv, normalize_symbol
from .formatter import format_stage1, format_stage2
from .ltf_validation import validate_ltf
from .regime import classify_regime, classify_regime_tier4, volatility_suppress
from .scoring import (
    ScoreContext,
    compute_score,
    is_london_or_ny,
    rs_score_delta,
    vol_compression,
    vol_expansion,
)
from .state_machine import Setup, StateStore, new_setup_id, tick_setup
from .targets import compute_targets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _now_iso() -> str:
    return pd.Timestamp.utcnow().isoformat()


def _scan_asset(ex, base: str, btc_ctx: BTCContext, regime, btc_4h: pd.DataFrame,
                btc_1d: pd.DataFrame, store: StateStore) -> None:
    symbol = normalize_symbol(ex, base)
    if not symbol:
        log.debug("no symbol for %s on %s", base, ex.id)
        return

    tier = cfg.tier_of(base)
    if tier == 0:
        return

    # ------------ Tick existing setups for this asset ------------
    _tick_existing(ex, base, symbol, store)

    if cooldown.in_cooldown(base):
        log.debug("%s in cooldown", base)
        return

    # ------------ Per-asset regime override for Tier 4 ------------
    # Tier 4 (commodities) are exempt from BTC's ADX regime — they use their
    # own Daily structural regime, and their own volatility-suppression check.
    asset_regime = regime
    if cfg.is_tier4(base):
        try:
            asset_daily = fetch_ohlcv(ex, symbol, "1d", limit=300)
            asset_regime = classify_regime_tier4(asset_daily)
            if volatility_suppress(asset_regime):
                log.warning("[%s] Tier 4 volatility suppression — own Daily ATR %.4f > %.1fx avg %.4f",
                            base, asset_regime.atr_value, cfg.VOL_SUPPRESS_ATR_MULT, asset_regime.atr_avg)
                return
            log.info("[%s] Tier 4 own regime: %s", base, asset_regime.label)
        except Exception as e:
            log.debug("Tier 4 regime fetch failed for %s: %s", base, e)

    # ------------ HTF detection ------------
    for tf in cfg.HTF_TIMEFRAMES:
        try:
            df = fetch_ohlcv(ex, symbol, tf, limit=300)
        except Exception as e:
            log.debug("ohlcv fail %s %s: %s", symbol, tf, e)
            continue
        if len(df) < 60:
            continue

        cond_a = detect_condition_a(df)
        cond_c = detect_condition_c(df)
        cond_b = detect_condition_b(df)
        cond_d_events = detect_condition_d(df)

        primary = cond_a or cond_c
        if not primary:
            continue

        direction = primary.direction
        # Confluence — need B or D in the same direction
        d_directional = directional_events(cond_d_events, direction)
        has_b = cond_b and cond_b.direction == direction
        has_d = bool(d_directional)
        if not (has_b or has_d):
            continue

        # BTC alignment gate
        allowed, btc_reason = direction_allowed(tier, direction, btc_ctx, df, btc_4h)
        if not allowed:
            log.info("[%s %s] %s blocked: %s", base, tf, direction, btc_reason)
            continue

        # Pick zone — Condition C's FVG/OB wins over A's key-level zone wins over B's boundary
        if cond_c and cond_c.direction == direction:
            zone = cond_c.zone
            invalidation = cond_c.broken_level
        elif cond_a and cond_a.direction == direction:
            zone = cond_a.zone
            invalidation = cond_a.level.price
        elif has_b:
            zone = cond_b.zone
            invalidation = cond_b.invalidation
        else:
            zone = d_directional[0].zone if d_directional[0].zone else None
            invalidation = d_directional[0].level_price
            if not zone:
                continue

        # Confidence
        liq_note = "; ".join(e.note for e in d_directional) if d_directional else "no liquidity context"
        for e in cond_d_events:
            if e.kind == "untapped":
                liq_note += f"; untapped @ {e.level_price:.4f}"

        conds_fired = []
        if cond_a and cond_a.direction == direction:
            conds_fired.append("A")
        if has_b:
            conds_fired.append("B")
        if cond_c and cond_c.direction == direction:
            conds_fired.append("C")
        if has_d:
            conds_fired.append("D")

        # Relative strength vs BTC — Tier 3/4 only (v5 scoring rule).
        # Measured over last 20 candles on the setup timeframe.
        rs_delta = 0
        if tier in (3, 4):
            btc_same_tf = btc_1d if tf == "1d" else btc_4h
            rs_val = relative_strength(df, btc_same_tf, lookback=20)
            rs_delta = rs_score_delta(rs_val)
            log.debug("[%s %s] RS vs BTC: %+.2f%% -> score delta %+d", base, tf, rs_val * 100, rs_delta)

        ctx = ScoreContext(
            direction=direction,
            htf_aligned=True,
            has_c_displacement=bool(cond_c and cond_c.direction == direction),
            vol_compression=vol_compression(df),
            has_d_class_1_2_5=has_d,
            btc_correlation_ok=True,
            vol_expansion_at_structure=vol_expansion(df),
            zone_kind=zone.kind,
            htf_condition_count=len(conds_fired),
            is_london_or_ny_session=is_london_or_ny(),
            regime=asset_regime,
            rs_score_delta=rs_delta,
        )
        score = compute_score(ctx)
        if cond_b and cond_b.confidence_cap is not None:
            score = min(score, cond_b.confidence_cap)

        if score < cfg.MIN_SCORE_DELIVER:
            log.info("[%s %s] score %d < %d — logged only", base, tf, score, cfg.MIN_SCORE_DELIVER)
            continue

        setup = Setup(
            id=new_setup_id(),
            symbol=symbol,
            base=base,
            tier=tier,
            direction=direction,
            timeframe=tf,
            conditions_fired=conds_fired,
            zone_low=zone.low,
            zone_high=zone.high,
            zone_kind=zone.kind,
            invalidation=invalidation,
            key_level=cond_a.level.price if cond_a and cond_a.direction == direction else invalidation,
            pattern_name=cond_b.name if cond_b and cond_b.direction == direction else None,
            pattern_category=cond_b.category if cond_b and cond_b.direction == direction else None,
            liquidity_note=liq_note,
            btc_context=btc_reason,
            market_regime=asset_regime.label + (" (Tier 4 structural)" if cfg.is_tier4(base) else ""),
            confidence=score,
            created_at=_now_iso(),
        )
        store.add(setup)
        cooldown.mark_alert(base)
        title, body = format_stage1(setup)
        send_email(title, body)
        log.info("[%s %s] Stage 1 fired — %s", base, tf, title)
        return  # one setup per asset per cycle is enough


def _tick_existing(ex, base: str, symbol: str, store: StateStore) -> None:
    """Advance every active setup on this asset, fire Stage 2 if LTF confirms."""
    for setup in store.for_asset(base):
        try:
            df_htf = fetch_ohlcv(ex, symbol, setup.timeframe, limit=120)
        except Exception:
            continue
        latest_close = float(df_htf["close"].iloc[-1])

        # Detect opposing structure: was there a confirmed counter-MSS?
        opposing = _opposing_structure(df_htf, setup.direction)
        new_state = tick_setup(setup, latest_close, opposing)
        setup.state = new_state

        if new_state in ("EXPIRED", "INVALIDATED"):
            store.archive(setup, new_state)
            log.info("[%s] setup %s -> %s", base, setup.id, new_state)
            if new_state == "INVALIDATED":
                cooldown.reset(base)
            continue

        if new_state == "AWAITING_LTF":
            # Try LTF validation now
            for ltf in cfg.LTF_TIMEFRAMES:
                try:
                    ltf_df = fetch_ohlcv(ex, symbol, ltf, limit=60)
                except Exception:
                    continue
                from .conditions.zones import Zone
                zone = Zone(low=setup.zone_low, high=setup.zone_high,
                            kind=setup.zone_kind, direction="bullish" if setup.direction == "long" else "bearish")
                val = validate_ltf(ltf_df, setup.direction, zone)
                if val.triggered:
                    # Build targets
                    targets = compute_targets(
                        df_htf,
                        setup.direction,
                        entry=(setup.zone_low + setup.zone_high) / 2,
                        sl=setup.invalidation,
                        untapped_targets=[],   # could plumb through cond_d, simple for now
                    )
                    setup.stage2_fired_at = _now_iso()
                    setup.ltf_trigger = f"{ltf}: {val.trigger_summary}"
                    setup.entry_zone_low = setup.zone_low
                    setup.entry_zone_high = setup.zone_high
                    if targets:
                        setup.tp1 = targets.tp1
                        setup.tp2 = targets.tp2
                        setup.rr_to_tp2 = targets.rr_to_tp2
                    if val.confluence_bonus:
                        setup.confidence = min(10, setup.confidence + 1)
                    store.archive(setup, "EXECUTED")
                    title, body = format_stage2(setup)
                    send_email(title, body)
                    log.info("[%s] Stage 2 fired — %s", base, title)
                    return

        store.update(setup)


def _opposing_structure(df: pd.DataFrame, direction: str) -> bool:
    """Quick check — did the latest close break the most recent OPPOSITE swing?"""
    from .indicators import last_n_swings, swing_pivots
    pivots = swing_pivots(df)
    close = float(df["close"].iloc[-1])
    if direction == "long":
        last_low = last_n_swings(pivots, "low", 1)
        return bool(last_low) and close < last_low[-1].price
    else:
        last_high = last_n_swings(pivots, "high", 1)
        return bool(last_high) and close > last_high[-1].price


def run_once() -> None:
    log.info("=== v5 scan start ===")
    try:
        ex = build_exchange()
    except Exception as e:
        log.error("exchange init failed: %s", e)
        return

    # BTC context
    try:
        btc_4h = fetch_ohlcv(ex, cfg.BTC_SYMBOL, "4h", limit=300)
        btc_1d = fetch_ohlcv(ex, cfg.BTC_SYMBOL, "1d", limit=300)
    except Exception as e:
        log.error("BTC fetch failed: %s", e)
        return

    regime = classify_regime(btc_1d)
    log.info("regime: %s ADX=%.1f ATR=%.4f (avg %.4f)",
             regime.label, regime.adx_value, regime.atr_value, regime.atr_avg)

    # BTC volatility suppression applies to crypto Tiers 1-3 only. Tier 4
    # (commodities) is independent of crypto regime and has its own per-asset
    # volatility check inside _scan_asset.
    crypto_suppressed = volatility_suppress(regime)
    if crypto_suppressed:
        log.warning("CRYPTO VOLATILITY SUPPRESSION — BTC Daily ATR %.4f > %.1fx avg %.4f. "
                    "Tiers 1-3 paused; Tier 4 still scanned.",
                    regime.atr_value, cfg.VOL_SUPPRESS_ATR_MULT, regime.atr_avg)

    btc_ctx = classify_btc_bias(btc_4h, btc_1d)
    log.info("BTC context: %s", btc_ctx.summary)

    store = StateStore()

    for base in cfg.all_symbols():
        # When BTC is too volatile, skip crypto tiers but keep scanning Tier 4.
        if crypto_suppressed and not cfg.is_tier4(base):
            continue
        try:
            _scan_asset(ex, base, btc_ctx, regime, btc_4h, btc_1d, store)
        except Exception as e:
            log.warning("asset %s failed: %s", base, e)

    log.info("=== v5 scan done ===")


def run_loop() -> None:
    interval = cfg.RUNTIME.loop_interval_sec
    log.info("loop mode, interval=%ds", interval)
    while True:
        start = time.time()
        try:
            run_once()
        except Exception as e:
            log.exception("scan crashed: %s", e)
        elapsed = time.time() - start
        sleep_for = max(10, interval - elapsed)
        log.info("sleeping %.0fs", sleep_for)
        time.sleep(sleep_for)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    args = p.parse_args()
    mode = "once" if args.once else ("loop" if args.loop else cfg.RUNTIME.run_mode)
    if mode == "loop":
        run_loop()
    else:
        run_once()


if __name__ == "__main__":
    main()
