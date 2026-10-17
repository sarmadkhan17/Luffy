# DA-06 — admission work efficiency

DA_06_STATUS: CLOSED_OFFLINE

The isolated change removes duplicate analyst input serialization and hashing. It preserves broad discovery, the 12-slot allocation policy, all analyst measurements, and strategy eligibility. Nothing was deployed. The remaining off-strategy investigation work has research consumers and is deliberately retained.

## Root cause and path

1. `trader/data/broad_crypto.py:observe` projects the broad eligible crypto cohort from bulk tickers and retained metadata. It reads cached closed-bar history and evaluates Attention independently of existing strategy symbols. `plan_scan` supplies per-strategy relevance and the independent peer population; relevance is not an entry permission.
2. `trader/cognition/attention.py:evaluate` computes broad anomaly scores, market state, observations and deterministic ranking. Investigation selection is not strategy admission.
3. `trader/attention_admission.py:admit` reserves 2 event + 2 exploration slots, then at most 8 required/ranked/relevant slots. Empty reserves remain unused. Rotation, source identities and allocation are replayable. No strategy-universe filter was added.
4. The read-only trace of `trader/kernel.py:cycle` shows that admitted symbols receive snapshots, positioning/depth context, analyst/strategy decisions, journaling and learning capture. Exposure service remains independent. Kernel was neither modified nor started.
5. `trader/engine/orchestrator.py:decide` evaluates and retains analyst measurements before strategy state/market/regime/symbol eligibility. An absent strategy signal prevents strategy-authorized entry, but analyst evidence still feeds journals and learning. `returned_none` does not prove ineligibility.
6. `trader/agents/base.py` performed `_inputs` both before an analyst calculation and inside `_vote`. Both calls serialized each full DataFrame to CSV and hashed it, although the first call consumed only availability. This duplicated expensive work on eligible and off-strategy candidates alike.

## Retained historical reproduction

The preserved scan is `scan_78513e8e21a148388868f783ce7cce34`, at 1791242657344 ms (2026-10-05 UTC). The diagnostic subset retained 16 of 20 candidates. Its only selected Attention anomaly was **RLC/USDT**, salience 5.32163207747. MOVR, QNT and RLC received Donchian `ineligible_symbol` receipts.

All 16 decisions were HOLD with no emitted strategy signal and entries disabled. Only **MOVR/USDT and QNT/USDT** had every strategy refused by state/market/regime/symbol eligibility: 2/16 (12.5%). They still produced 14 analyst votes, two snapshots and two decision records. RLC reached the funding-filtered strategy evaluator, which returned none; calling it wholly ineligible would be false. There were zero strategy evaluator calls for MOVR/QNT; the expensive work was upstream analyst/data/evidence work.

`retained.json.gz` preserves the scan, versioned bars, original causes/decisions/votes, 77 hash-verified source blobs, exact snapshot frames, original auxiliary inputs and allow-listed captured configuration. Source journal extraction opened immutable, read-only base pages, excluding its WAL. Attention DB and WAL were copied before SQLite access; no source database was opened for writing.

The Attention evaluator whose SHA256 matches the historical scan was recovered from Git commit f9e168e6d952f290b394ac49d5d134bf7927639a. It reproduces **every historical row, observation, market summary and selection exactly**. The newer baseline evaluator adds history/components fields; those pre-existing differences are recorded, and both current versions agree exactly. The historical scan predates hierarchical admission: it is not evidence of a historical 12-slot allocation. The separately retained synthetic 30-symbol receipt verifies the unchanged 12-slot policy exactly.

## Implementation and measurement

Only `trader/agents/base.py` changes executable behavior. Preflight passes `fingerprint=False`, preserving availability/finite-value checks while deferring CSV serialization and hashing to the final measurement. There is no persistent cache and no reuse across snapshots, analysts or calls. Final fingerprints are recomputed after the analyst body, including when a custom evaluator mutates its input. Direct `_vote` calls retain full hashing.

Matched retained-input replay, seven timed repetitions per version:

| Measure | Baseline | New |
|---|---:|---:|
| Analyst evaluations | 112 | 112 |
| DataFrame CSV calls | 224 | 112 |
| Serialized bytes | 53,915,820 | 26,957,910 |
| Median 16-symbol analyst batch | 2,192.90 ms | 1,205.31 ms |
| Sum of per-symbol medians, MOVR + QNT | 251.86 ms | 144.90 ms |

The batch reduction is 45.0% (1.82x); the deterministic reduction is 50% of frame serializations and 26,957,910 redundant bytes. Timing measures the analyst component on retained data, not a live cycle, venue latency or historical CPU time. Historical stage timings were not retained. Original timestamp gaps are not CPU measurements. The remaining 144.90 ms of MOVR/QNT analyst work is useful research evidence, not proven removable waste.

## Parity and tests

`verify.py` verifies exact equality of current baseline/new analyst votes and measurement source digests, 16 decisions, journal cycles/votes/decisions, broad Attention rows/market and the complete synthetic admission receipt. Replay uses original snapshot/auxiliary bytes, an empty local governance journal and empty strategy population to isolate the changed analyst component. It does not claim to reproduce historical strategy decisions under today's code. Wall timestamps and generated correlation IDs are excluded from current decision comparison; measurement observed times, strengths, uncertainty, rationale, source digests and all other evidence remain compared. Outcome scheduling is stochastic and is excluded from journal parity; its implementation is untouched.

The existing matched suite passes **133/133** before the change. With the four new tests added, baseline is **135 passed / 2 expected failures** detecting duplicate CSV calls for valid and invalid inputs. New code passes **137/137**. Tests cover analyst typed evidence, missing/nonfinite/optional/wrong-instrument inputs, direct votes, exact input revisions, mutation during evaluation, journal/restart capture, scan-plan membership, broad Attention, admission reserves/rotation/degraded data, mandatory exposure separation and orchestrator aggregation. No real network or venue execution is used. Regression and replay commands are in `REPRODUCE.txt`.

## Remaining policy decisions

None is required for this optimization. The existing policy is preserved in full.

If further reductions are desired, the exact bounded options are:

* Keep the policy and research evidence: retain all 16 historical decisions and 112 analyst votes; apply the measured serialization saving above. This is the implemented option.
* Skip the two provably ineligible historical evaluations: save 14 analyst evaluations and approximately 144.90 ms of analyst work after this patch, but lose two decisions and 14 analyst measurements plus their research/learning evidence. Acquisition and full-cycle savings are unknown. This violates the required evidence equivalence and was not implemented.
* Give strategy relevance priority over salience in the eight non-reserved slots, retaining two event and two exploration reserves: this changes allocation policy. The retained historical packet has only one selected anomaly and no historical 12-slot receipt, so displaced opportunities and savings cannot honestly be quantified from it. No such change was implemented or recommended on this evidence.
* Reduce exploration reserves: each removed reserved slot reduces the current maximum exploration budget by one of twelve slots (8.33 percentage points of deep capacity; one of two exploration slots is 50% of that reserve). Broad observation could remain, but investigation and opportunity-discovery coverage would change. Full-cohort detection/coverage and cost trade-offs are unmeasured; no change was implemented.

No production, Kernel, Dashboard, venue, trading, threshold, SDD, tracker, Factory or lifecycle changes. Claude execution was unavailable (`Not logged in`); the authorized offline work ran in this isolated Codex checkout.
