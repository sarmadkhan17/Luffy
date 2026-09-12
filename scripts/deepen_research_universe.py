"""Bring 1h and 15m up to the universes the search is scored on.

Idempotent: `DataFeed.fetch_ohlcv` merges INSERT OR REPLACE keyed by
(symbol, tf, ts) and records a history floor, so re-running tops up rather
than re-walking.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.data.feed import DataFeed                        # noqa: E402
from trader.research.universe import (DISCOVERY, HELDOUT,    # noqa: E402
                                      TARGETS, coverage, deepen)

if __name__ == "__main__":
    tfs = [a for a in sys.argv[1:] if a in TARGETS] or ["1h", "15m"]
    feed = DataFeed()
    print(f"deepening {len(DISCOVERY + HELDOUT)} symbols at {tfs}")
    deepen(DISCOVERY + HELDOUT, tfs, feed)
    print("\ncoverage (symbols with >=500 bars):")
    for tf, have in coverage(tfs, feed).items():
        d = sum(1 for s in DISCOVERY if s in have)
        h = sum(1 for s in HELDOUT if s in have)
        print(f"  {tf}: {d}/{len(DISCOVERY)} discovery, "
              f"{h}/{len(HELDOUT)} held-out")
