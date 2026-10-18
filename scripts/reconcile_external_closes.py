#!/usr/bin/env python3
"""Close journal trades that were closed on the venue by someone else.

Books the venue's own fills (realizedPnl less commission) for the recorded entry
order and the reduce fills that returned the position to zero; funding is kept
as separate evidence. Dry run unless --apply. Refuses anything ambiguous.

    ./venv/bin/python -m scripts.reconcile_external_closes --db data/luffy.db
    ./venv/bin/python -m scripts.reconcile_external_closes --db data/luffy.db --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.data.feed import make_exchange
from trader.engine.reconcile import reconcile_external_closes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--trade", action="append", default=[], help="limit to this trade id (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    args = ap.parse_args(argv)
    ex = make_exchange("futures")
    ex.load_markets()
    report = reconcile_external_closes(ex, Journal(args.db), apply=args.apply,
                                       trade_ids=set(args.trade) or None)
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    return 0 if all(r["status"] != "SKIPPED" for r in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
