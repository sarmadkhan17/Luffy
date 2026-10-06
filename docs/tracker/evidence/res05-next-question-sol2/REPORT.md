# [SOL-2][RES-05] CLOSED — result-to-next-question persistence

This continues RES-05 only. RES-04 remains CLOSED. The prior result-semantics
correction (`2e3c43e`) remains in place. No live provider/LLM calls, paid spend,
service launch, configuration change or trading activation occurred.

## Actual gap mapped first

The RES-01/RES-02 normal `investigation.main` producer already stores the typed
`investigation-volume-anomaly-question.v1` and its frozen investigation,
episode, state, catalog, registration update/context, source hashes and trace.
The existing append-only `investigation_research_records` table uses canonical
body hashes for identity and insert/duplicate/conflict semantics.

The completed Bank retained the original question, sources, explicit claim
availability, support/refuted alternatives, evaluation, conclusion, limitations
and honest cost availability, but `next_questions` was `NOT_AVAILABLE`. Five
baseline cases reproduce absent completed/inconclusive result linkage against
`0780290`; no mocked successful linkage substitutes for this finding.

## Change

The existing family module now derives bounded typed follow-ups from its
verified result using three registered kinds:

- `MISSING_MEASUREMENT` for INCONCLUSIVE results;
- `REFUTED_ALTERNATIVE` for each actually refuted registered alternative;
- `MISSING_PARTICIPANT_SOURCE` for the exact retained unavailable source reason.

These are **the same QUESTION_SCHEMA**, question kind, identity function and
append-only ledger as RES-01/RES-02, with a `result_followup` role. They inherit
the original scope/source/context/protocol and add exact originating
question/result IDs and canonical hashes, result status and hypothesis.
Their authority is `QUESTION_ONLY`. They add no evidence or support and grant
no scheduling, original-test retry, causal, predictive or trading authority.
No new question table, separate question system, router or recursive queue was
introduced. Unknown mappings refuse; no registered follow-up yields no question.
Missing/unsupported producer evidence remains refused or INCONCLUSIVE; it does
not become support because a follow-up was stored.

The v2 Bank builder records question IDs, hashes and types. The normal run
stores those existing-schema questions in the **same transaction** as the
result/Bank. A stable result/index storage key prevents an alternative question
under a changed ID from silently replacing the original relationship. Any
conflict rolls back the whole attempted result transaction. Retry is duplicate
and changes no bytes or recording timestamps.

`chain()` rederives every question from the verified originating result, checks
Bank references and storage keys, and rejects missing, changed or extra
follow-ups even with recomputed valid hashes. It returns each run's verified
linked questions alongside the immutable Bank.

Legacy v1 Bank/question/result records remain byte-identical and readable;
retry preserves their original identity and absence of historical follow-ups.
No past question is invented and no frozen Bank is overwritten. Predictive
source verification and learning replay select the retained builder version;
these are reader compatibility changes, not new RES-07 hypothesis behavior.

## Offline proof

- `tests-focused-final.txt`: 54 checks passed across new linkage, RES-01 normal
  producer, RES-02 routing, investigation family and prior RES-05 negatives.
- `tests-link-final.txt`: 14 final linkage checks passed, including normal
  fresh-interpreter completion from an INCONCLUSIVE result to a measured result
  with refuted alternatives. Both results and all five distinct linked
  questions survive restart without duplicate work.
- `tests-legacy-consumer.txt`: the exact legacy predictive source shape, without
  new next-question fields, remains readable and replayable.
- `baseline-failures.txt`: five completed/inconclusive cases fail against the
  original family code because linkage is absent.
- `tests-wrapper-focused.txt`: resumed verification through the mandatory
  `/home/sarmad/.local/bin/luffy-pytest`: 52 passed, 3 failed. All investigation
  linkage checks passed. The three unrelated strategy-decay routing cases
  refused before research with `environment_not_sanitized:TEMP,TMP,TMPDIR`;
  `routing-diagnosis.txt` and the fixture receipt establish that exact reason.
  The shadow environment allowlist and child were not changed by this work.
- `tests-wrapper-consumers.txt`: **21 passed in 382.21s** through the mandatory wrapper, covering new Bank source verification and downstream feedback/replay.
- `tests-consumers.txt`: earlier broad run retained honestly: 34 passed,
  1 failed and 37 setup errors, including `database or disk is full` and
  subsequent temporary-directory failures. The isolated portfolio case passes
  with both pre-change and current capture code (`unrelated-baseline.txt`,
  `unrelated-current.txt`). This is not a full-repository green claim.
- The interrupted `tests-research-consumers-final.txt` has no completion
  summary and is not counted as a passing run.

The new suite has socket and BrainLLM tripwires. It covers source-unavailability
truth, refuted alternatives, no registered question, unknown result shape,
rehashed lineage/text tampering, missing/extra links, changed storage key,
transaction rollback on conflict, retry, fresh-interpreter restart and legacy
identity. Quiet/missing/disabled normal-producer negatives remain covered by
RES-01 tests. Synthetic observations and quantitative fixtures are not a claim
of real market edge.

Source/test hashes and exact test outcomes are pinned in `proof.json`.
Code-only `graphify update .` refreshes the engineering graph without semantic
extraction or provider calls; changed community-label warnings are retained in
its output and no LLM relabeling is requested.

## Control and successor impact

RES-05 engineering is CLOSED at architecture/offline scope. No tracker, STATE,
NEXT or bundle manifest edits are made in this resumed work. The canonical
RES-05 row therefore retains its earlier BLOCKED status pending separate
control reconciliation; this report does not claim that row was updated.
RES-06's engineering dependency is satisfied through RES-05; RES-07's are
satisfied through RES-05 and the already CLOSED WRLD-05. Both remain unselected.
This closure authorizes no provider enablement or deployment.

Root free space was checked before the resumed substantial runs and after the
focused run: 8.2G in both checks. Final free space and consumer completion are
recorded in `proof.json` and `root-free-after.txt`. All resumed tests use only the
mandated wrapper, whose unique basetemp/TMP directories are on the data mount.
