"""Push analyst-designed genomes through the full dual gauntlet.
Acceptance deploys to the paper population exactly like the harvester."""

import json
import sys
import time

from trader.brain.harvester import Harvester
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.data.feed import DataFeed
from trader.strategy.genome import Genome


def tv_used_today(journal) -> int:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return journal.query(
        "SELECT COUNT(*) n FROM brain_events WHERE kind='tv_backtest' "
        "AND ts >= ?", (today,))[0]["n"]


CANDIDATES = json.load(open("data/analyst_candidates.json"))

META = {
    "ema_trend": ("Analyst: EMA-stack pullback", 
                  "Trend persistence: aligned EMA stacks signal institutional "
                  "flow direction; shallow pullbacks within strength offer "
                  "entry before continuation."),
    "breakout_retest": ("Analyst: volume breakout retest",
                        "Range breaks on volume mark initiative buying; the "
                        "retest confirms breakout validity before expansion."),
}


def main() -> None:
    cfg = load_config()
    j = Journal("data/luffy.db")
    hv = Harvester(j, cfg, DataFeed())
    budget = int(cfg.get("tv_harness", {}).get("daily_runs", 20))

    for c in CANDIDATES[:4]:
        fam = c["family"]
        if fam not in META:
            continue
        if budget - tv_used_today(j) < 5:
            print(f"STOP: TV budget exhausted ({tv_used_today(j)}/{budget})",
                  flush=True)
            return
        title, hyp = META[fam]
        g = Genome(
            strategy_id=f"ana_{fam}_{int(time.time()) % 100000}",
            family=fam, hypothesis=hyp,
            invalidation="Demote on PF<0.85 over 20 trades or 6 straight losses.",
            regime_filter=frozenset({"TRENDING_UP", "TRENDING_DOWN",
                                     "RANGING", "VOLATILE"}),
            markets=frozenset({"futures"}), params=c["params"])
        print(f"\n=== GAUNTLET {g.strategy_id} ({fam}) {c['params']} ===",
              flush=True)
        ok, ev = hv._dual_gauntlet(g)
        stage = ev.get("stage")
        tv_ev = ev.get("tv") or {}
        print(f"stage={stage} accepted={ok}", flush=True)
        if stage == "real_tv":
            checks = tv_ev.get("checks", {})
            print(f"  checks: {json.dumps(checks)}", flush=True)
            print(f"  pnl%={tv_ev.get('net_profit_pct')} dd%="
                  f"{tv_ev.get('max_dd')} folds+={tv_ev.get('positive_folds')}"
                  f"/{tv_ev.get('fold_count')}", flush=True)
        else:
            print(f"  evidence: {json.dumps(ev)[:220]}", flush=True)
        if ok:
            idea = {"idea_id": f"analyst_{int(time.time())}",
                    "source": "in-house analyst grid",
                    "title": title}
            hv._deploy(g, idea, ev)
            j.log_brain_event("harvest_accepted", g.strategy_id,
                              {"source": "analyst", "title": title,
                               "family": fam, "params": c["params"],
                               **ev})
            print(f"*** DEPLOYED {g.strategy_id} to paper ***", flush=True)
            return                      # one acceptance ends the session
        time.sleep(10)
    print("session done: no acceptance", flush=True)


if __name__ == "__main__":
    sys.exit(main())
