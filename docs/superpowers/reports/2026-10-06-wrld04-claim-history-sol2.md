# [SOL-2][WRLD-04] Claim evidence and version history

Scope: WRLD-04 only. WRLD-03 is CLOSED per session instruction. Closure is
architecture/offline engineering; no deployment or production claim-emission
proof is inferred. Tracker, STATE, NEXT and manifest are not changed.

## Demonstrated gap and bounded fix

Before the fix, three regression cases accepted a VALID claim with confidence
1 and a fabricated nonnull value despite its supporting evidence being only
an exact MISSING Observation, an exact MISSING RelationshipState, or absent
(with only contradictory evidence present). The constructor verified hashes
and availability, but never inspected the referenced evidence payload.
The initial focused run was **3 failed, 4 passed**.

`trader/world/model.py` now resolves each exact claim reference to its retained
record and rejects a VALID claim unless at least one supporting record carries
a nonnull value. Missing-only support can still be archived with UNKNOWN
quality and a null value. This adds no estimator, confidence rule, evidence
aggregation, schema migration, or production emitter. It is a necessary
nonmissing-support check, not a claim that a nonnull value proves an inference.

## Proof mapping

| Requirement | Exact proof |
| --- | --- |
| Supporting evidence lineage retained | `test_exact_support_and_contradiction_lineage_survives_archive`: archive/reopen matches model bytes and ID; each reference resolves to exact record SHA-256, source and source_ref. Same observation ID with modified payload is refused. |
| Contradictions retained, never erased by confidence | Exact archive carries both polarities; dropping the contradictory record invalidates the model. The version-history test retains the original contradiction even when a later snapshot omits it. The learned-overlay test retains the entire base claim and contradiction refs through two confidence applications. |
| Confidence changes create version/history | Content-addressed claim IDs change with confidence/validity; WorldHistory retains distinct cuts and refuses competing models at one exact cut. Real LearningApplication appends revisions 1 and 2 with previous_hash linkage; SQLite UPDATE and DELETE are refused. |
| Observed facts not overwritten by confidence | Immutable claims reject field and nested metadata mutation. Confidence/validity snapshots retain exact observations. Learned overlays leave model JSON, claim value/quality and both evidence polarities unchanged. |
| Exact historical/as-of reconstruction | File-backed WorldModelRecord and WorldHistory round-trip byte-for-byte, including claim IDs, confidence, quality and evidence. Exact cuts reconstruct each version; an absent cut refuses latest fallback. Learned revisions remain selectable after Journal reopen. |
| Missing evidence remains UNKNOWN | Observation and relationship missing-only support and contradiction-only VALID claims are refused. UNKNOWN/null claims still round-trip. Missing cut/dimension stays absent/UNKNOWN. A learned .9 confidence overlay on an UNKNOWN/null claim leaves UNKNOWN/null and the original missing Observation intact. |

New proofs are in `tests/test_wrld04_claim_history.py`. Existing temporal,
replay, WorldContext, cognition and real learning tests are checked alongside
this proof; production learning registries remain unchanged and test-only
rules are injected solely into isolated test journals.

## Validation

Focused final run: **8 passed**.
Broader relevant suite: **125 passed in 275.30s**:

```text
./venv/bin/python -m pytest tests/test_wrld04_claim_history.py tests/test_final_audit_learning_time.py tests/test_stage7_replay_world_closure_r2.py tests/test_intelligence_spine.py tests/test_wrld05_normal_context.py tests/test_real_learning_integration.py tests/test_cognition_attention.py -q
```

That broader run loaded seven WRLD-04 cases before the additional UNKNOWN
learned-overlay parameter was added. The final focused run checked all eight
cases (8 passed in 4.27s); no implementation changed between these green runs.
There are 126 distinct passing cases across the two runs, with overlap.
`git diff --check`: PASS.

Graphify AST update completed (24,993 nodes, 63,061 edges), with its manifest writer suppressed to honor the
explicit manifest freeze; the wrapper checks the manifest SHA-256 remains
unchanged (assertion PASS). Community labels partly changed; no LLM relabel was run. No semantic extraction or API call is used. Shared graph artifacts
were already dirty before this task and are excluded from the WRLD-04 commit.

The mandated minimal Claude CLI execution returned `Not logged in`; this
session's authorized work was completed directly.

## Dependency result

WRLD-04 satisfies its requested architecture/offline closure condition.
DATA-05 is already recorded CLOSED with pinned `fc16e07` evidence. Together
with this WRLD-04 closure, WRLD-06 is now eligible for its own proof. This does
not close WRLD-06 or change any tracker entry.
