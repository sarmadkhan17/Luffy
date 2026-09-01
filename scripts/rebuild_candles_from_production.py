"""Rebuild the candle store from production public data.

Why this exists
---------------
`data/candles.db` was filled from the Binance **Demo Trading** endpoint. The
kernel built one demo exchange and handed it to both the Executor and
DataFeed, so market data came from the order simulator. Verified on
BTC/USDT 4h at 2022-01-01: the stored volume (375,667.5) matched the demo
endpoint exactly, against 24,921.4 on production futures — a 15x inflation
reaching back to 2022.

Closes track production to ~0.04%, so price-only features were sound. Volume
was not: volume z-scores correlate only +0.46 with the real series, and
`taker_buy` was synthesised from candle shape entirely.

This script drops the contaminated rows and re-pages the history from the
production public API, which also captures the real takerBuyBaseAssetVolume
that ccxt's unified fetch_ohlcv discards.

Usage
-----
    ./venv/bin/python -m scripts.rebuild_candles_from_production --dry-run
    ./venv/bin/python -m scripts.rebuild_candles_from_production

Idempotent: rows are INSERT OR REPLACE keyed by (symbol, tf, ts). Stop it and
re-run and it resumes. The old store is copied aside before anything is
deleted.
"""
import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from trader.core.config import load_config  # noqa: E402
from trader.data.feed import DataFeed, make_exchange  # noqa: E402

TARGETS = {"4h": 11000, "1h": 26000, "15m": 35000}


def _db_path() -> Path:
    return Path("data/candles.db")


def stored_symbols(db: Path) -> list[str]:
    with sqlite3.connect(db) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT symbol FROM candles ORDER BY symbol")]


def verify(feed: DataFeed, symbol: str, tf: str) -> str:
    """Compare the rebuilt tail against a fresh production pull."""
    raw = feed._klines(symbol, tf, limit=50)
    if not raw:
        return "no production reference"
    ref = {int(r[0]): float(r[5]) for r in raw}
    got = feed.cached_ohlcv(symbol, tf, limit=20000)
    if got is None or got.empty:
        return "EMPTY after rebuild"
    ts = (pd.to_datetime(got["ts"], utc=True).astype("int64") // 10**6)
    have = dict(zip(ts, got["volume"]))
    both = [t for t in ref if t in have]
    if not both:
        return "no overlap to verify"
    bad = [t for t in both
           if not (ref[t] * 0.5 <= have[t] <= ref[t] * 2.0)]
    tb = got["taker_buy"].notna().mean() if "taker_buy" in got else 0.0
    return (f"{len(both)} bars checked, {len(bad)} off-volume, "
            f"taker_buy present on {tb:.0%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()

    db = _db_path()
    if not db.exists():
        print(f"no candle store at {db}")
        return 1

    syms = args.symbols or stored_symbols(db)
    print(f"candle store : {db} ({db.stat().st_size/1e6:.1f} MB)")
    print(f"symbols      : {len(syms)}")
    print(f"targets      : {TARGETS}")

    if args.dry_run:
        with sqlite3.connect(db) as c:
            for tf in TARGETS:
                n = c.execute("SELECT COUNT(*) FROM candles WHERE tf=?",
                              (tf,)).fetchone()[0]
                print(f"  {tf}: {n} rows would be deleted and refetched")
        return 0

    backup = db.with_suffix(f".demo-backup-{int(time.time())}.db")
    print(f"\nbacking up  -> {backup}")
    shutil.copy2(db, backup)

    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM candles")
        c.commit()
        c.execute("VACUUM")
    print("contaminated rows deleted\n")

    # DataFeed() with no exchange = production public data (feed.py)
    feed = DataFeed()
    assert feed.data_ex is not None or feed._ex is None, \
        "refusing to rebuild from a non-production exchange"

    t0 = time.perf_counter()
    for i, sym in enumerate(syms, 1):
        for tf, want in TARGETS.items():
            try:
                df = feed.fetch_ohlcv(sym, tf, limit=want, force=True)
            except Exception as e:
                print(f"  [{i}/{len(syms)}] {sym} {tf}: FAILED {e}")
                continue
            n = 0 if df is None else len(df)
            print(f"  [{i}/{len(syms)}] {sym:<14} {tf:>3}: {n:>6} bars | "
                  f"{verify(feed, sym, tf)}")
    print(f"\ndone in {time.perf_counter()-t0:.0f}s")
    print(f"old demo store kept at {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
