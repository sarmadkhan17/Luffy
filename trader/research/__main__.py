"""Look at the search from outside the kernel.

    python -m trader.research --status          what it has done so far
    python -m trader.research --top 20          the best discovery results
    python -m trader.research --measure --tf 4h measure that horizon now
    python -m trader.research --once            run exactly one batch

`--once` and `--measure` run the batch in THIS process's child, ignoring
`research.enabled`, so a horizon can be calibrated and timed before the
kernel is allowed to spend hours on it.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ..core.config import ROOT, load_config
from ..core.journal import Journal
from .ledger import Ledger


def _journal() -> Journal:
    # the same database the kernel and the dashboard open
    return Journal(str(ROOT / "data" / "luffy.db"))


def _status(led: Ledger, cfg: dict) -> None:
    c = led.counts()
    print(f"combinations evaluated : {c['combos']}")
    print(f"  by verdict           : {c['by_verdict']}")
    print(f"  by horizon           : {c['by_tf']}")
    print(f"  by parts             : {c['by_k']}")
    print(f"batches                : {c['batches']} "
          f"({c['failed_batches']} failed)")
    for tf in (cfg.get("research") or {}).get("horizons") or []:
        g = led.gauges(tf)
        usable = sum(1 for m in g.values() if m.get("usable"))
        s = led.slices(tf) or {}
        cut = s.get("cut_ms")
        print(f"\n[{tf}] gauges {usable}/{len(g)} usable · "
              f"discovery symbols {len(s.get('discovery') or {})} · "
              f"cut {cut}")
        for r in led.journal.query(
                "SELECT window, consistency_p, powered FROM "
                "research_controls WHERE tf=? ORDER BY window", (tf,)):
            mark = "powered" if r["powered"] else "UNDERPOWERED"
            p = r["consistency_p"]
            print(f"    control {r['window']:<24} "
                  f"p={p if p is None else round(p, 6)} — {mark}")


def _top(led: Ledger, cfg: dict, n: int) -> None:
    for tf in (cfg.get("research") or {}).get("horizons") or []:
        rows = led.rows(tf, limit=n)
        if not rows:
            continue
        print(f"\n[{tf}] best discovery results — DISCOVERY EVIDENCE ONLY, "
              f"which is a description of the markets it was found on")
        print(f"  {'p':>9} {'PF':>5} {'CAGR%':>7} {'k':>2} {'verdict':<9} "
              f"parts")
        for r in rows:
            p = r["consistency_p"]
            print(f"  {('%.1e' % p) if p is not None else 'n/a':>9} "
                  f"{(r['median_pf'] or 0):>5.2f} "
                  f"{(r['total_pct'] or 0):>7.1f} {r['k']:>2} "
                  f"{r['verdict'] or '':<9} "
                  f"{','.join(json.loads(r['parts'] or '[]'))}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="trader.research")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--tf", default="")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    if args.tf:
        cfg.setdefault("research", {})["horizons"] = [args.tf]
    j = _journal()
    led = Ledger(j)
    led.ensure()

    if args.once or args.measure:
        from .runner import ResearchRunner
        cfg.setdefault("research", {})["enabled"] = True
        if args.measure:
            # drop the horizon's measurements so it is re-measured now
            for tf in cfg["research"].get("horizons") or []:
                with j._tx() as c:
                    c.execute("DELETE FROM research_gauges WHERE tf=?", (tf,))
        rep = ResearchRunner(j, cfg).step()
        print(json.dumps(rep, indent=2, default=str))
        return 0
    if args.top:
        _top(led, cfg, args.top)
        return 0
    _status(led, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
