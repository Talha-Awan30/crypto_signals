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
from .state_machine import (
    Setup,
    StateStore,
    compute_setup_hash,
    has_active_setup_hash,
    has_opposing_active_in_candle,
    new_setup_id,
    tick_setup,
)
from .targets import compute_targets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _now_iso() -> str:
    return pd.Timestamp.utcnow().isoformat()


def build_tier5_universe(ex) -> List[str]:
    """BOT-011: dynamic universe — active-exchange USDT perps with >$10M 24h vol,
    excluding Tiers 1-4 and stablecoins/wrapped/leveraged tokens.

    Capped at TIER_5_MAX_SYMBOLS (default 50) by descending volume.
    """
    fixed = set(cfg.TIER_1 + cfg.TIER_2 + cfg.TIER_3 + cfg.TIER_4)
    try:
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.debug("Tier 5 fetch_tickers failed: %s", e)
        return []
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m or not m.get("swap") or m.get("quote") != "USDT":
            continue
        base = m.get("base") or ""
        if base in fixed:
            continue
        # exclude stables, wrapped, leveraged
        if any(p in base.upper() for p in cfg.TIER_5_EXCLUDE_PATTERNS):
            # but only if base IS one of the patterns, not just contains random text
            if base.upper() in cfg.TIER_5_EXCLUDE_PATTERNS:
                continue
        qv = t.get("quoteVolume") or 0
        if qv < cfg.TIER_5_MIN_VOLUME_USD:
            continue
        rows.append((base, float(qv)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [b for b, _ in rows[: cfg.TIER_5_MAX_SYMBOLS]]


def _structured_liquidity(cond_d_events, direction: str, current_price: float):
    """BOT-003: pick 3 most relevant liquidity items.

    Returns (primary_str, untapped_above, untapped_below).
    """
    primary = None
    for e in cond_d_events:
        if e.contributes_to_confluence and e.direction == direction:
            primary = f"{e.kind} @ {e.level_price:.6f}"
            break

    untapped_above = None
    untapped_below = None
    for e in cond_d_events:
        if e.kind == "untapped":
            if e.level_price > current_price:
                if untapped_above is None or e.level_price < untapped_above:
                    untapped_above = e.level_price
            elif e.level_price < current_price:
                if untapped_below is None or e.level_price > untapped_below:
                    untapped_below = e.level_price
    return primary, untapped_above, untapped_below


def _build_reasoning(setup_event_kind: str, direction: str, zone_kind: str,
                     conditions_fired, liquidity_primary, ltf_trigger):
    """BOT-010: 3 fixed lines."""
    cond_summary = " + ".join(conditions_fired) if conditions_fired else "core conditions"
    structure = (f"{setup_event_kind} confirmed in {direction} direction with "
                 f"{cond_summary} confluence on the HTF candle.")
    zone_descr = (f"Retracement zone defined as {zone_kind}; "
                  f"{liquidity_primary or 'no nearby liquidity sweep recorded'}.")
    execution = (ltf_trigger or
                 "LTF validation pending — Stage 2 will fire on micro-MSS / displacement / rejection in zone.")
    return structure, zone_descr, execution


def _scan_asset(ex, base: str, btc_ctx: BTCContext, regime, btc_4h: pd.DataFrame,
                btc_1d: pd.DataFrame, store: StateStore,
                tier5_set: Optional[set] = None) -> None:
    symbol = normalize_symbol(ex, base)
    if not symbol:
        log.debug("no symbol for %s on %s", base, ex.id)
        return

    tier = cfg.tier_of(base, tier5_set)
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

        # Conditions list
        conds_fired = []
        if cond_a and cond_a.direction == direction:
            conds_fired.append("A")
        if has_b:
            conds_fired.append("B")
        if cond_c and cond_c.direction == direction:
            conds_fired.append("C")
        if has_d:
            conds_fired.append("D")

        # BOT-003: structured 3-item liquidity context
        current_price = float(df["close"].iloc[-1])
        liq_primary, liq_above, liq_below = _structured_liquidity(cond_d_events, direction, current_price)
        liq_note_parts = []
        if liq_primary:
            liq_note_parts.append(liq_primary)
        if liq_above is not None:
            liq_note_parts.append(f"Untapped Above: {liq_above:.6f}")
        if liq_below is not None:
            liq_note_parts.append(f"Untapped Below: {liq_below:.6f}")
        liq_note = " | ".join(liq_note_parts) if liq_note_parts else "no liquidity context"

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

        # BOT-011: Tier 5 score cap when A/C absent
        has_a = bool(cond_a and cond_a.direction == direction)
        has_c = bool(cond_c and cond_c.direction == direction)
        if tier == 5 and not (has_a or has_c):
            score = min(score, cfg.TIER_5_SCORE_CAP_WITHOUT_AC)

        # BOT-007: strict final-score gate
        if score < cfg.MIN_SCORE_DELIVER:
            log.info("[%s %s] score %d < %d — Below Threshold (internal only)",
                     base, tf, score, cfg.MIN_SCORE_DELIVER)
            continue

        # BOT-004: deterministic dedupe — block duplicate Stage 1 for same setup
        key_level_for_hash = cond_a.level.price if has_a else invalidation
        setup_hash = compute_setup_hash(symbol, direction, key_level_for_hash, tf)
        if has_active_setup_hash(store, setup_hash):
            log.info("[%s %s] Stage 1 SUPPRESSED — DUPLICATE setup_hash %s already active",
                     base, tf, setup_hash)
            continue

        # BOT-006: block opposing-direction alerts within same HTF candle
        candle_id = str(df.index[-1])
        if has_opposing_active_in_candle(store, base, candle_id, direction):
            log.warning("[%s %s] Stage 1 SUPPRESSED — CONFLICTING SIGNAL within same candle",
                        base, tf)
            continue

        # BOT-010: structured 3-line reasoning
        setup_event_kind = ("MSS" if has_c else
                            "HTF Key Level Reaction" if has_a else
                            (cond_b.name if cond_b else "Setup"))
        r_struct, r_zone, r_exec = _build_reasoning(setup_event_kind, direction, zone.kind,
                                                    conds_fired, liq_primary, None)

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
            key_level=key_level_for_hash,
            pattern_name=cond_b.name if cond_b and cond_b.direction == direction else None,
            pattern_category=cond_b.category if cond_b and cond_b.direction == direction else None,
            liquidity_note=liq_note,
            btc_context=btc_reason,
            market_regime=asset_regime.label + (" (Tier 4 structural)" if cfg.is_tier4(base) else ""),
            confidence=score,
            created_at=_now_iso(),
            # BOT-001 / BOT-004 / BOT-006 / BOT-008
            current_price=current_price,
            exchange_id=getattr(ex, "id", "unknown"),
            setup_hash=setup_hash,
            candle_id=candle_id,
            liquidity_primary=liq_primary,
            liquidity_untapped_above=liq_above,
            liquidity_untapped_below=liq_below,
            reason_structure=r_struct,
            reason_zone=r_zone,
            reason_execution=r_exec,
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
                        untapped_targets=[],
                    )
                    setup.stage2_fired_at = _now_iso()
                    setup.ltf_trigger = f"{ltf}: {val.trigger_summary}"
                    setup.entry_zone_low = setup.zone_low
                    setup.entry_zone_high = setup.zone_high
                    if targets:
                        setup.tp1 = targets.tp1
                        setup.tp2 = targets.tp2
                        setup.rr_to_tp2 = targets.rr_to_tp2
                    # LTF confluence bonus (+1) — applied BEFORE the delivery gate
                    if val.confluence_bonus:
                        setup.confidence = min(10, setup.confidence + 1)
                    # Capture live price at firing time (BOT-001)
                    setup.current_price = latest_close
                    # BOT-010: refresh execution reasoning with actual LTF trigger
                    setup.reason_execution = setup.ltf_trigger
                    # BOT-002: pre-delivery null check on TP1/TP2/R:R
                    if setup.tp1 is None or setup.tp2 is None or setup.rr_to_tp2 is None:
                        store.archive(setup, "INCOMPLETE_NO_TARGETS")
                        log.warning("[%s] Stage 2 SUPPRESSED — INCOMPLETE SETUP — TP1/TP2/R:R missing", base)
                        return
                    # BOT-007: hard delivery gate on FINAL score (after LTF bonus)
                    if setup.confidence < cfg.MIN_SCORE_DELIVER:
                        store.archive(setup, "BELOW_THRESHOLD")
                        log.info("[%s] Stage 2 SUPPRESSED — final score %d < %d",
                                 base, setup.confidence, cfg.MIN_SCORE_DELIVER)
                        return
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

    # BOT-011: build Tier 5 dynamic universe each cycle
    tier5_list = build_tier5_universe(ex)
    tier5_set = set(tier5_list)
    log.info("Tier 5 dynamic universe: %d symbols (cap %d, min $%.0fM vol)",
             len(tier5_list), cfg.TIER_5_MAX_SYMBOLS, cfg.TIER_5_MIN_VOLUME_USD / 1e6)

    universe = list(cfg.all_symbols()) + tier5_list

    for base in universe:
        # When BTC is too volatile, skip crypto tiers but keep scanning Tier 4.
        if crypto_suppressed and not cfg.is_tier4(base):
            continue
        try:
            _scan_asset(ex, base, btc_ctx, regime, btc_4h, btc_1d, store, tier5_set)
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
