# LUFFY-TRADE-PROVENANCE-R1

Date: 2026-09-30. Implementer: Opus (Claude Code). Base `87b9e4b`. Control: FROZEN.
Uncommitted, not deployed, kernel not restarted, no venue calls, no test orders.

## What it records (prospective)

| Link | Where | Rule |
|---|---|---|
| Strategy version at entry | `trades.entry_identity_json` | Built from the population the kernel actually compiled (`_load_population`). A spec's evaluator stamps `spec_sha256` of the exact compiled spec on every signal; the identity is VERIFIED only when that hash equals the loaded spec's hash. A mismatch is AMBIGUOUS; no hash, no strategy, or a strategy missing from the running population is UNKNOWN. A trigger (`trades_entry_identity_immutable`) refuses any change, including NULL→value, so history can't be backfilled. |
| Decision | inside identity | decision/cycle/scan ids, action, score, threshold, confidence, meta_p, and the proposing signal. |
| Execution → venue order | `trade_legs` (one row per booking receipt, `booking_id` unique) | purpose (entry / recovered_entry / partial_exit / final_exit / final_exit_partial_fill / panic_exit / native_exit / reconcile_align / reconcile_ghost / adopted / entry_unrecorded), origin (luffy_order / venue_observed / adopted / unrecorded), venue order id exactly as returned (the string is kept verbatim: no trimming, no leading-zero or numeric normalization; only None or "" is unknown), client order id, protective algo id. A Luffy order with no venue id has order identity UNAVAILABLE. |
| Venue fills | `trade_fills` (unique on market_type + symbol + fill key) | Scoped by the market type of the receipt's own trade snapshot (`spot`/`futures`). The key is `<market_type>\|id:<venue fill id>`; `venue_fill_id` keeps the venue string untouched, and order matching compares the exact string (`" 001001 "` never matches `"001001"` or `"1001"`). Identical numeric ids on spot and futures never collide. If the market type is unknown, nothing is keyed or attributed (`unscoped`; the receipt keeps the fill). ATTRIBUTED only when the fill's venue order id equals an order Luffy recorded in the same market type and symbol for exactly one trade. No venue fill id → AMBIGUOUS. Order recorded for two trades → AMBIGUOUS. Unknown order → UNATTRIBUTED, and the observing booking is kept in `observation_json`, which is not attribution. Replays are idempotent. Conflicting content keeps the original evidence and downgrades the fill to AMBIGUOUS. |
| Terminal close | `trade_legs.terminal_close`, `status_transition` | Set from the receipt's own before/after trade status: 1 only when that booking moved the trade `open->closed`. The purpose label (`final_exit`) is not the marker. A closed trade is VERIFIED only when exactly one terminal leg exists and it is a Luffy order with a verified venue order id, `EXACT_LUFFY_ORDER_LINK`, complete fills and a known market type. A missing terminal leg is named `terminal_close` in `missing` and `terminal_close_provenance` in the owner read's `unavailable`. A partial exit never substitutes for it. |
| Exit attribution | `trade_legs.exit_attribution` | `EXACT_LUFFY_ORDER_LINK` means only that the venue order Luffy recorded for this exit is recorded for this journal trade and for no other trade in the same market type and symbol. It does **not** claim exclusive ownership of the venue position (`venue_position_exclusivity: NOT_CLAIMED`). AMBIGUOUS covers: another open trade on the symbol in the journal (`journal_open_siblings_same_symbol`, `sibling_evidence_scope: journal_only`), the order recorded for more than one trade, no venue order id, or an unknown market type. VENUE_EVENT_UNVERIFIED means a native stop, ghost or alignment was booked without a Luffy order or fill identity. |
| Commission | read-time from fills | Per leg: VERIFIED (coverage complete, every fill has commission and asset), PARTIAL, ESTIMATED (booked with the taker model), or UNAVAILABLE. Totals are summed as Decimals by asset, and each fill is counted once. |
| Slippage basis | `trade_legs.reference_json` | Entry: the decision snapshot price the order was sized and stopped on (`snap.price`, last exec-TF close), plus `snapshot_at`, `bar_ts` and `submitted_ms`, taken immediately before `create_order`. Exit: the caller's `exit_price_hint` and `submitted_ms`. The hint's own observation time is unknown. |
| Funding | read | Always UNAVAILABLE per trade. |

Schema migration (`trade_provenance.migrate`, called by `Journal.__init__`) is additive only. It creates missing tables; adds `trade_legs.market_type/status_transition/terminal_close` and `trade_fills.market_type` when absent; and adds a UNIQUE index on `trade_fills(market_type, symbol, fill_key)` plus scoped order indexes. The original `UNIQUE(symbol, fill_key)` is kept: new keys carry the market namespace, so both constraints hold. Rows from the earlier R1 draft keep `market_type NULL` and their unscoped key. They are never re-keyed and their scope is never guessed. The read lists them under `legacy_unscoped_fills` and never counts them. A draft-era `EXACT` exit is read as `LEGACY_EXACT_UNSCOPED`, and its `open_siblings_same_symbol` detail is read as `journal_open_siblings_same_symbol`.

Everything is written in `booking.persist`, inside the existing booking transaction and behind a SAVEPOINT. If provenance recording fails, it logs `control_events.provenance_record_failed` and never fails or rolls back the booking (tested).

## Execution boundary

No order parameters, sizes, sides, types, timings, stop logic, selection, sizing or reconciliation authority changed. Test `test_provenance_does_not_change_what_is_sent` compares every order sent with and without provenance. The only additions on the order path are two `time.time()` reads and evidence fields added to existing journal calls. No new venue reads: entry fills are bound later, from fills that the existing close/reconcile reads already return.

Known side effect: the recovery intent now carries the entry identity, including the full compiled spec. That makes `execution_recovery` state and its control-event details a few KB larger.

## Historical backfill (measured on a backup copy, not production)

`scripts/backfill_trade_provenance.py`. The default dry run never touches the supplied database in any way: SQLite never opens the source. The earlier draft opened it with `mode=ro`, which can still create or write the source's `-shm` (reproduced: a directory holding only the database and its `-wal` gains a `-shm` after a `mode=ro` read). Now the dry run:
1. Records the directory's membership of `<db>`, `<db>-wal`, `<db>-journal` and `<db>-shm`, plus SHA-256, mtime and size of each file present.
2. Copies the database, `-wal` and any rollback `-journal` into a temporary directory with plain file reads, hashing as it copies. `-shm` is never copied.
3. Records the same state again. The run is refused (`SnapshotRefused`) if the before and after states differ, if the copied bytes differ from the recorded hash, or if any source file is unreadable.
4. Opens SQLite on the copy only. Committed WAL frames are recovered there, and a hot rollback journal is rolled back there. It then runs `PRAGMA integrity_check`, refuses unless the result is `ok`, and initializes `Journal` (with its migration) on the copy.

The copy is removed when the run finishes, fails, is refused or is interrupted. `source.opened` in the report reads `file snapshot copy; sqlite opened only the copy`, and `source.files_copied` lists the files copied. `--apply` migrates the supplied database and backfills it the same way as before; integrity replay is unchanged. Backfilled legs get the market type and terminal marker of their own receipt. The figures below come from the earlier run on a backup copy and were not re-measured in this pass.
- Deterministic: 24 of 24 receipts replayed. They produced 12 entry legs with venue order ids and 2 Luffy final exits (`pos_05a5e8f981`, `pos_931b048bb5`), whose 6 fills (4 exit, 2 entry) are all ATTRIBUTED by exact order id with VERIFIED commission.
- Left as is: 9 native exits and 1 ghost are VENUE_EVENT legs with no order or fill identity. All 65 trades keep a NULL entry identity (UNKNOWN). Backfilled exits are UNKNOWN_HISTORICAL, because sibling state at booking was not recorded.

## Close Trade

NOT truth-ready. The provenance model now detects and labels the blocker case: an exit is AMBIGUOUS when a sibling trade is open on the symbol. But `Executor.close` still sends the named trade's journal amount against the net position, and it still books the symbol-window P&L. Fixing that is an execution change and is out of scope here. The frontend idempotency defects from OWNER-CLOSE-TRADE-UI-R1 are also still unfixed. Risk (`already exposed here`) currently prevents concurrent same-symbol trades, so the case can't be reached through normal entry. That is a sizing gate, not an execution invariant.

## Gaps

- The entry reference is the decision snapshot price. No fresh quote is taken at submission; its age can be measured from `snapshot_at`/`submitted_ms`.
- Native protective-stop fills are not bound, because the algo→`actualOrderId` bridge (`native_exit_provenance.py`) is not called in the live path.
- Entry fills of an open trade stay UNAVAILABLE until a later close/reconcile read returns them.
- `graphify update .` completed after the final correction (AST-only, no LLM): 17503 nodes, 39952 edges, 970 communities. Generated graph artifacts and caches changed separately from the six-file correction package.

## Future traceability (conditional)

A trade opened and closed after deployment is traceable end to end (strategy version → decision → entry order → fills → terminal Luffy close) only if every booking's provenance write succeeds. Those writes are contained: a failure logs `provenance_record_failed` and leaves the trade PARTIAL/UNKNOWN, never VERIFIED. It also relies on the close/reconcile venue windows returning the fills and on the trade row carrying `spot`/`futures`. Accepted limits that still apply:
- Native protective-stop exits are terminal but VENUE_EVENT_UNVERIFIED. The algo→order lookup is not called, so those trades stay PARTIAL.
- Entry fills of an open trade stay UNAVAILABLE until a later window read returns them.
- Funding is never attributed per trade.
- **Close Trade is NOT truth-ready** (see above).

## Tests

- Surgical pass (dry-run isolation, exact venue ids): `tests/test_trade_provenance.py` has **46 tests, 46 passed**. New tests cover:
  - a complete entry followed by a failed final write: PARTIAL, with `terminal_close` named in the owner read;
  - a failed entry write followed by a complete terminal close (verified identity, complete exit fills): PARTIAL with `entry_order` missing. `complete` now explicitly requires an entry leg; before this guard the case read VERIFIED;
  - a partial exit cannot substitute for the terminal close;
  - the open→closed marker;
  - spot and futures fills with identical ids do not collide, including replay and conflict;
  - no cross-market order attribution;
  - an order recorded for two trades is AMBIGUOUS;
  - an unknown market type fails closed;
  - the narrowed vocabulary, no venue-exclusivity claim, and the legacy `EXACT` reading;
  - additive migration of the R1 draft schema;
  - CLI dry run on a pre-R1 database in three setups: DELETE mode; WAL with a writer holding an uncheckpointed commit (`-wal` and `-shm` present); and that database plus `-wal` copied into a directory with no `-shm`. In all three, every file in the source directory (`-shm` included) keeps its hash, mtime and size; membership is unchanged; no `-shm` is created; schema, columns and trades (read from a separate file copy) are unchanged; the uncheckpointed WAL commit is visible to the backfill; and no temporary copy is left behind. The source fingerprint is now filesystem-only; the earlier test opened the source with SQLite and excluded `-shm`, which hid the defect;
  - a failed or interrupted (`KeyboardInterrupt`) dry run leaves the source unchanged in all three setups;
  - a source that changes during the copy is refused before the copy is used, and an unreadable source is refused;
  - opaque venue ids (surrounding whitespace, tab, leading zeros) are stored, keyed and matched exactly; trimmed or zero-stripped variants stay UNATTRIBUTED;
  - `--apply` migrates, backfills and is idempotent.

  The existing "orders sent are unchanged" test still passes.
- Directly affected (rerun because `_oid` changed): `tests/test_owner_reads.py` and `tests/test_trade_booking.py`, 44 passed. The full suite was not run in this pass.
- Earlier pass (for reference): 21 tests; full suite 3845 passed, with failures that were pre-existing at base.
