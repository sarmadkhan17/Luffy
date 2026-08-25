"""One-off: run the entire strategy population through the TV judge.

Demotes (makes trade-ineligible) every strategy that fails TradingView's
1-year walk-forward; collapses duplicate genomes; keeps survivors honest.
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

print("\n═══ POPULATION AFTER VERDICT ═══")
for r in j.list_strategies():
    print(f"  {r['name']:32} {r['state']:9} "
          f"reason: {(r['retire_reason'] or '')[:60]}")

Vault(j).refresh_strategy_notes()
Vault(j).write_moc()
print("\nvault notes refreshed")
