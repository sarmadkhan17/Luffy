"""Find and repair bars the store froze while they were still forming.

`DataFeed` extended the candle store with `since = last + tf_ms`, one bar
past the newest row it held — but the row it held had been written while
that bar was still open. The one bar that could be wrong was the one bar
never requested again, so each fetch laid down another permanently frozen
partial candle.

A frozen bar is easy to recognise once you look: the open is exact (it is
known the instant the bar opens) while high/low/close are truncated to
whatever traded before the snapshot, and the volume is a fraction of the
bar's real volume. Measured 2026-09-02, 104 of 123 (symbol, timeframe)
tails disagreed with the venue, up to four 4h bars deep.

`feed.py` no longer writes an unclosed bar and now re-reads the tail on
every incremental fetch, so this repairs history rather than preventing it.

    ./venv/bin/python scripts/repair_partial_bars.py --check   # report only
    ./venv/bin/python scripts/repair_partial_bars.py           # repair
"""
from __future__ import annotations

import argparse
import sqlite3
import time
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import norm_symbol          # noqa: E402
from trader.data.feed import DataFeed, make_exchange  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "candles.db"
#: how far back from each tail to check. The freeze accumulates one bar per
#: fetch, so it is bounded by how many fetches ran since the last full
#: rebuild — generous here, it costs one request either way.
DEPTH = 40
#: a bar matches if every price is within this of the venue's
TOL = 1e-3


def _venue_bars(feed: DataFeed, symbol: str, tf: str, since: int) -> dict:
    raw = feed._klines(symbol, tf, since=since, limit=1000)
    out = {}
    for r in raw or []:
        try:
            out[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]),
                              float(r[4]), float(r[5]),
                              float(r[9]) if len(r) > 9 else float("nan"))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report what is stale, change nothing")
    ap.add_argument("--depth", type=int, default=DEPTH)
    args = ap.parse_args()

    feed = DataFeed(exchange=make_exchange("futures", demo=False,
                                           with_keys=False))
    db = sqlite3.connect(DB)
    pairs = db.execute("SELECT symbol, tf, COUNT(*) FROM candles "
                       "GROUP BY symbol, tf ORDER BY tf, symbol").fetchall()

    # A bar that has not closed does not belong on disk at all. Rewriting it
    # from the venue would just store a fresher partial; the store holds
    # closed bars and `feed.py` now keeps it that way.
    now_ms = int(time.time() * 1000)
    purged = 0
    for symbol, tf, _n in pairs:
        tf_ms = DataFeed._TF_MS.get(tf)
        if not tf_ms:
            continue
        cur = db.execute(
            "DELETE FROM candles WHERE symbol=? AND tf=? AND ts > ?",
            (symbol, tf, now_ms - tf_ms))
        purged += cur.rowcount
    db.commit()
    if purged:
        print(f"purged {purged} unclosed bar(s) from the store\n")

    total_bad = total_fixed = 0
    unreadable: list[str] = []
    for symbol, tf, _n in pairs:
        tf_ms = DataFeed._TF_MS.get(tf)
        if not tf_ms:
            continue
        rows = db.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
            (symbol, tf, args.depth)).fetchall()
        if not rows:
            continue
        since = min(r[0] for r in rows)
        try:
            venue = _venue_bars(feed, symbol, tf, since)
        except Exception as e:
            unreadable.append(f"{symbol} {tf}: {str(e)[:80]}")
            continue
        if not venue:
            unreadable.append(f"{symbol} {tf}: venue returned nothing")
            continue

        bad = []
        for ts, o, h, l, c, v in rows:
            b = venue.get(int(ts))
            if b is None:
                continue
            if any(abs(a - x) > TOL * max(abs(x), 1e-9)
                   for a, x in ((o, b[0]), (h, b[1]), (l, b[2]), (c, b[3]))):
                bad.append((int(ts), c, b))
        if not bad:
            continue
        total_bad += len(bad)
        worst = max(bad, key=lambda r: abs(r[1] - r[2][3]) / max(r[2][3], 1e-9))
        drift = (worst[2][3] / worst[1] - 1) * 100 if worst[1] else float("nan")
        print(f"  {tf:4} {symbol:22} {len(bad):3} stale  worst close "
              f"{worst[1]:.6g} → {worst[2][3]:.6g} ({drift:+.2f}%)")

        if args.check:
            continue
        # only rewrite bars that have actually closed
        db.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
            [(norm_symbol(symbol), tf, ts, b[0], b[1], b[2], b[3], b[4],
              None if b[5] != b[5] else b[5])
             for ts, _c, b in bad if ts + tf_ms <= now_ms])
        db.commit()
        total_fixed += len(bad)

    print(f"\n{len(pairs)} (symbol, timeframe) series checked to depth "
          f"{args.depth}")
    print(f"{total_bad} stale bars found"
          + ("" if args.check else f", {total_fixed} rewritten from the venue"))
    if unreadable:
        print(f"{len(unreadable)} series could not be read:")
        for u in unreadable[:10]:
            print(f"  {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
