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
                "SELECT window, consistency_p, powered, status FROM "
                "research_controls WHERE tf=? ORDER BY window", (tf,)):
            status = r.get("status") or "measured"
            if status != "measured":
                mark = f"COULD NOT CALIBRATE ({status})"
            elif r["powered"]:
                mark = "powered"
            else:
                mark = "UNDERPOWERED"
            p = r["consistency_p"]
            print(f"    control {r['window']:<24} "
                  f"p={p if p is None else round(p, 6)} — {mark}")
    _referee_status(led, cfg)


def _referee_status(led: Ledger, cfg: dict) -> None:
    from . import portfolio_null as pn
    r = cfg.get("research") or {}
    tests = led.tests()
    t, alpha = led.next_alpha(float(r.get("fdr_target", 0.10)),
                              float(r.get("lord_w0", 0.05)))
    cap = int(r.get("referee_max_draws", 19999))
    reachable = pn.draws_for(alpha, cap) is not None
    print(f"\nreferee                : {'ON' if r.get('referee') else 'OFF'}"
          f" · handoff {'OPEN' if r.get('handoff') else 'closed'}")
    print(f"  held-out looks spent : {len(tests)} "
          f"({sum(1 for x in tests if x['rejected'])} rejected the null)")
    print(f"  next look            : test {t} at alpha={alpha:.2e} — "
          + ("resolvable" if reachable else
             f"BUDGET CANNOT RESOLVE IT at {cap} draws; looks defer"))
    by = {}
    for row in led.candidates():
        by[row["state"]] = by.get(row["state"], 0) + 1
    print(f"  candidates by state  : {by or 'none yet'}")


def _top(led: Ledger, cfg: dict, n: int) -> None:
    horizons = (cfg.get("research") or {}).get("horizons") or []
    printed_any = False
    for tf in horizons:
        rows = led.rows(tf, limit=n)
        if not rows:
            continue
        printed_any = True
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
    if not printed_any:
        print(f"no discovery results yet for any of: "
              f"{', '.join(horizons) if horizons else '(no horizons configured)'}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="trader.research")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--rekey", action="store_true",
                    help="archive this horizon's research_combos/"
                         "research_controls rows before --measure "
                         "re-points its thresholds — required once the "
                         "ledger already holds rows scored under them")
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
            # Thresholds are frozen once measured, deliberately: a stored
            # hash carries a percentile RANK, never its value, so silently
            # re-measuring would re-point every existing hash at different
            # numbers while `Ledger.known()` still treats it as "already
            # evaluated" under the OLD ones — a ledger of mixed vintages
            # under stable identities. Refuse unless the horizon's rows are
            # archived aside first.
            for tf in cfg["research"].get("horizons") or []:
                n = led.combos_exist(tf)
                if n and not args.rekey:
                    print(f"refusing to re-measure {tf}: research_combos "
                          f"already holds {n} row(s) scored under its "
                          f"current thresholds. Re-measuring would change "
                          f"what those stored hashes mean without changing "
                          f"the hashes themselves. Pass --rekey to archive "
                          f"them first.")
                    return 1
                if n:
                    from ..core.types import now_utc
                    moved = led.archive_horizon(tf, now_utc().isoformat())
                    print(f"archived {moved['combos']} combo(s) and "
                          f"{moved['controls']} control row(s) for {tf} "
                          f"before re-measuring")
                with j._tx() as c:
                    c.execute("DELETE FROM research_gauges WHERE tf=?", (tf,))
        rep = ResearchRunner(j, cfg).step()
        print(json.dumps(rep, indent=2, default=str))
        return 1 if rep.get("ok") is False else 0
    if args.top:
        _top(led, cfg, args.top)
        return 0
    _status(led, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
