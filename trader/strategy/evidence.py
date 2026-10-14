"""Legacy 15m candle-loading compatibility for offline Genome tools.

Defaults and frames remain shared with spec loading and parity checks.
Current strategy admission belongs to Analyst.admit; the obsolete Genome
prescreen and gauntlet gates are retired.
"""
from __future__ import annotations

import logging

import pandas as pd

from .backtest import resample

log = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT"]


def _scfg(cfg: dict) -> dict:
    return cfg.get("strategies", {}) or {}


def backtest_symbols(cfg: dict) -> list[str]:
    return list(_scfg(cfg).get("backtest_symbols") or DEFAULT_SYMBOLS)


def backtest_bars(cfg: dict) -> int:
    """0/absent means 'everything cached' — we pass a large limit and let the
    feed return what it has, instead of the old hardcoded 30-day window."""
    n = int(_scfg(cfg).get("backtest_bars", 0) or 0)
    return n if n > 0 else 20000


def _load_one(feed, symbol: str, limit: int):
    """Cache-first candle load for backtesting.

    The candle store is the right source here: it already holds ~87 days of
    15m bars, and a gauntlet must not spend minutes paging the REST API. Only
    retained bar-cut receipts establish replay eligibility; a cold or
    unqualified store returns unavailable rather than fetching latest data.
    """
    df = None
    try:
        if hasattr(feed, "cached_ohlcv"):
            df = feed.cached_ohlcv(symbol, "15m", limit=limit)
    except Exception as e:
        log.warning(f"evidence: store read {symbol} failed: {e}")
    if df is not None and len(df) >= 400:
        return df
    # A late acquisition is not evidence available to earlier simulated
    # decisions. Collection is an explicit separate operation.
    return None


def load_frames(feed, cfg: dict, symbols: list[str] | None = None) -> dict:
    """{symbol: 15m DataFrame} plus a shared 'BTC_1h' context frame.

    BTC 1h is RESAMPLED from BTC 15m rather than fetched, because
    candles.db holds far more 15m history (~87d) than 1h (~21d).
    """
    syms = symbols or backtest_symbols(cfg)
    limit = backtest_bars(cfg)
    out: dict[str, pd.DataFrame] = {}
    for sym in syms:
        df = _load_one(feed, sym, limit)
        if df is not None and len(df) >= 400:
            out[sym] = df
    btc = out.get("BTC/USDT")          # never `or` a DataFrame — ambiguous truth
    if btc is None:
        btc = _load_one(feed, "BTC/USDT", limit)
    out["_btc_1h"] = resample(btc, "1h") if btc is not None and len(btc) else None
    return out
