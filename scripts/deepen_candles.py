"""Deepen the local candle cache.

Principle: when a backtest is underpowered, get more data — never relax the
test to fit what happens to be on hand.

candles.db held 15m only, ~208 days. That is ~500 4h bars, which after a 210
bar warmup and a 70/30 split leaves nothing to test on, so every
higher-timeframe result was small-sample noise. Binance serves years on the
1h and 4h endpoints; this pages them in.

Idempotent: rows are INSERT OR REPLACE keyed by (symbol, tf, ts), so it can
be re-run to top up.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from trader.core.config import load_config  # noqa: E402
from trader.data.feed import DataFeed  # noqa: E402

# ~3 years on 1h, ~5 on 4h, ~1 on 15m — enough that a 70/30 walk-forward on
# 4h still leaves hundreds of out-of-sample bars after warmup.
TARGETS = {"4h": 11000, "1h": 26000, "15m": 35000}


def deepen(symbols, targets=None, feed=None) -> dict:
    feed = feed or DataFeed()
    targets = targets or TARGETS
    report = {}
    for sym in symbols:
        for tf, want in targets.items():
            have = feed.cached_ohlcv(sym, tf, limit=200000)
            n_before = 0 if have is None else len(have)
            if n_before >= want:
                report[f"{sym} {tf}"] = f"{n_before} (already deep enough)"
                continue
            t0 = time.perf_counter()
            try:
                df = feed.fetch_ohlcv(sym, tf, limit=want)
            except Exception as e:
                report[f"{sym} {tf}"] = f"FAILED: {e}"
                continue
            n = 0 if df is None else len(df)
            span = ""
            if n:
                ts = pd.to_datetime(df["ts"], utc=True)
                span = f" {ts.min().date()}->{ts.max().date()} " \
                       f"({(ts.max() - ts.min()).days}d)"
            report[f"{sym} {tf}"] = (f"{n_before} -> {n}{span} "
                                     f"[{time.perf_counter() - t0:.0f}s]")
            print(f"  {sym:12} {tf:4} {report[f'{sym} {tf}']}", flush=True)
    return report


if __name__ == "__main__":
    cfg = load_config()
    from trader.strategy.evidence import backtest_symbols
    syms = sys.argv[1:] or backtest_symbols(cfg)
    print(f"deepening {syms} to {TARGETS}\n", flush=True)
    deepen(syms)
    print("\n=== final depth ===", flush=True)
    import sqlite3
    c = sqlite3.connect("data/candles.db")
    for sym, tf, n, lo, hi in c.execute(
            "SELECT symbol, tf, COUNT(*), MIN(ts), MAX(ts) FROM candles "
            "WHERE tf IN ('15m','1h','4h') GROUP BY symbol, tf "
            "ORDER BY symbol, tf"):
        lo = pd.to_datetime(lo, unit="ms", utc=True)
        hi = pd.to_datetime(hi, unit="ms", utc=True)
        print(f"{sym:12} {tf:4} n={n:6d} span={(hi - lo).days:5d}d  "
              f"{lo.date()} -> {hi.date()}")
