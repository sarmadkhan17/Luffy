# [SOL-2][RES-05] BLOCKED — structured research persistence

RES-04 is CLOSED. This work maps and corrects RES-05 only. No live provider,
LLM, paid request, service launch or trading activation occurred.

## Existing evidence mapped before changes

| Required link | Normal external worker | Existing investigation research family |
|---|---|---|
| Original question | Registered/generated question and producer provenance retained verbatim; replay verifies source | Exact observable question, investigation, registration context and trace IDs |
| Sources | Registry definitions, query/checkpoint receipts, metadata, passage and source hashes | Frozen measurement/update/target-input chain, verified by rederivation |
| Claims | Explicit NOT_AVAILABLE/no registered extractor | Explicit NOT_AVAILABLE/no extraction contract |
| Support/contradiction | Explicit NOT_CLASSIFIED; passages never establish support | Registered measured path SUPPORTED; two competing paths REFUTED; missing input yields NOT_ASSESSED |
| Experiment/evaluation | Empty experiment list: collector performs no evaluation | Frozen predicate protocol, measured OutcomeEvidence and structural run steps |
| Conclusion | INCONCLUSIVE plus distinct COLLECTED/EMPTY/UNAVAILABLE collection outcomes | Result status/reason and per-path findings copied from verified result |
| Limitations | Exact lexical, bounded, metadata/abstract-only limitations | Explicit descriptive-only meaning and participant source not acquired |
| Cost | Current-pass request attempts/bytes, zero paid/LLM spend, operating cost NOT_MEASURED | Explicit NOT_MEASURED/no family telemetry contract |
| Next question | Retained QUESTION_ONLY intent requiring a registered falsification protocol | Own Bank field NOT_AVAILABLE/no generator |

`trader/research/external_research.py:research_pass` is the normal registered
question consumer. It stores structured Bank records in the existing Journal,
not just logs/bookmarks. `verify_bank` verifies their identities, original
question, source definitions and request/extraction lineage. Journal insertion
is immutable insert/duplicate/conflict and refuses conflicting truth.

`trader/observability/investigation_research.py` is an existing completed
research path with refuted alternatives. Its replay rederives the whole frozen
chain. `predictive_bridge` and `predictive_bank` provide separate quantitative
attachments and bounded questions; external Bank sources are deliberately not
accepted by that bridge. These contracts do not establish a standalone terminal
REFUTED result from the external question worker.

## Demonstrated gap and change

Seven rehashed and registered local fixture mutations passed the original
external verifier: fabricated claims, support or contradiction despite a missing
source, fabricated passed experiment, removed next question, false collection
outcome, and understated current-pass cost. The fixtures deliberately bypass
append-only protection and recompute valid hashes/registrations, so semantic
refusal cannot be confused with a duplicate insertion refusal.

The verifier now checks the collector's existing fixed result fields, reconstructs
collection disposition from receipt statuses, and reconciles current-pass cost
with receipts marked uncached. Builder fields share one definition with the
writer. Valid v1/v2 receipts retain their original bytes and identities; no
schema migration or new evidence/evaluation authority was added. Cost remains
self-reported operational telemetry; this is no independent operating-cost audit.

## Why RES-05 remains BLOCKED

The full requested closure condition is not demonstrated. External collection
only emits INCONCLUSIVE; investigation completion retains REFUTED alternatives
inside a SUPPORTED result but its own next-question field remains unavailable.
The completed result containing REFUTED alternatives therefore lacks the
complete linked next-question chain. Explicit missing claims/cost/evaluation statuses are
honest records, not invented evidence or measured costs.

The correction above closes replay integrity gaps, not those missing terminal
semantics. Establishing a registered result/next-question contract for the
supported research family remains necessary. Do not relabel descriptive
alternatives or predictive UNSUPPORTED as a new REFUTED result just to close the
row. RES-06/RES-07 remain unchanged and blocked on RES-05; no successor selected.

## Verification and control

`baseline-failures.txt`: seven corrected cases fail against original HEAD code.
`focused-tests.txt`: 12 final focused checks pass, with socket and BrainLLM
tripwires. COLLECTED/EMPTY/UNAVAILABLE records remain first-class INCONCLUSIVE;
restart and duplicate preserve the exact row and ID without new transport work;
conflicting writes leave the original untouched. Two inventory checks retain
the completed/refuted-alternative and inconclusive investigation semantics.

The initial four-suite run passed 60 tests. The larger run reached the shell
file-descriptor limit (`tests.txt`); `tests-final.txt` records the rerun with
8192 descriptors: **161 passed in 137.89s**. Source/test hashes and results are pinned in `proof.json`.
Code-only `graphify update .` runs without semantic extraction or provider calls.
The graph refresh reports changed community labels; no LLM relabeling was run.

Canonical RES-05 is BLOCKED and NEXT selects that same result. Dashboard
Tracker read is AVAILABLE. `control-checks.json` verifies the selected status,
178 IDs, 177 unchanged unrelated rows, and preserved original closure condition
and dependency edges. Historical closures and runtime evidence remain historical.
