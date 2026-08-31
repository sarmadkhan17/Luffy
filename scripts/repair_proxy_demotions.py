"""Undo demotions that were decided by the params-blind TradingView proxy.

`trader/brain/tv.py:validate_population` used to demote a strategy when
`TVClient.walk_forward(symbol, family)` failed. That call maps a FAMILY to a
stock TradingView strategy (ema_trend->ema_cross, vwap_fade->bollinger,
sweep_reversal->rsi, ...) and backtests it on Yahoo BTC-USD. It never
receives the genome's params, so its verdict is identical for every strategy
of a family and says nothing about any individual genome.

Those rows are therefore demoted on evidence that does not exist. This
restores them to PAPER — not ACTIVE: they must re-earn promotion through
realized trades (strategy/promotion.py) like anything else.

Dry-run by default. Pass --apply to write.

    ./venv/bin/python scripts/repair_proxy_demotions.py
    ./venv/bin/python scripts/repair_proxy_demotions.py --apply
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal

DB = Path(__file__).resolve().parents[1] / "data" / "luffy.db"
MARKER = "TV validation:"


def main() -> int:
    apply = "--apply" in sys.argv
    j = Journal(str(DB))
    rows = j.query(
        "SELECT id,name,kind,state,retire_reason FROM strategies "
        "WHERE state='demoted' AND retire_reason LIKE ?", (MARKER + "%",))

    if not rows:
        print("nothing to repair — no proxy-demoted strategies found")
        return 0

    print(f"{'ID':22} {'NAME':30} {'FAMILY':18} STATE")
    for r in rows:
        print(f"{r['id'][:22]:22} {r['name'][:30]:30} {r['kind']:18} "
              f"{r['state']} -> paper")
    print(f"\n{len(rows)} strategy(ies) demoted by the family proxy.")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to restore.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        j.query(
            "UPDATE strategies SET state='paper', state_changed_at=?, "
            "retire_reason=? WHERE id=?",
            (now,
             f"restored from proxy demotion (was: {r['retire_reason'][:120]})",
             r["id"]))
        j.log_brain_event("proxy_demotion_reverted", r["id"], {
            "family": r["kind"],
            "previous_reason": r["retire_reason"][:300],
            "note": "demoted by params-blind Yahoo family proxy; "
                    "must re-earn promotion via realized trades"})
    print(f"\nrestored {len(rows)} strategy(ies) to PAPER probation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
