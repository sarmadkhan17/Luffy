# Review 07 D5 / QR07-14 — Brain research intake

Isolated branch: `sol1/review07-d5`, based on `65f00d9` (D1–D4 evidence), containing implementation tip `94b9000`. Exact parents and ancestry checks are in `ancestry.json`. Brain correction `ef53aa3` and OD07 reconciliation `81565a0` precede the D1–D4 commits `c9cfe7a`, `868f237`, `94b9000`. The running Kernel's separately inspected process remains in `release-ef9fca8`, expecting full revision `ef9fca839c23d48198c5f2361bbf3623d94266fa` (`kernel-identity.json`). No service or deployment action occurred.

## Maintained intake and handoff

`Scraper.harvest_once` → `ideas.record` → `brain_events/harvest_idea` → `ideas.pending(stream='research')` → optional `predictive_bridge.cycle(brain_source_path=...)` / `--brain-source` → read-only SQLite snapshot → `brain-research-source.v1` with `QUESTION_ONLY` authority → existing immutable `bridge_sources` and `bridge_questions` → existing `predictive_bank.predictive_extensions` consumer.

The reader rebuilds the question from its retained source and verifies the source ID, exact question binding and accepted availability boundary. `question_source` exposes the original retained provenance to the downstream reader. No Investigation is manufactured, no generic passage becomes a quantitative hypothesis, and no quantitative/statistical result is assigned. Investigation sources continue through their existing authenticated Bank → template → translation → planner/referee path.

The normal bridge remains explicitly invoked and independent of Kernel. No service, scheduler, replacement engine, provider or new Factory route was introduced. Brain and Investigation inputs share the existing `max_sources` cycle cap; existing experiment caps and statistical controls remain unchanged.

## Source and consumption contract

The retained source contains the original idea ID, exact persisted queue row (row ID, kind, timestamp, subject and raw JSON detail), source database path, source URL/text/title/streams inside that detail, and every original consumption row for that idea at intake. Content-derived identities include this complete snapshot. The queue timestamp defines earliest available time; accepted receipts also retain their observation time. Publication time is not invented: the upstream queue does not retain it when absent from its original payload. This authenticates a local retained queue projection, not the truth of its external prose or remote publisher identity.

`bridge_intake_outcomes` and `bridge_idea_consumption` use the bridge's existing bounded append-only tables and immutable triggers. Accepted question/source, receipt and research-consumption marker commit atomically. Refused provenance is retained and consumed for this Bank's research consumer. Future availability and temporary source unavailability are deferred and leave the passage unconsumed. Repeated identical deferrals preserve their first observation; a later acceptance is a separate immutable outcome. A foreign source schema receives a persistent refusal.

The adapter calls the maintained `ideas.mark_consumed(..., stream='research')` contract, storing its event in the bridge Bank and overlaying it during the existing queue selector's consumption query. Original Brain rows remain byte-for-byte unchanged. Strategy and legacy source consumption keep their existing semantics. Restart/delivery deduplication belongs to this bound Bank, as it does for existing bridge sources; deliberately choosing a different Bank is not a global acknowledgment to other consumers. The original source queue remains pending to readers that do not use this Bank overlay. The Bank must be retained for durable replay.

## Deterministic proof

`tests/test_brain_research_intake.py` exercises the real scraper harvest and queue persistence with synthetic feed output, actual bridge SQLite transactions, and the existing verified Bank reader. `handoff-evidence.json` retains the resulting question and provenance. Its hypothesis, experiment and measured-result arrays are empty; the question is `UNTESTED` / `QUESTION_ONLY`. It is a real question handoff, not proof of predictive efficacy or later quantitative execution.

Positive, refusal, deferred-to-accepted, duplicate-row delivery, repeated-cycle, fresh-process restart, rollback-before-consumption, legacy/research/strategy consumption, missing-source, foreign-schema, budget, lock, source-alias, source-integrity and authority-refusal cases are covered. The mixed-source regression verifies that a Brain passage and a retained Investigation source share the same bridge Bank and that the original Investigation hypothesis remains readable. Existing predictive-bridge and Factory-transfer regression tests are retained separately. Machine-readable JUnit artifacts and logs accompany the test summary.

## Scope and complexity

Production changes: `trader/brain/ideas.py`, `trader/cognition/predictive_bank.py`, `trader/research/predictive_bridge.py`; new bounded adapter `trader/research/brain_intake.py`. New focused test file: `tests/test_brain_research_intake.py`.

`complexity.json` measures **+175 production lines, +5 functions, +1 adapter class, +2 immutable tables**. No production file or function was removed. The Brain selector, per-stream consumption helper, bridge append/locking/budgets and Bank reader are reused instead of duplicating their routing. Inspection confirms the former external-research worker and generated-question source are already retired; their absence remains pinned by the existing retired-chain tests. No additional helper deletion was justified by the traced consumers. The test file adds 244 lines. Total code change is +419 net lines (+175 production, +244 tests). There is an addition, not a claimed net reduction. Generated Graphify changes are local engineering cache artifacts, excluded from the implementation commits; AST-only update was run.

No CI file or Opus work was modified. No production database was written; no provider was called; no order, Kernel/Dashboard restart or deployment occurred. Configuration, FROZEN/PAPER state, referee/handoff switches, research scheduling and error budgets were not changed. The repository's requested Claude execution probe was skipped under the explicit no-provider-call boundary.

D5 is complete for the authorized offline functional integration. D6/D7/D8 are untouched. The remaining boundary is operational adoption of the opt-in intake and any later registered quantitative treatment of unverified passage questions; neither is activated here. Ready for separately scoped D6 work, with no implication of deployment or trading permission.

## Completed verification

- Final intake and existing queue contracts: **34 passed** (`intake-final.xml`).
- Shared passage/Investigation Bank: **1 passed** (`mixed-source.xml`).
- Existing Brain and strategy consumers: **46 passed** (`consumers.xml`).
- Broader predictive bridge, D1–D4 quantitative, Research/Factory, Kernel handoff and retired-chain regressions: **176 passed**, 1680.31 seconds (`focused.xml`).

These runs overlap; their counts are not a unique-test total. The broad run began during implementation. The final intake/queue and mixed-source runs verify the amended passage replay contract; no quantitative-engine code changed. The final AST-only code-graph update completed (`graph-update.log`). `boundary-verification.json` replays the saved handoff against the committed source contract and checks that CI, configuration and Kernel source were untouched.

Implementation commits: `37bea0a`, `09975cd`, `dcb5c1a`. Evidence is retained by the enclosing report commit. The implementation remains on its isolated branch; it has not been merged into or deployed over the running release.
