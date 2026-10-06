# DATA-05 — append-only revisions and exact-cut truth

Engineering closure, 2026-10-06. Canonical reconciliation pending: YES.
Scope: DATA-05 only; offline engineering and evidence. No provider calls,
Kernel/Dashboard boot, production-store mutation, or tracker/state/next/manifest
writes. ACC-03 and EXE-01 work is independent.

## Existing evidence mapped before changes

- `trader/data/market_provenance.py`: `annotate` records exact raw source,
  event/receipt/availability clocks, identity, quality and transform;
  `seal` hashes raw + normalized values and receipt metadata. `append` uses
  `INSERT OR IGNORE`, retains distinct retrieval identities even for identical
  payloads, and links `supersedes` atomically within an instrument/source/event.
- `load` chooses each event's newest observation at an explicit availability
  and observation cut; revision-stream mode retains all eligible revisions.
  Replay additionally requires receipt availability by that bar's close.
  Window selection retains the predecessor needed for alignment.
- `trader/data/feed.py`: `latest_ohlcv` uses the current clock through
  `ohlcv_asof`; explicit historical reads use retained revisions without
  network/cache fallback. `replay_ohlcv` uses original bar-close eligibility.
  `eligible_frame` masks non-VALID data to NaN; an invalid newest receipt is
  unavailable evidence, rather than silently falling back to an older value.
- Derivative and reference stores use the same revision reader. Their default
  reads intentionally return revision streams for temporal alignment; explicit
  cuts select one revision per event. Legacy projection tables are inventory,
  not historical receipt truth.
- DATA-02 `resolve_lineage` reconstructs named durable receipts and refuses
  missing/future/non-VALID/foreign-instrument/schema/transform/hash mismatch.
  `trader/dashboard/owner_reads.py` exposes verified receipts or an unavailable
  reason. Learning capture replay uses frozen source manifests/chunks and does
  not resolve mutable current market views.
- Existing exact tests: `test_revision_retention_pit_and_reopen`,
  `test_latest_asof_and_replay_separate`,
  `test_replay_original_revision_not_later_backfill`,
  `test_receipt_availability_not_event_time`,
  `test_future_observation_and_cache_rejected`,
  `test_revision_content_tamper_rejected`,
  `test_derivative_revisions_quality_and_pit`,
  `test_funding_cost_reader_respects_revision_availability`,
  `test_reference_revisions_reopen_and_stale_quality`, plus
  `tests/test_data02_decision_lineage.py` (durable resolver and owner reads).
  Pre-change baseline: these two full modules passed, **76 tests**.

## Demonstrated gap and bounded change

The shared reader verified receipt JSON hashes but trusted separate SQLite
`revision_id`, `event_ms`, `available_ms`, and `observed_ms` columns for
selection/ranking without comparing them to the verified JSON. The resolver
also ignored those indexed clocks. Corrupting only SQL availability and
observation columns let `load(..., as_of_ms=1779999300099)` return a validly
hashed receipt with `available_at_ms=1779999300100`. This is a shared-reader
contract violation; public feeds have additional eligibility filtering, so
this reproducer does not establish a production trading look-ahead incident.

`load` now checks the selected indexed identity/clocks against the hashed
receipt in ordinary, stream, replay, and window modes. DATA-02 reconstruction
checks the same clocks. Mismatch raises `market_revision_index_corrupt`;
owner reads expose unavailable evidence. Append semantics, quality policy,
source selection, schema, and hashes are unchanged. No migration is needed.
The checks validate selected receipts; this is not a whole-database forensic
scan or protection against coordinated re-hashing of all stored content.

## Closing-condition proof

`tests/test_data05_revision_truth.py` exercises four receipts for one event:
original, same payload received later, late correction, and future correction.

1. Original bytes, value, hash and source remain retained after corrections.
2. Latest selects the eligible original/same-value/corrected/future revision
   at the corresponding current cut, matching explicit historical selection.
3. Exact availability boundaries and immediately preceding cuts select only
   the revision known then; same payload does not erase distinct receipts.
4. An earlier cut has no future receipt; future IDs in historical lineage
   are refused. Corrupted indexes cannot rebase future availability.
5. Hash corruption refuses historical reads and lineage reconstruction;
   selected index tampering refuses all selection modes and latest.
6. DATA-02 reconstruction matches the original raw source, content hash and
   source identity after later revisions; wrong instrument/transform/schema
   and missing revision are refused.
7. Closing/reopening the market database reproduces original historical
   selection, newest latest selection, original bar-close replay, and identical
   reconstructed metadata. Negative lineage controls are repeated after reopen.

New tests also prove the production owner read reports corrupted index
metadata as unavailable. All use temporary SQLite stores and offline venues.

## Validation

Command:

```text
./venv/bin/python -m pytest -q tests/test_stage1_data_provenance_pit.py tests/test_data02_decision_lineage.py tests/test_data05_revision_truth.py tests/test_final_audit_learning_time.py tests/test_historical_outcome_capture.py tests/test_cognition_replay.py tests/test_learning_foundation.py tests/test_owner_reads.py
```

**208 passed in 39.69s**, including **33 new DATA-05 tests**.
`git diff --check` passed for the scoped changes. No real-market evaluation.
Local Claude CLI execution probe failed with `Not logged in`; work continued
under the user's explicit engineering authorization. Graphify query mapped the
existing source paths. Graph refresh is deferred because `graphify update .`
writes a manifest, prohibited by this session; no full semantic extraction.

## Cross-tracker impact (read-only)

- **DATA-03:** the shared selection/index check strengthens current, exact-cut,
  replay, stream and window boundaries. Existing future cache, rollback,
  failed-refresh, derivative/reference, and feature tests pass. This does not
  close DATA-03's broader all-consumer/universe coverage requirement.
- **WRLD-05 / WRLD-06:** shared market historical sources retain identity;
  existing learned-revision tests prove future applied changes do not rewrite
  historical world claims and exact cuts survive reopen/frozen replay.
  Separate row mapping/closure remains required.
- **LRN-01 and replay/outcome consumers:** historical capture, cognition replay,
  learning foundation and owner-read suites pass. Capture binds frozen source
  hashes, refuses missing/tampered originals and never substitutes latest
  config/strategy; restart manifests are deterministic. No learning wiring or
  authority policy changed and no other row is closed by this report.
- **DATA-07, QNT-01, DEC-01:** retained identities remain available to dependent
  work; their deduplication, parity, and coherent-context closure conditions
  were not evaluated as DATA-05 closure work.

Next critical item: map DATA-03's all-consumer future-data boundaries using
this proof; canonical DATA-05 reconciliation remains a separate owner workflow.
