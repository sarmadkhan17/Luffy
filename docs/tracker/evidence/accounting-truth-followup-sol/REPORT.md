# Accounting truth follow-up — CLOSED offline

ACC-01 remains CLOSED only with the canonical correction at `88677941115a138258af261e37a6a488ac7a844c`. Its earlier evidence omitted four paths. The original condition has not been weakened: reconciliation retains receipts, field source and discrepancies/corrective actions; local estimates stay labelled. Independently rerun reconcile/booking suites pass.

The exact Opus worktree is `/tmp/claude-1000/-mnt-luffy-data-luffy-production/762c7983-d852-425b-a0c1-d9dc66652a9d/scratchpad/wt`. Its `docs/tracker/evidence/acc01-correction-opus/closure.yaml` was imported into this checkout unchanged. This corrects ACC-01's part of the historical shared ACC-01/ACC-02 evidence; ACC-02's requirement/status remain unchanged.

| Previously missed gap | Corrected evidence in 8867794 |
| --- | --- |
| Ghost close without a quote booked zero exit/total loss or silently flat P&L | Journal storage placeholder retained, receipt P&L class/status UNKNOWN, price source `journal_entry_not_a_fill`. |
| Panic close without order/ticker price booked total loss | UNKNOWN classification and price source; original order identity retained. |
| Adoption without venue entry price claimed zero as reported price | `venue_entry_price=null`, `entry_price_status=UNKNOWN`, venue position exposure source retained. NOT NULL journal entry zero remains a storage placeholder. |
| Mark-priced ghost/panic/alignment shrink lacked estimate/source labels | `pnl_status=ESTIMATE`, `pnl_value_class=DERIVED_ESTIMATE`, source/basis recorded. |

ACC-03 consumer correction is pinned to `37c8d43ced2d65fb2418e87435b178318832301b` (`trade-economics-read.v2`). Dashboard economics now replays and checks trade-bound booking evidence before exposing journal P&L. It reads the entire booking history so an early UNKNOWN/invalid leg cannot disappear behind a display cap. UNKNOWN status/class, an unpriced basis or a journal-entry price placeholder produces null. A DERIVED_ESTIMATE retains its estimate status and price-source/basis coverage. Recorded venue fill monetary fields stay in their independent section; even a known journal number never establishes complete net accounting. Legacy unclassified journal values remain explicitly journal-booked, not promoted to venue actuals.

GUI-01 revalidation covers the main realized-today aggregate, the historical strategy activity total and trade-lineage economics. Any UNKNOWN contributing booking suppresses the displayed total; estimates are visibly labelled. The raw scalar remains available only as explicitly named recorded journal evidence. Realized-today evidence is versioned `overview-journal-realized.v2`. A readable day with no closed trades remains a measured zero, distinct from an UNKNOWN closed trade.

Verification:

- `backend-final.txt`: 190 passing tests across consumer, ACC-01, economics, Overview, historical activity, reconcile, immutable booking, whole-trade accounting and execution accounting suites.
- `backend-final-additional.txt`: 7 passing economics tests, including one additional regression proving UNKNOWN/estimated journal values cannot hide or relabel recorded venue monetary fields. Total distinct backend tests: 191.
- `acc01-tests.txt`: 101 independently passing canonical/adjacent reconcile and booking tests (overlap with the combined backend run; not added twice).
- `frontend-tests.txt`: all 167 frontend tests pass. `typecheck.txt`: PASS.
- `browser-tests.txt` / `browser-results.json`: 7 passing browser checks, including actual fixture API bookings with UNKNOWN and DERIVED_ESTIMATE, exact Needs You and stopped/stale/disconnected Overview regressions.
- `browser-readonly.txt`: 1 passing real READ_ONLY_GUI server check with Kernel STOPPED. Eight browser checks total.
- `overview-unknown.png`, `overview-estimate.png`: fixture screenshots, not owner visual approval.
- `source-manifest.json`: exact source hashes and implementation/canonical commits. `verify.py` / `control-validation.json`: control parity, unchanged original conditions/edges, three-row impact boundary and exact bundle hashes.

The implementation commit also pins the previously authorized GUI-01 changes that had remained uncommitted in this shared checkout. Unrelated working-tree changes are preserved. No Kernel or production Dashboard was launched; fixture servers, fake gateways and fake exchanges only. No live venue/provider calls, runtime activation or owner visual acceptance were performed.

Tracker/STATE/NEXT/manifest are synchronized. Only ACC-01, ACC-03 and GUI-01 evidence is corrected; their CLOSED status is retained at the verified offline scope. Original conditions, dependencies, future activation gates, accounting incompleteness and VIS-01 AWAITING_OWNER remain. GUI-02 is recommended for dependency/evidence mapping and remains unselected.
