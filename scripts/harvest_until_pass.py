"""Drive harvester cycles back-to-back until a strategy passes the full
dual gauntlet (or the TV budget runs dry). Kernel's own 4h thread shares
the same journal-accounted budget, so overlap is safe."""

import sys
import time

from trader.brain.harvester import Harvester
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.data.feed import DataFeed


def tv_used_today(journal) -> int:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return journal.query(
        "SELECT COUNT(*) n FROM brain_events WHERE kind='tv_backtest' "
        "AND ts >= ?", (today,))[0]["n"]


def main() -> None:
    cfg = load_config()
    j = Journal("data/luffy.db")
    hv = Harvester(j, cfg, DataFeed())
    budget = int(cfg.get("tv_harness", {}).get("daily_runs", 20))
    max_cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 12

    for cycle in range(1, max_cycles + 1):
        used = tv_used_today(j)
        left = budget - used
        print(f"[cycle {cycle}] tv runs used {used}/{budget}", flush=True)
        if left < 5:                       # keep margin for a full gauntlet
            print("STOP: TV budget nearly exhausted", flush=True)
            return
        try:
            stats = hv.harvest_once()
        except Exception as e:
            print(f"[cycle {cycle}] harvest error: {e}", flush=True)
            time.sleep(120)
            continue
        print(f"[cycle {cycle}] {stats}", flush=True)
        if stats.get("accepted", 0) > 0:
            print("PASS: strategy accepted — deployed to paper", flush=True)
            return
        if stats.get("extracted", 0) == 0:
            # pool drained; wait for crawlers/sources to refresh
            print("no fresh extractable ideas — cooling down 10 min",
                  flush=True)
            time.sleep(600)
        else:
            time.sleep(90)
    print("DONE: max cycles reached without acceptance", flush=True)


if __name__ == "__main__":
    main()
