"""One-off: run the entire strategy population past the Yahoo family proxy.

ADVISORY ONLY. This no longer demotes anything: the proxy maps a family to a
stock TradingView strategy and never sees the genome's genes, so its verdict
cannot distinguish two strategies of the same family. It reports concerns and
collapses duplicate genomes. Real demotion lives in strategy/promotion.py
(realized trades) and strategy/evidence.py (internal walk-forward).
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain.llm import BrainLLM
from trader.brain.tv import TVClient, validate_population
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.knowledge.vault import Vault
from trader.notify.telegram import Telegram

logging.basicConfig(level=logging.WARNING)

cfg = load_config()
j = Journal(str(Path(__file__).resolve().parents[1] / "data" / "luffy.db"))
tv = TVClient(interval=cfg["strategies"].get("tv_interval", "1h"),
              period=cfg["strategies"].get("tv_period", "1y"),
              min_oos_trades=int(
                  cfg["strategies"].get("tv_min_oos_trades", 5)),
              require_positive_oos=bool(
                  cfg["strategies"].get("tv_require_positive_oos", True)))

res = validate_population(j, tv, Telegram())
print(json.dumps(res, indent=1))

print("\n═══ POPULATION (UNCHANGED — ADVISORY ONLY) ═══")
for r in j.list_strategies():
    print(f"  {r['name']:32} {r['state']:9} "
          f"reason: {(r['retire_reason'] or '')[:60]}")

Vault(j).refresh_strategy_notes()
Vault(j).write_moc()
print("\nvault notes refreshed")
