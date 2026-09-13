"""The two universes the search is scored on, and how deep the store is.

Measured 2026-09-11: the candle store held 19 of 19 discovery symbols at 4h
and 8 of 19 at 1h and 15m, because only the TRADED universe was ever fetched
at the finer frames. A search at 1h over 8 symbols cannot reach p<0.01 no
matter what it finds, so deepening is not an optimisation — it is the
difference between a horizon being searchable and not.

These lists live in the package rather than in `scripts/` because the
kernel's research thread reads them: importing a script module would make a
trading kernel depend on its working directory.
"""
from __future__ import annotations

import logging
import sqlite3
import time

from ..data.feed import DataFeed

log = logging.getLogger(__name__)

# The screen's two universes, verbatim from scripts/screen_mechanisms.py.
# The split rule is fixed there so it cannot be chosen to flatter a result;
# do not re-partition it here.
DISCOVERY = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
             "DOGE/USDT", "ADA/USDT", "LINK/USDT", "AVAX/USDT", "LTC/USDT",
             "1000PEPE/USDT", "APT/USDT", "BCH/USDT", "DASH/USDT",
             "FET/USDT", "INJ/USDT", "T/USDT", "WLD/USDT", "XMR/USDT"]
HELDOUT = ["UNI/USDT", "SUI/USDT", "TAO/USDT", "ZEC/USDT", "NEAR/USDT",
           "FIL/USDT", "AAVE/USDT", "HYPE/USDT", "TRUMP/USDT",
           "1000SHIB/USDT", "ARB/USDT", "CRV/USDT", "DOT/USDT", "ICP/USDT",
           "OP/USDT", "TRX/USDT", "XLM/USDT"]

#: ~5y at 4h, ~3y at 1h, ~1y at 15m — the store's standing policy
TARGETS = {"4h": 11000, "1h": 26000, "15m": 35000}

MIN_BARS = 500


class NoExchange:
    """A DataFeed built for research reads must never reach the network.

    Passing this rather than None stops DataFeed from constructing a ccxt
    client, and any accidental use of it raises here instead of quietly
    fetching.
    """

    def __getattr__(self, name):
        raise RuntimeError(f"research reads may not use the exchange "
                           f"(asked for {name!r})")


def coverage(tfs=None, feed=None) -> dict:
    """{tf: {symbol: bars}} for every symbol carrying at least MIN_BARS, or
    `{"skipped": "coverage_unavailable"}` when the store could not be read
    at all.

    COUNTED in SQL, not loaded: the research thread asks this on every step,
    and reading 36 full frames to learn their lengths would put seconds of
    pandas work on a kernel thread to answer a question SQLite answers in
    one pass.

    A locked store or a schema change must read as "we could not look",
    never as "nothing exists here" — the empty map used to mean the LATTER,
    and the caller then fell back to trusting every configured symbol was
    present, which is the flattering wrong answer in exactly the direction
    that disables `min_discovery_symbols` when it matters most. Only
    `sqlite3.Error` is swallowed into that signal; anything else is a real
    bug and must surface.
    """
    feed = feed or DataFeed(exchange=NoExchange())
    want = set(DISCOVERY + HELDOUT)
    out = {}
    for tf in (tfs or list(TARGETS)):
        try:
            rows = feed.db.execute(
                "SELECT symbol, COUNT(*) FROM candles WHERE tf=? "
                "GROUP BY symbol", (tf,)).fetchall()
        except sqlite3.Error as e:
            log.warning(f"research coverage unavailable for {tf}: {e}")
            return {"skipped": "coverage_unavailable"}
        out[tf] = {sym: int(n) for sym, n in rows
                   if sym in want and int(n) >= MIN_BARS}
    return out


def deepen(symbols, tfs=None, feed=None) -> dict:
    feed = feed or DataFeed()
    report = {}
    for tf in (tfs or list(TARGETS)):
        want = TARGETS[tf]
        for sym in symbols:
            have = feed.cached_ohlcv(sym, tf, limit=200000)
            before = 0 if have is None else len(have)
            if before >= want:
                report[f"{sym} {tf}"] = f"{before} (deep enough)"
                continue
            t0 = time.perf_counter()
            try:
                df = feed.fetch_ohlcv(sym, tf, limit=want)
            except Exception as e:                     # noqa: BLE001
                report[f"{sym} {tf}"] = f"FAILED: {e}"
                print(f"  {sym:14} {tf:4} FAILED: {e}", flush=True)
                continue
            n = 0 if df is None else len(df)
            report[f"{sym} {tf}"] = f"{before} -> {n}"
            print(f"  {sym:14} {tf:4} {before} -> {n} "
                  f"[{time.perf_counter() - t0:.0f}s]", flush=True)
    return report
