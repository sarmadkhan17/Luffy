#!/usr/bin/env python3
"""Set every trade's realized P&L to the venue's own figure.

The journal computed P&L from what Luffy asked for rather than what the
venue did, and three faults compounded into the number the Analyst selects
on:

  * `close_trade` overwrote `realized_pnl` instead of adding to it, so every
    trade that took its 1.5R partial recorded only the final leg;
  * `close()` priced that leg over the whole journalled amount, while
    reduce-only had filled whatever the venue still held;
  * amounts were never quantized to the lot step, so the journalled size
    drifted from the venue's on every leg.

All three are fixed forward. This repairs the record they left behind.
Binance reports `realizedPnl` and `commission` per fill, so the truth is one
REST call per symbol-window away — no reconstruction, no guessing.

    ./venv/bin/python -m scripts.backfill_realized_pnl            # dry run
    ./venv/bin/python -m scripts.backfill_realized_pnl --apply

Trade windows are disjoint per symbol (RiskManager refuses a second position
in a symbol it is already exposed to), so [opened_at, closed_at] attributes
each fill to exactly one trade.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import ROOT
from trader.core.journal import Journal
from trader.data.feed import make_exchange

MARGIN = timedelta(minutes=2)     # a fill lands just before close_trade stamps


def _ms(iso: str, offset: timedelta) -> int:
    return int((datetime.fromisoformat(iso) + offset).timestamp() * 1000)


def venue_pnl(ex, symbol: str, opened_at: str, closed_at: str | None):
    """Realized P&L net of commission over this trade's window, or None."""
    try:
        since = _ms(opened_at, -MARGIN)
    except Exception:
        return None
    until = None
    if closed_at:
        try:
            until = _ms(closed_at, MARGIN)
        except Exception:
            pass
    try:
        fills = ex.fetch_my_trades(symbol, since=since, limit=1000)
    except Exception as e:
        print(f"  ! {symbol}: fill history unavailable ({e})")
        return None
    total, n = 0.0, 0
    for f in fills:
        if until is not None and f["timestamp"] > until:
            continue
        i = f.get("info") or {}
        try:
            total += float(i.get("realizedPnl") or 0) - float(
                i.get("commission") or 0)
        except (TypeError, ValueError):
            continue
        n += 1
    return None if n == 0 else total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the corrections (default is a dry run)")
    ap.add_argument("--tolerance", type=float, default=0.01,
                    help="ignore differences smaller than this, in USDT")
    args = ap.parse_args()

    j = Journal(ROOT / "data" / "luffy.db")
    ex = make_exchange("futures")
    ex.load_markets()

    trades = j.query("SELECT * FROM trades WHERE market_type='futures' "
                     "ORDER BY opened_at")
    fixed = skipped = unchanged = 0
    delta_total = 0.0
    for t in trades:
        was = float(t["realized_pnl"] or 0)
        truth = venue_pnl(ex, t["symbol"], t["opened_at"], t["closed_at"])
        if truth is None:
            skipped += 1
            continue
        if abs(truth - was) <= args.tolerance:
            unchanged += 1
            continue
        delta_total += truth - was
        flag = "partial" if t["tp1_done"] else "       "
        print(f"  {t['symbol']:<12} {t['status']:<6} {flag} "
              f"{was:>10.2f} → {truth:>10.2f}  ({truth - was:+.2f})")
        if args.apply:
            with j._tx() as c:
                c.execute("UPDATE trades SET realized_pnl=? WHERE id=?",
                          (round(truth, 8), t["id"]))
        fixed += 1

    verb = "corrected" if args.apply else "would correct"
    print(f"\n{verb} {fixed} · unchanged {unchanged} · no venue history "
          f"{skipped} · net {delta_total:+.2f} USDT")
    if fixed and not args.apply:
        print("dry run — re-run with --apply to write")
    if args.apply and fixed:
        j.log_control_event("pnl_backfill", "operator",
                            detail=f"{fixed} trades, net {delta_total:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
