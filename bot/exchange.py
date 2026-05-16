"""Exchange factory with auto-fallback (Binance -> OKX -> KuCoin -> Bitget).

Why a factory: cloud IPs are blocked by some exchanges. At startup we try each
exchange in order, do a smoke-test fetch, and use the first one that succeeds.
On Oracle/GitHub-Actions IPs this will usually land on OKX or KuCoin; on a
residential IP it will use Binance.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import ccxt
import pandas as pd

from .config import RUNTIME

log = logging.getLogger(__name__)


def _build(ex_id: str) -> ccxt.Exchange:
    cls = getattr(ccxt, ex_id)
    ex = cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.load_markets()
    # smoke test: fetch a few BTC candles
    ex.fetch_ohlcv("BTC/USDT:USDT", timeframe="1h", limit=5)
    return ex


def build_exchange(preference: Optional[List[str]] = None) -> ccxt.Exchange:
    prefs = preference or RUNTIME.exchange_preference
    last_error: Optional[Exception] = None
    for ex_id in prefs:
        try:
            ex = _build(ex_id)
            log.info("exchange selected: %s", ex_id)
            return ex
        except Exception as e:
            log.warning("%s unavailable: %s", ex_id, str(e)[:120])
            last_error = e
    raise RuntimeError(f"no exchange available from {prefs}; last error: {last_error}")


def fetch_ohlcv(ex: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def fetch_funding_rate(ex: ccxt.Exchange, symbol: str) -> Optional[float]:
    try:
        fr = ex.fetch_funding_rate(symbol)
        v = fr.get("fundingRate")
        return float(v) if v is not None else None
    except Exception:
        return None


def normalize_symbol(ex: ccxt.Exchange, base: str) -> Optional[str]:
    """Resolve 'BTC' -> 'BTC/USDT:USDT' on the active exchange."""
    candidate = f"{base}/USDT:USDT"
    if candidate in ex.markets:
        return candidate
    # some exchanges format differently — scan
    for sym, m in ex.markets.items():
        if m.get("base") == base and m.get("quote") == "USDT" and m.get("swap"):
            return sym
    return None
