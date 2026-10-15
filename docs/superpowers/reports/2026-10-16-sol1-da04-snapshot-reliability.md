# DA-04 snapshot reliability — offline evidence

Scope: isolated offline remediation from verified unified `6bc0f4cc`, using
the reconciled RP-03 complete bundle at `2e447489`. DA-08 is an ancestor;
DA-05's queue/readiness methods and history preparation match its preserved
implementation; DA-09's closure tests match exactly and the original runtime
fix is present as `f32726d5`. Input hashes and reconciliation checks are in
the accompanying `input-provenance.json` evidence. No additional copy of an
already incorporated runtime patch was applied.

Disposition: **PARTIAL** for the original incident claim. The demonstrated
offline defects are repaired and separately verified; the historical 56
remain unauthenticated. This disposition does not authorize operational
closure or integration.

The historical **56 snapshot failures cannot be authenticated** from the
supplied bundles and targeted preserved evidence inspected in this assignment.
There is no authenticated 56-occurrence manifest with per-occurrence cut,
source revisions, refusal and code/config identity. The number is not a
reproduced failure count. DA-05's 56 preparation passes, retained event 2338's
5 qualified / 407 missing / 27 stale histories, and inherited pytest failures
are different populations. None substitutes for the missing history. No
historical root cause or operational closure is claimed.

The acquired-data path is `DataFeed.fetch_multi` → `fetch_ohlcv`. Foreground
background mode reads the retained as-of revisions and submits demand to the
existing DA-02 queue. Its single worker obtains provider rows, annotates exact
local acquisition clocks, and atomically appends receipts plus the inventory
projection through `write_tx`. Only the retained read supplies a decision
frame. Kernel qualifies those supplied frames at its post-acquisition cut,
binds revision identities in `market_provenance_parts`, and sends the same
snapshot to strategy/analyst evaluation, holdings servicing and the existing
decision/evidence consumers. Admission still owns deep selection; cache,
queue diagnostics and snapshot success confer no trading authority.

Deterministic reproductions establish separate current defects:

1. A failed synchronous refresh cached stale values with a recent cache
   timestamp. An unforced call could then return stale data without attempting
   recovery until the cache TTL elapsed. The cache branch now checks the
   existing receipt freshness/quality before reusing it.
2. `_store_save` swallowed transaction errors without returning persistence
   status. A worker refreshing a warm series could report completion using
   older retained bars even though its new receipt was rolled back. Explicit
   bounded persistence status now reaches the worker's failed count/outcome.
   The previous committed frame remains usable only on its own original
   identity, quality and clocks. A failed save triggers neither an extra
   retained reread nor a floor-marker write.
3. Store-read exceptions could escape foreground background-mode acquisition
   and abort later symbol processing. Reads now refuse that series with a
   bounded diagnostic. Kernel also contains exceptions at the acquisition
   boundary; optional BTC context failure remains optional.
4. Kernel collapsed missing, stale, invalid and mismatched frames into a
   generic missing-snapshot skip. A uniformly foreign execution identity
   could pass the old internal-consistency check without a proposal binding.
   Current qualification additionally checks loaded DataFeed identity/source
   (or the existing exact Registry binding). It does not invent identity when
   the producer has none.
5. Repeated configured timeframes and an already acquired BTC 1h context
   could be read twice. The existing multi-fetch deduplicates timeframes and
   Kernel reuses its already acquired BTC context.
6. Background acquisition could discard a short stale frame before retaining
   the cause, reporting `missing`. It now retains `stale`, `invalid` or
   `insufficient_history` before returning the same unavailable result. This
   followup changes only diagnostic state, not acquisition or qualification.

Kernel refusal reasons are finite: `missing`, `stale`, `invalid`,
`identity_mismatch`, `read_failure`, `incomplete`, `unavailable_at_cut`,
`insufficient_history`, `source_unavailable`, `persistence_failure`.
`valid` labels a qualified frame. Each Kernel attempt resets symbol,
timeframe, cut and availability; no previous symbol's diagnostic leaks into a
later refusal. Persistence detail is `sqlite_busy` or `write_failure`; no raw
exception text is added to canonical diagnostics. Existing log behavior is
retained. The cycle exposes bounded refusal counts and freezes the exact
diagnostic through the existing missed-snapshot evidence transaction and
Attention cause. The old high-level `missing_snapshot` and
`FAILED_SNAPSHOT` labels remain compatible.

Qualification performs no new database query. It uses the same current-frame
eligibility, final-bar checks and TTL as before. Explicit cuts still reject
future available/observed receipts; bar-close replay still excludes late
backfills. Decisions retain exact revision IDs, never a later "latest"
lookup. The delayed-commit fixture observes no new revision before commit,
records a refusal, releases persistence, reopens the store and verifies that
the refusal source bytes remain unchanged and new snapshots replay only their
named retained revisions. Receipt availability is the existing local receive
clock, not a new historical commit-visibility index; an as-of inventory query
after commit is not proof that an earlier Kernel decision saw those rows.
No later read is used to upgrade an earlier decision or its refusal.
The admission source cut remains distinct from the later snapshot decision
cut. Both original cuts remain in lineage; acquiring decision data does not
rewrite the earlier broad-admission evidence.

The existing receipt/projection transaction remains atomic under competing
SQLite writers; real isolated EXCLUSIVE reader faults and IMMEDIATE writer
faults exercise rollback and recovery. There is no new queue, thread, table,
retry loop, provider mechanism, duplicated frame cache, or database migration.
The merge path counts timestamps instead of concatenating all receipt/value
columns only to discard them before a retained reread.

One pre-existing source-mutation negative control follows the acquisition
body after it moved behind the diagnostic wrapper. Its causal assertion is
unchanged: deleting the cache clock/eligibility fences must expose future
data and be detected. It supports both the original and wrapped body. All
other inherited regression assertions remain unchanged.

Final measured tests, costs, commit IDs, bundle verification and cleanup are
recorded in `DA04_RETURN.json` and the accompanying structured test evidence.
Dedicated fault, lock, delayed-persistence, restart and refusal-capture tests
use default SQLite durability. The large matched logical comparison disables
fixture fsync (`synchronous=OFF`) identically in both trees after the default
large runs were interrupted for host I/O starvation. Locking and transaction
logic remain active; that comparison does not establish power-loss durability
or production timing. Performance samples use the default settings. Their
first 12-snapshot batch expands the requested history window and is identified
as warmup; the last two batches measure the warmed snapshot path.
Across those 24 warmed snapshots, wall time was 9.115 → 8.883 ms per
snapshot; CPU was 9.033 → 8.882 ms. An earlier main-implementation sample was
10.055 ms wall / 9.981 ms CPU; both samples are preserved, and no speedup or
stable overhead is claimed from this observed variation. Each side used one
candle-store SELECT per snapshot, zero writes and zero provider requests.
The unchanged Journal proposal-binding read is included in time but not the
candle-store SQL trace. Cold acquisition
used the same 108 SQL statements, 60 SELECTs, 24 INSERTs and 12 fake-provider
requests. Both candle stores were 1,187,840 bytes and process peak RSS was
422,868 KiB. The simple refused-cycle fixture adds 127 canonical reason bytes
to its existing source blob. These small synthetic samples do not establish
production latency or statistically significant performance differences;
I/O-bound cold/warmup wall times are not attributed to the code change.
The matched logical selection has 828 cases: **826 passed / 2 inherited
failures on both trees**, identical failure IDs and no new failures. The two
legacy fixtures expect projection-only rows to qualify for replay or a tail
cursor; both conflict with the existing retained-provenance boundary. They
were not changed or relaxed. The 25 new fault/diagnostic cases are 2 passed /
23 failed on the baseline and **25 passed** on the final runtime. Those 23
assertion failures do not denote 23 distinct historical incidents. The final
default-durability focused run also includes queue and cache-mutation controls:
**37 passed**. The matched broad run tested `d31bab4a`; the narrow followup
`553dc423` is covered by that final focused run and independent restore checks.
The matched logical runs consumed 563.236 → 564.111 CPU seconds, 708.155 →
706.994 wall seconds, and 1,205,196 → 1,209,192 KiB peak RSS. These totals
include the large unchanged research/coverage fixtures; they are not cycle
latency measurements. Full repository tests were not run.
An independent complete-history bundle restore passed **28 default-durability
checks** on the final runtime, including all 25 DA-04 cases, original DA-09
writer-lock negative controls and the RP-03 Gate 2 refusal control. Exact
input hashes, source audit, costs and test counts are also committed in
`2026-10-16-sol1-da04-verification.json`.
Fixtures establish software behavior only. They establish neither present
provider coverage nor live reliability. DA-05 remains PARTIAL; DA-08, DA-09
and RP-03 retain their independent offline dispositions. Strategy ownership,
Risk, Execution, FROZEN, recovery, config and held-out research artifacts are
unchanged. No provider calls, Kernel/Dashboard starts, production database
writes, integration, deployment, tracker or SDD edits were performed.
