import sys; sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parents[1])
"""One-off: seed the vault + record the ZEC incident postmortem."""
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.knowledge.vault import Vault

j = Journal("data/luffy.db")
v = Vault(j)
v.run_full_refresh()

v.incident_note("ZEC duplicate-entry crash-loop", """**What happened (first supervised live run):**

A NaN score from indicator edge cases on a symbol with missing candles made
SQLite store NULL into a NOT NULL column. The journalize call crashed AFTER
the previous cycle's entry attempt was already forgotten — so the same ZEC
SELL signal refired every cycle, stacking short entries with no stop-loss
(executor died before SL placement).

**What saved us:**
- Supervised first run caught it in minutes, not weeks.
- RiskManager's already-exposed guard blocked re-entry once the position
  was finally journaled.

**Fixes shipped:**
- NaN sanitization in orchestrator (`_clean`) + zscore guards.
- Executor fill confirmation via fetch_order retries.
- Decision outcome persisted after every entry attempt.

**Lesson:** journal-before-risk is the write ordering that makes crashes
idempotent; never let an unjournaled order exist without a reconcile sweep.
See [[Market Microstructure]] for why demo-venue async fills matter.
""")
print("vault seeded + postmortem written")
