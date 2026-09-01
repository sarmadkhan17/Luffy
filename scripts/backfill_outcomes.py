"""Grade the near-threshold HOLDs the live sampler discarded.

Run explicitly — this is NOT wired into the kernel. It changes what the
learning loop sees (calibration, online expert weights, the meta-label
model), and that eventually moves position sizing, so it should be a
deliberate act with a visible before/after rather than a background job.

    ./venv/bin/python scripts/backfill_outcomes.py --dry-run
    ./venv/bin/python scripts/backfill_outcomes.py

Candles come from data/candles.db, which already covers the decision window.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import ROOT
from trader.core.journal import Journal
from trader.engine.outcome_backfill import LEAN_FRAC, backfill_holds, candidates


def load_frames(symbols: list[str], tf: str = "15m") -> dict:
    """{symbol: ohlcv frame} straight from the persistent candle cache."""
    con = sqlite3.connect(ROOT / "data" / "candles.db")
    out = {}
    for sym in symbols:
        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND tf=? ORDER BY ts", con, params=(sym, tf))
        if df.empty:
            continue
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        out[sym] = df
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lean-frac", type=float, default=LEAN_FRAC,
                    help="|score| >= this * threshold counts as a lean")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    j = Journal(ROOT / "data" / "luffy.db")
    before = j.query("SELECT COUNT(*) n FROM outcomes "
                     "WHERE correct_4h IS NOT NULL")[0]["n"]
    cands = candidates(j, a.lean_frac, a.limit)
    symbols = sorted({c["symbol"] for c in cands})
    print(f"graded outcomes now : {before}")
    print(f"candidate HOLDs     : {len(cands)} across {len(symbols)} symbols")

    if a.dry_run:
        print("\n(dry run — nothing written)")
        return
    if not cands:
        return

    frames = load_frames(symbols)
    missing = [s for s in symbols if s not in frames]
    if missing:
        print(f"no candles for      : {', '.join(missing)}")
    n = backfill_holds(j, frames, lean_frac=a.lean_frac, limit=a.limit)
    after = j.query("SELECT COUNT(*) n FROM outcomes "
                    "WHERE correct_4h IS NOT NULL")[0]["n"]
    hit = j.query("SELECT ROUND(AVG(correct_4h), 4) r FROM outcomes "
                  "WHERE correct_4h IS NOT NULL")[0]["r"]
    print(f"\nwritten             : {n}")
    print(f"graded outcomes now : {after}  ({after / max(before, 1):.1f}x)")
    print(f"pooled 4h hit rate  : {hit}")


if __name__ == "__main__":
    main()
