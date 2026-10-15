# DA-09 final admission and receipt integrity closure

DA_09_STATUS: CLOSED_OFFLINE

## Scope and provenance

Exact authorized unified baseline: `6bc0f4cce0360d9ab89440798376b66755b9c34e`, restored into a new independent recovery checkout from the complete-history `int01-unified-final.bundle`. Initial checkout was clean. No production working files were edited, services started, providers contacted, orders submitted, deployment performed, or statistical evaluation budget spent. Tests use fixture accounts/exchanges and a socket-denying `sitecustomize`. The repository's provider-backed Claude probe was excluded by the owner's explicit offline restriction.

Both original defects were already repaired in the baseline. No runtime implementation change or admission-policy redesign is warranted. New work consists of regression evidence and this closure record.

| Original fix | Incorporated equivalent | Verified resolution |
| --- | --- | --- |
| `207e424ff2c966231762562aedb256690cd04778` | `f32726d5fa3c2a95f18636c95df3f720d51176e0` | Python assignment expressions in a comprehension share the enclosing function's binding. The old auxiliary projection replaced the admission `receipt` with an auxiliary dict or `None`, causing verification failure on the next symbol. The `aux_receipt` binding preserves admission. The current auxiliary assignment is AST-identical to the original fix. |
| `cef02b74d1a1f5fd4b64c66301d5baa2a67b5a94` | `f32726d5fa3c2a95f18636c95df3f720d51176e0` | Read the retained rotation/receipt pair and verify, compute and encode before SQLite's writer lock. Under `BEGIN IMMEDIATE`, compare the pair again before atomically retaining blobs, event, next rotation and latest receipt. The entire current `persist` function is AST-identical to the original fix. |

The equivalent commit records `Source-Commit: bf185e55b86f095b7fc44977e36c5c8ab749d69f`. This trailer is recorded provenance, not a claim that the two original commits are literal ancestors of the re-applied unified history. Original full patches and AST hashes are retained in the evidence directory. Original Git stores were read only; a redundant historical fetch was canceled without changing source refs.

## Closing conditions verified

- Multi-symbol normal cycles complete with absent, available and future auxiliary receipts. Both discretionary symbols are processed and decided; their admission/investigation identity remains exact. Future auxiliary inputs are excluded.
- Deliberately restoring the exact inverse shadowing hunk reproduces `admission_receipt_corrupt` for all three auxiliary cases. Moving writer acquisition back before preparation reproduces SQLite lock failure for both observation schemas. These are expected, asserted failures inside passing regression tests.
- Aborting the first or second symbol's decision leaves the committed receipt intact. Mandatory protection still services all 15 held positions. A subsequent scan links to the committed prior rotation and completes both decisions; historical receipt bytes and counts remain exact. An abort is never counted as a completed cycle or a new admission decision.
- Independent committed safety writes remain available during old/new receipt verification for v1 and v2. Real simultaneous writers, both on empty and initialized journals, produce one committed rotation and one explicit optimistic-concurrency refusal, with no phantom event.
- Failures after evidence storage and at event, rotation and latest-pointer insertion roll back the entire operation for both schemas, including otherwise unreferenced blob segments. A fresh Journal sees the original receipt; the next successful admission advances exactly once.
- SQLite busy refusal leaves evidence/rotation unchanged and marks storage safety failure. After the competing writer releases its lock, admission persistence can resume. This test does not grant trading permission or clear the safety latch.
- Missing/corrupt/mismatched receipt or rotation, corrupt retained evidence and forged source clocks fail closed. Refused attempts do not present a previous successful receipt as current admission.
- Fresh-process v1/v2 replay, lossless Journal reads, binary backup integrity, historical frozen input verification, source-cut/restart and late-observation refusal all pass in the selected existing suites.

## Counts, authority and related issues

Policy remains maximum 12 slots, event reserve 2, exploration reserve 2, minimum 0, with no forced reserve backfill. The retained event 2338 remains 439 broad instruments, 10 admitted, 0 event slots, 2 exploration slots and 2 unused slots, receipt ID `582e37df84dffa658c2d5c0fb1f1d74929c23312c8e174ec7329dc379bedb653`. Its decompressed fixture SHA-256 remains `542d63df388e978d0ee991b0a78f3c418425232673be32975b80c5f102dad6b4`. Historical v1 semantics remain frozen; v2 verifies exact identity. The multi-symbol cycle fixture intentionally admits two of thirty during warmup; this is a separate synthetic fixture, not an operational population claim.

Admission grants investigation only. Decision, Risk and Execution authority, independent holdings protection, FROZEN entry refusal, research referee/handoff stop, configuration and frozen doctrine are unchanged. Runtime/configuration hashes and an empty baseline-to-final runtime diff establish preservation in addition to tests.

DA-01's exact native identity and historical/v2 receipt distinction are reused. DA-02's existing bounded queue, transactions and cache behavior are exercised through the existing acquisition and DA-05 suites; no alternate queue is introduced. DA-03's availability/source-cut behavior is checked through replay, source-cut and late-data refusal tests; DA-09 cannot establish provider coverage. DA-05's retained event remains **5 qualified / 407 missing / 27 stale**; the existing **20/412/7 -> 439 qualified** progression is explicitly synthetic and is not relabeled as operational data or decision readiness. DA-05 remains PARTIAL in its own handoff. This closure does not change DA-01/02/03/05 status or close any other issue.

## Verification and limitations

Results: **330 baseline gates passed; 130 supplemental gates passed; 460 unique passing cases, zero failures/errors and zero new failures.** Three expensive replay matrix cases were explicitly deselected. New verification commit: `58ffe821b277af91e7f1da75ebdadb74eb1707b7`; no runtime implementation commit was added.

Exact commands, interpreter, offline guard, file selections, original patches, hashes, JUnit results and test comparison are retained with this report. The added `tests/test_da09_receipt_closure.py` reuses established fixtures and contains 23 passing cases. The historical negative controls remain confined to temporary monkeypatches; no production mutation is retained.

The original selected suites run against unchanged baseline runtime code. The final coverage reuses those passing gates because the only code addition is the regression test file; the full baseline file contents remain unchanged. Supplemental coverage includes the new tests and related replay/transaction/data-cut suites. The comparison distinguishes reused baseline coverage from newly executed supplemental cases instead of presenting it as a second full-suite run. Bundle restoration reruns the 23 closure cases independently.

No DA-09 offline defect remains demonstrated. This is offline implementation closure only; operational/provider behavior and full-repository correctness are not claimed. The three expensive `normal_hold_and_risk_block_to_learning` matrix cases are deselected in the supplemental replay selection; the remaining replay cases complete. Unrelated issue statuses and previously documented broader-suite failures are not altered.
