"""Entrypoint — supports `--once` (GitHub Actions) and `--loop` (VPS/Oracle).

Usage:
    python -m src.main --once     # single scan cycle, then exit
    python -m src.main --loop     # long-running scheduler
    python -m src.main            # honours RUN_MODE env var
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import List

from .config import CONFIG
from .data import build_exchange, fetch_funding_rate, fetch_ohlcv, get_universe
from .formatter import format_signal
from .news import fetch_news, summarize
from .notifiers import get_notifier
from .signals import Signal, analyze
from .state import mark_sent, should_send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("scanner")

TIMEFRAMES = ["1h", "4h"]


def _base_currency(symbol: str) -> str:
    # "BTC/USDT:USDT" -> "BTC"
    return symbol.split("/")[0]


def scan_once() -> None:
    log.info("=== scan start ===")
    try:
        ex = build_exchange()
    except Exception as e:
        log.error("exchange init failed: %s", e)
        return

    universe = get_universe(ex)
    log.info("universe: %d symbols", len(universe))

    notifier = get_notifier()
    signals: List[Signal] = []

    for symbol in universe:
        try:
            funding = fetch_funding_rate(ex, symbol)
            for tf in TIMEFRAMES:
                try:
                    df = fetch_ohlcv(ex, symbol, tf, limit=300)
                except Exception as e:
                    log.debug("ohlcv fail %s %s: %s", symbol, tf, e)
                    continue
                sig = analyze(symbol, tf, df, funding)
                if sig:
                    signals.append(sig)
        except Exception as e:
            log.warning("symbol %s failed: %s", symbol, e)

    if not signals:
        log.info("No high-probability condition detected.")
        return

    log.info("%d raw signals — filtering dedupe", len(signals))
    sent = 0
    for sig in signals:
        key = sig.dedupe_key()
        if not should_send(key):
            continue
        # Attach news only for ones we're actually sending (saves API calls)
        posts = fetch_news(_base_currency(sig.symbol))
        sig.news_context = summarize(posts)
        title, body = format_signal(sig)
        if notifier.send(title, body):
            mark_sent(key)
            sent += 1
    log.info("=== scan done — sent %d signals ===", sent)


def run_loop() -> None:
    interval = CONFIG.loop_interval_min * 60
    log.info("starting loop mode, interval=%ds", interval)
    while True:
        start = time.time()
        try:
            scan_once()
        except Exception as e:
            log.exception("scan crashed: %s", e)
        elapsed = time.time() - start
        sleep_for = max(10, interval - elapsed)
        log.info("sleeping %.0fs", sleep_for)
        time.sleep(sleep_for)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one scan cycle and exit")
    parser.add_argument("--loop", action="store_true", help="run forever with APScheduler-like loop")
    args = parser.parse_args()

    mode = "once" if args.once else ("loop" if args.loop else CONFIG.run_mode)
    if mode == "loop":
        run_loop()
    else:
        scan_once()


if __name__ == "__main__":
    main()
