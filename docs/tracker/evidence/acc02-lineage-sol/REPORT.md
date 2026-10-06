# ACC-01 / ACC-02 dependency closure

Result: CLOSED_ARCHITECTURE_OFFLINE. No live observations claimed.

DATA-01 (18a5edf) and EXE-01 (a980039) were CLOSED at intake. ACC-01 was mapped and satisfied first, then ACC-02. Their original requirements and edges remain unchanged.

ACC-01: engine/booking.py retains immutable before/after snapshots and original evidence with sha256; assess/replay refuse missing fees, currency conversions and rewritten assessments. engine/reconcile.py and engine/trade_accounting.py distinguish venue order fills from estimates, retain history/pages/raw receipts and explicit discrepancies/retry reasons. test_trade_booking covers native/ghost/panic estimates, absent fees, transaction rollback and legacy source-free export. test_reconcile and test_reconcile_alignment_commits cover venue alignment without substituting symbol-only trade ownership. DATA-01 canonical identity tests establish versioned market binding.

ACC-02: engine/entry_authority.py retains original logical action/decision/client order ids. engine/booking.py records trade-bound entry, align_delta and close legs transactionally; repeated fill delivery and callbacks cannot double-reduce or double-book. engine/trade_accounting.py legs/history_rows/assess binds exact order/fill ids to booking snapshots and signed financing; incomplete/overlapping/reused identities, non-USDT fees and incomplete history remain retry-required. execution_accounting preserves recovery closes across restart with archive/release in one transaction. Whole-trade import is idempotent and replays source-free.

Verification: 107 passed in tests.txt across seven named suites. Synthetic fixtures only, including mock venue methods; no Kernel, deployed Dashboard or external provider called. This closes evidence mapping for these prerequisites, not ACC-03 or GUI-01.
