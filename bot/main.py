"""v7 scanner entrypoint — B and D independent triggers; two-stage alerts.

Per asset per cycle:
  1. Tick existing setups (advance state machine, send Stage 2 if ready)
  2. Per HTF (1D/4H/2H/1H):
        Detect Condition B (pattern breakout) — if found, fire Stage 1 (if score >= 8)
        Detect Condition D events — fire Stage 1 for each trigger (sweep+reclaim,
        external sweep, sweep+accept). Both can fire on the same asset if both
        independently qualify; if same direction on same TF, also flag B+D CONFLUENCE.
  3. On Daily TF only: run Consolidation Watch (if enabled).
  4. Run Liquidity Approach Watch (if enabled, informational).
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config as cfg
from .btc_corr import BTCContext, classify_btc_bias, relative_strength
from .conditions.b_patterns import detect_condition_b
from .conditions.d_liquidity import (
    LiquidityEvent,
    detect_condition_d,
    pending_zones,
    primary_triggers,
)
from .conditions.zones import Zone
from .email_notify import send_email
from .exchange import build_exchange, fetch_ohlcv, normalize_symbol
from .formatter import (
    format_consolidation_watch,
    format_liquidity_approach_watch,
    format_stage1,
    format_stage2,
)
from .indicators import atr as atr_series_fn
from .ltf_validation import validate_ltf
from .regime import classify_regime, classify_regime_tier4, is_news_candle, volatility_suppress
from .scoring import (
    ScoreContext,
    compute_score,
    rs_score_delta,
    vol_ratio_prior5,
)
from .sl_geometry import compute_sl, is_sl_valid, premium_discount
from .state_machine import (
    Setup,
    StateStore,
    compute_setup_hash,
    has_active_setup_hash,
    has_opposing_active_in_candle,
    has_recent_same_direction_alert,
    new_setup_id,
    tick_setup,
)
from .targets import compute_targets
from .watches import (
    ConsolidationWatch,
    LiquidityApproachWatch,
    consolidation_watch,
    liquidity_approach_watch,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _now_iso() -> str:
    return pd.Timestamp.utcnow().isoformat()


def build_tier5_universe(ex) -> List[str]:
    """v7 Tier 5: USDT perps with >=$5M 24h vol, >=90d history, 4H ATR <= 15%
    of price, no single candle move > 25% in last 10 candles. Cap 50."""
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
        if base.upper() in cfg.TIER_5_EXCLUDE_PATTERNS:
            continue
        qv = t.get("quoteVolume") or 0
        if qv < cfg.UNIVERSE_MIN_VOLUME_USD:
            continue
        rows.append((base, float(qv)))
    rows.sort(key=lambda x: x[1], reverse=True)
    # Trim to cap; further filters (ATR%, candle-move, history) applied per-asset
    return [b for b, _ in rows[: cfg.TIER_5_MAX_SYMBOLS]]


def _tier5_extra_filters_pass(df_4h: pd.DataFrame) -> bool:
    if len(df_4h) < cfg.ATR_PERIOD + 12:
        return False
    a = atr_series_fn(df_4h, cfg.ATR_PERIOD)
    atr_now = float(a.iloc[-1])
    price = float(df_4h["close"].iloc[-1])
    if price <= 0 or atr_now / price > cfg.TIER_5_MAX_4H_ATR_PCT:
        return False
    last10 = df_4h.iloc[-cfg.TIER_5_LOOKBACK_CANDLES:]
    moves = ((last10["close"] - last10["open"]).abs() / last10["open"]).abs()
    if (moves > cfg.TIER_5_MAX_CANDLE_MOVE_PCT).any():
        return False
    return True


def _build_score_ctx(
    timeframe: str,
    regime,
    pattern_name: Optional[str],
    liquidity_kind: Optional[str],
    df: pd.DataFrame,
    ltf_confluence_2plus: bool,
    rs_delta: int,
    trap_warning: bool,
    bd_confluence: bool,
    tier2_btc_opposing: bool,
) -> ScoreContext:
    return ScoreContext(
        timeframe=timeframe,
        regime=regime,
        pattern_name=pattern_name,
        liquidity_kind=liquidity_kind,
        breakout_vol_ratio_prior5=vol_ratio_prior5(df),
        ltf_confluence_2plus=ltf_confluence_2plus,
        rs_score_delta=rs_delta,
        trap_warning=trap_warning,
        bd_confluence=bd_confluence,
        tier2_btc_opposing=tier2_btc_opposing,
    )


def _try_fire_stage1(
    store: StateStore,
    base: str,
    symbol: str,
    tier: int,
    direction: str,
    timeframe: str,
    df: pd.DataFrame,
    zone: Zone,
    pattern_name: Optional[str],
    pattern_category: Optional[str],
    liquidity_event: Optional[LiquidityEvent],
    pending_levels: List[LiquidityEvent],
    asset_regime,
    btc_reason: str,
    rs_delta: int,
    trap_warning: bool,
    bd_confluence: bool,
    tier2_btc_opposing: bool,
    cons_watch: Optional[ConsolidationWatch],
    exchange_id: str,
    condition_label: str,
) -> Optional[Setup]:
    """Validate, score, and (if eligible) persist + email a Stage 1 alert."""
    atr_now = float(atr_series_fn(df, cfg.ATR_PERIOD).iloc[-1])

    # SL placement with directional padding
    sl = compute_sl(direction, zone.low, zone.high, atr_now)
    # entry zone == retracement zone for Stage 1
    if not is_sl_valid(direction, sl, zone.low, zone.high):
        log.warning("[%s %s %s] Stage 1 SUPPRESSED — INVALID SL GEOMETRY (sl=%.6f vs zone %.6f-%.6f)",
                    base, timeframe, direction, sl, zone.low, zone.high)
        return None

    # Score
    liquidity_kind = liquidity_event.kind if liquidity_event else None
    ctx = _build_score_ctx(
        timeframe=timeframe, regime=asset_regime, pattern_name=pattern_name,
        liquidity_kind=liquidity_kind, df=df, ltf_confluence_2plus=False,
        rs_delta=rs_delta, trap_warning=trap_warning,
        bd_confluence=bd_confluence, tier2_btc_opposing=tier2_btc_opposing,
    )
    score = compute_score(ctx)

    if score < cfg.MIN_SCORE_DELIVER:
        log.info("[%s %s %s] score %d < %d — Below Threshold (internal only)",
                 base, timeframe, condition_label, score, cfg.MIN_SCORE_DELIVER)
        return None

    # v7 cooldown — 4h same-direction; opposite direction allowed immediately
    if has_recent_same_direction_alert(store, base, direction):
        log.info("[%s %s %s] Stage 1 SUPPRESSED — 4h same-direction cooldown active",
                 base, timeframe, direction)
        return None

    # Deterministic dedupe by setup_hash
    setup_hash = compute_setup_hash(symbol, direction, zone.low if direction == "long" else zone.high, timeframe)
    if has_active_setup_hash(store, setup_hash):
        log.info("[%s %s] Stage 1 SUPPRESSED — DUPLICATE setup_hash %s active",
                 base, timeframe, setup_hash)
        return None

    # Same-candle opposing-direction block
    candle_id = str(df.index[-1])
    if has_opposing_active_in_candle(store, base, candle_id, direction):
        log.warning("[%s %s] Stage 1 SUPPRESSED — CONFLICTING SIGNAL (opposite direction in same candle)",
                    base, timeframe)
        return None

    # Premium / Discount
    swing_high = float(df["high"].iloc[-40:].max())
    swing_low = float(df["low"].iloc[-40:].min())
    pd_label, pd_pct = premium_discount(float(df["close"].iloc[-1]), swing_high, swing_low)

    # Pending zone summary
    pending_text = None
    if pending_levels:
        items = []
        for pz in pending_levels[:3]:
            items.append(f"{pz.note}")
        pending_text = " | ".join(items)

    # Reasoning (3-line)
    r_structure = (f"{condition_label} confirmed in {direction} direction on {timeframe} "
                   f"({pattern_name or liquidity_event.kind if liquidity_event else condition_label}).")
    r_zone = (f"Retracement zone defined as {zone.kind} {zone.low:.6f}-{zone.high:.6f}; "
              f"{liquidity_event.note if liquidity_event else 'pattern compression boundary'}.")
    r_execution = "LTF validation pending — Stage 2 fires on micro-MSS / displacement / rejection inside zone."

    setup = Setup(
        id=new_setup_id(),
        symbol=symbol,
        base=base,
        tier=tier,
        direction=direction,
        timeframe=timeframe,
        condition=condition_label,
        zone_low=zone.low,
        zone_high=zone.high,
        zone_kind=zone.kind,
        sl=sl,
        key_level=zone.low if direction == "long" else zone.high,
        pattern_name=pattern_name,
        pattern_category=pattern_category,
        liquidity_event_kind=liquidity_event.kind if liquidity_event else None,
        liquidity_event_price=liquidity_event.level_price if liquidity_event else None,
        pending_zone_levels=pending_text,
        premium_discount_label=pd_label,
        premium_discount_pct=pd_pct,
        market_regime=asset_regime.label,
        btc_context=btc_reason,
        confidence=score,
        current_price=float(df["close"].iloc[-1]),
        exchange_id=exchange_id,
        setup_hash=setup_hash,
        candle_id=candle_id,
        consolidation_watch_flag=cons_watch is not None,
        created_at=_now_iso(),
        reason_structure=r_structure,
        reason_zone=r_zone,
        reason_execution=r_execution,
    )
    store.add(setup)
    title, body = format_stage1(setup)
    send_email(title, body)
    log.info("[%s %s] Stage 1 fired — %s", base, timeframe, title)
    return setup


def _try_fire_stage2(ex, setup: Setup, store: StateStore) -> None:
    """LTF validation on 30M/15M. If triggered, fire Stage 2 (with TP1/TP2/TP3
    and SL geometry re-validated)."""
    zone = Zone(low=setup.zone_low, high=setup.zone_high, kind=setup.zone_kind,
                direction="bullish" if setup.direction == "long" else "bearish")

    # HTF context for targets and tick
    try:
        df_htf = fetch_ohlcv(ex, setup.symbol, setup.timeframe, limit=200)
    except Exception:
        return
    latest_close = float(df_htf["close"].iloc[-1])

    # Tick state machine
    opposing = _opposing_structure(df_htf, setup.direction)
    new_state = tick_setup(setup, latest_close, opposing)
    setup.state = new_state

    if new_state in ("EXPIRED", "INVALIDATED"):
        store.archive(setup, new_state)
        log.info("[%s] setup %s -> %s", setup.base, setup.id, new_state)
        return

    if new_state != "AWAITING_LTF":
        store.update(setup)
        return

    for ltf in cfg.LTF_TIMEFRAMES:
        try:
            ltf_df = fetch_ohlcv(ex, setup.symbol, ltf, limit=80)
        except Exception:
            continue
        val = validate_ltf(ltf_df, setup.direction, zone)
        if not val.triggered:
            continue

        # Targets
        targets = compute_targets(
            df_htf, setup.direction,
            entry=(setup.zone_low + setup.zone_high) / 2,
            sl=setup.sl,
            untapped_targets=[],
        )
        if not targets:
            store.archive(setup, "INCOMPLETE_NO_TARGETS")
            log.warning("[%s] Stage 2 SUPPRESSED — INCOMPLETE SETUP (no targets)", setup.base)
            return

        # TP3 = next swing beyond TP2, simple proxy: TP2 + 1.5*(TP2-TP1)
        tp3 = targets.tp2 + (targets.tp2 - targets.tp1) * 1.5 if setup.direction == "long" else \
              targets.tp2 - (targets.tp1 - targets.tp2) * 1.5

        # SL must remain valid relative to entry zone
        if not is_sl_valid(setup.direction, setup.sl, setup.zone_low, setup.zone_high):
            store.archive(setup, "INVALID_SL_GEOM")
            log.warning("[%s] Stage 2 SUPPRESSED — INVALID SL GEOMETRY", setup.base)
            return

        # LTF confluence bonus (+1)
        if val.confluence_bonus:
            setup.confidence = min(10, setup.confidence + 1)

        # Final delivery gate
        if setup.confidence < cfg.MIN_SCORE_DELIVER:
            store.archive(setup, "BELOW_THRESHOLD")
            log.info("[%s] Stage 2 SUPPRESSED — final score %d < %d",
                     setup.base, setup.confidence, cfg.MIN_SCORE_DELIVER)
            return

        risk = abs(setup.zone_low + setup.zone_high) / 2 - setup.sl if setup.direction == "long" \
               else setup.sl - (setup.zone_low + setup.zone_high) / 2
        risk = abs(risk)
        rr_tp1 = abs(targets.tp1 - (setup.zone_low + setup.zone_high) / 2) / risk if risk > 0 else 0.0

        setup.stage2_fired_at = _now_iso()
        setup.ltf_trigger = f"{ltf}: {val.trigger_summary}"
        setup.entry_zone_low = setup.zone_low
        setup.entry_zone_high = setup.zone_high
        setup.tp1 = targets.tp1
        setup.tp2 = targets.tp2
        setup.tp3 = tp3
        setup.rr_to_tp1 = round(rr_tp1, 2)
        setup.rr_to_tp2 = targets.rr_to_tp2
        setup.current_price = latest_close
        setup.reason_execution = setup.ltf_trigger

        store.archive(setup, "EXECUTED")
        title, body = format_stage2(setup)
        send_email(title, body)
        log.info("[%s] Stage 2 fired — %s", setup.base, title)
        return


def _opposing_structure(df: pd.DataFrame, direction: str) -> bool:
    from .indicators import last_n_swings, swing_pivots
    pivots = swing_pivots(df)
    close = float(df["close"].iloc[-1])
    if direction == "long":
        last_low = last_n_swings(pivots, "low", 1)
        return bool(last_low) and close < last_low[-1].price
    else:
        last_high = last_n_swings(pivots, "high", 1)
        return bool(last_high) and close > last_high[-1].price


def _scan_asset(ex, base: str, btc_ctx: BTCContext, regime, btc_4h: pd.DataFrame,
                btc_1d: pd.DataFrame, store: StateStore,
                tier5_set: Optional[set] = None) -> None:
    symbol = normalize_symbol(ex, base)
    if not symbol:
        return

    tier = cfg.tier_of(base, tier5_set)
    if tier == 0:
        return

    # Tick existing setups first
    for s in list(store.for_asset(base)):
        try:
            _try_fire_stage2(ex, s, store)
        except Exception as e:
            log.debug("[%s] Stage 2 tick failed: %s", base, e)

    # Tier-4 own regime override
    asset_regime = regime
    if cfg.is_tier4(base):
        try:
            asset_daily = fetch_ohlcv(ex, symbol, "1d", limit=250)
            asset_regime = classify_regime_tier4(asset_daily)
            if volatility_suppress(asset_regime):
                log.warning("[%s] Tier 4 vol-suppress; skipping", base)
                return
        except Exception:
            pass

    # Per-TF HTF scan
    for tf in cfg.HTF_TIMEFRAMES:
        try:
            df = fetch_ohlcv(ex, symbol, tf, limit=300)
        except Exception as e:
            log.debug("ohlcv fail %s %s: %s", symbol, tf, e)
            continue
        if len(df) < 60:
            continue

        # Tier 5 per-asset extra filters (4H only — most representative)
        if tier == 5 and tf == "4h" and not _tier5_extra_filters_pass(df):
            return  # excluded entirely

        # News candle suppression (current TF)
        if is_news_candle(df):
            log.debug("[%s %s] suppressed — news candle", base, tf)
            continue

        # Detect B and D
        cond_b = detect_condition_b(df)
        cond_d_events = detect_condition_d(df)
        d_triggers = primary_triggers(cond_d_events)
        d_pending = pending_zones(cond_d_events)

        # RS for Tier 3
        rs_delta = 0
        if tier == 3:
            btc_same_tf = btc_1d if tf == "1d" else btc_4h
            rs_val = relative_strength(df, btc_same_tf, lookback=20)
            rs_delta = rs_score_delta(rs_val)

        # Tier 2 BTC alignment opposing → score -1 (does NOT suppress)
        tier2_btc_opposing = False
        if tier == 2:
            want = "bullish"  # placeholder — we will recompute below per direction
            # actually checked per fire below

        # ----- B trigger -----
        if cond_b:
            direction = cond_b.direction
            t2_opp = (tier == 2 and btc_ctx.htf_bias != ("bullish" if direction == "long" else "bearish"))
            trap_warn = bool(cond_b.trap and cond_b.trap.is_trap)

            # B+D confluence?
            bd_conf = any(e.direction == direction for e in d_triggers)

            _try_fire_stage1(
                store=store, base=base, symbol=symbol, tier=tier,
                direction=direction, timeframe=tf, df=df,
                zone=cond_b.zone,
                pattern_name=cond_b.name, pattern_category=cond_b.category,
                liquidity_event=next((e for e in d_triggers if e.direction == direction), None),
                pending_levels=d_pending,
                asset_regime=asset_regime, btc_reason=btc_ctx.summary,
                rs_delta=rs_delta, trap_warning=trap_warn,
                bd_confluence=bd_conf, tier2_btc_opposing=t2_opp,
                cons_watch=None, exchange_id=getattr(ex, "id", "unknown"),
                condition_label="B+D" if bd_conf else "B",
            )

        # ----- D triggers — pick best per direction to avoid spam -----
        # Priority: sweep_reclaim > external_sweep > sweep_accept
        priority = {"sweep_reclaim": 3, "external_sweep": 2, "sweep_accept": 1}
        best_long: Optional[LiquidityEvent] = None
        best_short: Optional[LiquidityEvent] = None
        for ev in d_triggers:
            if not ev.direction or not ev.zone:
                continue
            p = priority.get(ev.kind, 0)
            if ev.direction == "long":
                if best_long is None or p > priority.get(best_long.kind, 0):
                    best_long = ev
            else:
                if best_short is None or p > priority.get(best_short.kind, 0):
                    best_short = ev

        for ev in (best_long, best_short):
            if ev is None:
                continue
            direction = ev.direction
            t2_opp = (tier == 2 and btc_ctx.htf_bias != ("bullish" if direction == "long" else "bearish"))
            _try_fire_stage1(
                store=store, base=base, symbol=symbol, tier=tier,
                direction=direction, timeframe=tf, df=df,
                zone=ev.zone,
                pattern_name=None, pattern_category=None,
                liquidity_event=ev, pending_levels=d_pending,
                asset_regime=asset_regime, btc_reason=btc_ctx.summary,
                rs_delta=rs_delta, trap_warning=False,
                bd_confluence=False, tier2_btc_opposing=t2_opp,
                cons_watch=None, exchange_id=getattr(ex, "id", "unknown"),
                condition_label="D",
            )

    # ----- Consolidation Watch (Daily TF) -----
    if cfg.RUNTIME.enable_consolidation_watch:
        try:
            df_d = fetch_ohlcv(ex, symbol, "1d", limit=120)
            cw = consolidation_watch(df_d, base)
            if cw:
                title, body = format_consolidation_watch(cw, tier)
                send_email(title, body)
                log.info("[%s] Consolidation Watch alert", base)
        except Exception as e:
            log.debug("[%s] consolidation_watch failed: %s", base, e)

    # ----- Liquidity Approach Watch -----
    if cfg.RUNTIME.enable_liquidity_approach:
        try:
            df_4h_w = fetch_ohlcv(ex, symbol, "4h", limit=100)
            for ap in liquidity_approach_watch(df_4h_w, base):
                title, body = format_liquidity_approach_watch(ap, tier)
                send_email(title, body)
                log.info("[%s] Liquidity Approach Watch alert", base)
        except Exception as e:
            log.debug("[%s] liquidity_approach failed: %s", base, e)


def run_once() -> None:
    log.info("=== v7 scan start ===")
    try:
        ex = build_exchange()
    except Exception as e:
        log.error("exchange init failed: %s", e)
        return

    try:
        btc_4h = fetch_ohlcv(ex, cfg.BTC_SYMBOL, "4h", limit=300)
        btc_1d = fetch_ohlcv(ex, cfg.BTC_SYMBOL, "1d", limit=300)
    except Exception as e:
        log.error("BTC fetch failed: %s", e)
        return

    regime = classify_regime(btc_1d)
    log.info("BTC regime: %s (ADX=%.1f ATR=%.4f avg=%.4f)",
             regime.label, regime.adx_value, regime.atr_value, regime.atr_avg)
    crypto_suppressed = volatility_suppress(regime)
    if crypto_suppressed:
        log.warning("CRYPTO VOL SUPPRESSION — Tiers 1-3 paused; Tier 4 still scanned")

    btc_ctx = classify_btc_bias(btc_4h, btc_1d)
    log.info("BTC context: %s", btc_ctx.summary)

    store = StateStore()
    tier5_list = build_tier5_universe(ex)
    tier5_set = set(tier5_list)
    log.info("Tier 5 dynamic universe: %d symbols (cap %d, min $%.0fM vol)",
             len(tier5_list), cfg.TIER_5_MAX_SYMBOLS, cfg.UNIVERSE_MIN_VOLUME_USD / 1e6)

    universe = list(cfg.all_symbols()) + tier5_list
    for base in universe:
        if crypto_suppressed and not cfg.is_tier4(base):
            continue
        try:
            _scan_asset(ex, base, btc_ctx, regime, btc_4h, btc_1d, store, tier5_set)
        except Exception as e:
            log.warning("asset %s failed: %s", base, e)

    log.info("=== v7 scan done ===")


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
