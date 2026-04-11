"""Market data layer — OHLCV, funding rates, symbol universe."""
from __future__ import annotations

import logging
from typing import List

import ccxt
import pandas as pd

from .config import CONFIG

log = logging.getLogger(__name__)


def build_exchange() -> ccxt.Exchange:
    cls = getattr(ccxt, CONFIG.exchange_id)
    ex = cls({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.load_markets()
    return ex


def fetch_ohlcv(ex: ccxt.Exchange, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    """Return OHLCV as a DataFrame with a UTC DatetimeIndex."""
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df


def fetch_funding_rate(ex: ccxt.Exchange, symbol: str) -> float | None:
    """Return the current funding rate as a decimal (e.g. 0.0001 = 0.01%)."""
    try:
        fr = ex.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate")) if fr.get("fundingRate") is not None else None
    except Exception as e:
        log.debug("funding rate fetch failed for %s: %s", symbol, e)
        return None


def get_universe(ex: ccxt.Exchange) -> List[str]:
    """Core symbols + top-N by 24h quote volume among USDT perps."""
    core = [s for s in CONFIG.core_symbols if s in ex.markets]

    broader: List[str] = []
    if CONFIG.broader_top_n > 0:
        try:
            tickers = ex.fetch_tickers()
            rows = []
            for sym, t in tickers.items():
                m = ex.markets.get(sym)
                if not m or not m.get("swap") or m.get("quote") != "USDT":
                    continue
                qv = t.get("quoteVolume") or 0
                rows.append((sym, qv))
            rows.sort(key=lambda x: x[1], reverse=True)
            broader = [s for s, _ in rows[: CONFIG.broader_top_n]]
        except Exception as e:
            log.warning("broader universe fetch failed: %s", e)

    seen, out = set(), []
    for s in core + broader:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
