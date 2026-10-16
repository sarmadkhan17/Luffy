DA_05_STATUS: OPERATIONAL_VERIFICATION_REQUIRED

The justified offline code repairs are complete. The retained evidence does
not establish present full-market 4h coverage, and no provider was contacted.
Current operational readiness therefore remains unverified. Deterministic
source fixtures prove the repaired paths and refusals; their generated candles
are not operational observations or evidence of strategy profitability.

The starting point is verified DA-10 full head
`571cf562ff86d7cbe415801dc5b5d459a695e9a7`. It contains the WM-03 data lineage,
integrated DA-05, preserved DA-06 reapplication `cb15d7c5`, and both final
DA-10 implementation commits `f3ed34aa` and `596f9f17`. The input bundles were
SHA256 checked. `lineage.json` records ancestry and reconciliation against the
original DA-05 complete bundle at `ec75bd6e`. Eight queue/readiness methods
match its AST exactly, and `attention_history.py` plus the original 17 DA-05
tests match byte for byte. The inherited DA-04 worker failure diagnostics
are deliberately retained. No completed DA-05 repair was reimplemented or
cherry-picked again. The original histories remain available in the bundle.

ROOT_CAUSE: Two remaining DataFeed defects were reproduced, including durable
state left by the first defect. When an existing qualified history was shorter
than a new request of at most 1,000 bars, acquisition calculated its start
from the deficit rather than the complete requested window. A fresh 154-bar
Attention history expanded to only 246 bars for a 400-bar snapshot request.
The short result was then recorded as the source's history floor, so restart
continued serving 246 without another request despite older source data.
Separately, the exception fallback returned a cached 20-bar window after a
provider failure even when the caller required 26 or 30 bars. The ordinary
successful/initial-short paths already enforced the minimum.

FIXES: The existing bounded request now starts on the current closed-bar grid
far enough back for the whole requested window. Anchoring to the current cut
also preserves the existing missing-tail regression: a store missing its last
12 bars repairs to 400, rather than unnecessarily expanding to 412. Existing
full-window tail refresh and paged acquisition paths are unchanged. The failed
refresh fallback passes through the existing `finish` qualification/minimum
boundary. Short observations remain in the candle and immutable revision
stores; they do not acquire decision authority.

Existing floor hints need recovery too. The same `candle_floor` table gains a
nullable `window_version` column. Old rows and their values remain present,
but only hints established under the corrected full-window contract are used
to skip acquisition. An old hint consequently receives a bounded recheck
through the ordinary queue. A genuinely short source is marked after that
check and reused after restart; an incorrectly truncated hint no longer
strands older available history. Migration uses the existing transaction
cleanup with one serialized, double-checked ALTER; concurrent openers are
tested. This is an optimization-hint contract marker, not a new quality
threshold, candle receipt, storage subsystem or second acquisition queue.
The read-only retrospective connection path does not perform migration.

MARKET_DATA_READINESS: `broad_crypto.observe` still observes the full eligible
crypto cohort from bulk ticker and metadata receipts, independently of current
strategy symbols. It reads retained 4h bars at its frozen cut. The existing
Attention capture/evaluator checks availability, observed time, finality,
quality, the exact 26-bar warmup grid and unchanged salience thresholds. Broad
capture also requests 153 bars for its longer correlation context. Admission
freezes the existing roles, rotation and 12-slot maximum before acquisition.
Only after receipt persistence/verification does history preparation submit
bounded work, including broad exploration, through DA-02's existing queue.

An admitted candidate subsequently calls `_candidate_snapshot_for` and
`_snapshot_for`. `fetch_multi` requests the existing 400-bar execution/context
windows; background misses queue work and refuse unavailable inputs until a
later read. DATA qualifies receipts at the post-acquisition cut, checking
freshness, source and instrument identity. Optional missing 4h context cannot
substitute for a strategy's declared 4h frame. The existing compiled evaluator
then applies closed-bar, dependency and feature rules. Per-strategy eligibility
remains separate from broad discovery. A valid window need not emit a signal;
the legacy evaluator does not distinguish every false predicate from
insufficient feature lookback. No extra live-readiness assertion was invented.

The end-to-end deterministic test uses the normal admission, durable receipt,
single-worker body, snapshot and compiled-evaluator methods without booting a
Kernel. A labelled synthetic test rule with EMA(300) cannot evaluate from the
old 246-bar truncation and produces its expected output from the new 400-bar
window. An earlier admission cut continues to see only its original 154 bars,
the frozen receipt remains exact, and no-output, short, stale and unavailable
cases remain WARMING/refused. These tests install no strategy or trading
permission. Factory, probation, lifecycle, Risk and execution code is untouched.

BEFORE_AFTER_EVIDENCE: `measure-before.json` and `measure-final.json` record
matched deterministic work. Available-source expansions are 154→400:
246→400 returned bars; 300→400: 300→400; 399→400: 399→400; and 154→1,000:
846→1,000. A genuinely 250-bar source returns 250 instead of the incorrectly
truncated 246. Every probe uses two acquisitions in total (initial plus
expansion), a maximum request limit of 1,000 and zero additional restart
requests. Preserving the extra genuine receipts increases stored evidence;
this is a correctness repair, not a storage reduction or latency-speedup claim.
The 154→400 probe retains 554 immutable revisions instead of 400. The failed
short fallback returns no frame instead of 20 bars, while all 20 candle rows
remain retained. Existing caps, batch size, trading reserve, service fairness,
cooldowns and provider timeouts are unchanged.

Actual retained event 2338 has 439 symbols: 5 qualified, 407 missing and 27
stale. Current Attention rows and the entire admission receipt replay exactly.
The earlier claimed distribution 20/412/7 remains a synthetic reproduction;
its original operational receipt was not located by previous DA-05 work.
The retained DA-06 scan `scan_78513e8e21a148388868f783ce7cce34` supplies 77
hash-verified source blobs and 16 exact snapshots. Fifteen have only 16 4h bars;
the only 400-bar snapshot is RLC/USDT. All 13 snapshots within the retained
Donchian strategy's declared include set are among the short windows. None
establishes the 101 bars needed for that rule's 100-bar lagged channel. The
current evaluator receives these exact retained values and leaves its
insufficient predicates false; no bars or clocks are extended. That packet
predates the repaired acquisition path and hierarchical admission. It proves
a historical gap, not a fresh live regression or current coverage. The strategy
definition is only a numerical/input probe, not a replayed lifecycle decision.
`retained-readiness.json` records the exact cuts, source hashes and per-symbol
results for both distinct historical packets.

REGRESSION_TESTS: `verification.json` is the machine-checked final matrix and
case comparison. Baseline storage/Attention/DA-04 selection: 50 passing cases.
Baseline broader feed, provenance, admission, compiler, Attention and DA-06
selection: 268 passing cases and one inherited failure. The final selection
contains those 319 old cases plus 20 new closure controls. The new controls
are also executed against a pristine worktree at the exact baseline head;
staged exploratory runs are not substituted for that matched result.

One inherited test, `test_incremental_fetch_re_reads_the_stored_tail`, seeds
unqualified projection inventory and expects a targeted `since` refresh. The
existing DA-05 provenance guard instead reacquires the bounded window with
`since=None`. The same assertion fails on baseline and final code; provenance
was not weakened to satisfy it. The full repository suite is not certified.
An intermediate implementation anchored expansion to the old tail and failed
the existing missing-tail regression (412 instead of 400); the final current-cut
anchor repairs it. Raw intermediate logs remain identified as attempts. An
early test wrongly expected reacquired revisions to keep the old selected
revision IDs; it was corrected to require preservation in immutable storage.
The intermediate whole-cohort rerun was interrupted only after the floor fix
changed source, then replaced by the final complete selection. None of these
attempts is reported as a passing final gate.

The final main selection is **338 passed / 1 inherited failure**, and the six
additional read-only feed cases pass on both versions: **344 unique passing
cases / 1 identical inherited failure**. All old case statuses match exactly.
The 20 new controls change from **7 passed / 13 failed** on the pristine
baseline to **20 passed**. The final focused storage/readiness selection has
**61 passed**. The 439-symbol synthetic cohort still advances 20→431→439
ranking-eligible windows with the same 419 acquisitions and at most 16 pending
submissions; it does not establish real provider coverage. Its final candle
database is 85,950,464 bytes, with a 128-series cache and 128 retained outcomes.
Raw logs/XML are preserved verbatim, including their whitespace. Code-only
whitespace checks pass. Implementation commit:
`519baea203fd1bac6698354df3806261c8e8464a`.

REMAINING_GAPS: Only owner-authorized future operational observation can
establish current source availability, coverage, freshness, listing history
and per-strategy dependencies under real queue load. Old floor hints require
one bounded recheck before they can again optimize short-source requests;
unavailable sources cannot certify them. An in-flight provider call remains
subject to the existing timeout and is not preempted. The synthetic 439-symbol
recovery scenario measures code behavior under an available frozen source,
not real throughput or completed current-market acquisition. No missing
evidence was promoted to ready, and no owner policy decision is needed for
the implemented repairs.

All writes and tests used this assignment's disposable recovery-volume paths.
The production interpreter/dependencies were read only. No production database
was opened, no provider or coding-provider call was made, no Kernel/Dashboard
was started, and no production writes, trades, deployment, SDD/tracker edits,
threshold changes or integration occurred. The repository's provider-backed
coding workflow cannot apply under this owner's explicit no-provider boundary;
implementation and verification were performed locally. AST-only Graphify was
used according to repository guidance; no semantic/API extraction ran.

The bundle includes complete Git history, this report, scripts, raw final and
baseline evidence and the retained packets inherited from DA-06/DA-10. A fresh
independent restoration reruns the verifier, retained replay, new controls and
Git integrity checks. Exact commit IDs, bundle digest, restore results and
cleanup status are recorded in the adjacent external return JSON and hash
manifest, avoiding a self-referential bundle hash inside its own commit.
