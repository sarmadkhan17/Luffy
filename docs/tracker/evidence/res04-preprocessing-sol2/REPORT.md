# [SOL-2][RES-04] CLOSED — architecture/offline preprocessing

Dependencies RES-03 and LLM-02 are CLOSED. Work is limited to RES-04;
RES-05 is now eligible but unselected. No live provider calls, LLM reasoning,
paid spend, service launch or activation occurred.

## Evidence mapped first

| Requirement | Existing exact evidence |
|---|---|
| Correct source class | `external_sources.route/select`: registered strategy-decay questions route to academic factors/public Crossref; unsupported question/class/source refuses. RES-02 generated handoff/CLI tests exercise the normal producer and worker. |
| Cheap relevance | `filter_metadata`: lexical finance plus strategy/decay/regime terms in titles, before detail retrieval. |
| Duplicate documents | Normalized URL and whitespace/case-normalized title dedup; request checkpoints and durable bank identities suppress repeated work after restart. Exact positive metadata/content reuse across independent questions retains original receipts while performing a fresh search. |
| Bounded work | `Bounds`: queries, metadata, retrievals, response bytes, passage characters and deadline; child runner hard timeout; storage capacity and single-writer lock. |
| Provenance | Question producer receipts, registry/source hashes, retriever and builder versions, query identities, raw response/content hashes, metadata snapshot hashes, passage hashes, timestamps and registered Research Bank identities. |
| Limits and insufficient evidence | Explicit EMPTY/UNAVAILABLE, missing abstracts, INCONCLUSIVE/RESEARCH_ONLY, bounded/nonexhaustive selection, no full-text/causal/predictive validity claim, zero paid/LLM cost and unmeasured operating cost. |
| No unnecessary reasoning | No LLM caller in this path; disabled-provider boundary remains separately CLOSED. Existing duplicate/irrelevance tests assert transport call counts. |

## Demonstrated gaps and correction

Seven focused cases failed against the original module, with one critical
negative already passing. The corrected baseline reproduction uses deliberately
rewritten fixture rows plus valid recomputed hashes/registration, so an insert
conflict cannot stand in for semantic validation.

1. Raw-HTML dedup missed equivalent visible text with different markup.
2. Prefix extraction could retain unrelated text while omitting relevant text
   later in an abstract; an irrelevant abstract could become extracted evidence.
3. Replay accepted rehashed fabricated query references/source URLs, altered
   passages and removed limitations because it did not rederive preprocessing.

The v2 builder normalizes rendered text before content dedup, then selects a
lexically relevant passage window within the declared character cap. Rendering
scans at most 32,000 abstract characters; token matching is linear. No matching
window remains `NO_RELEVANT_PASSAGE`, with no passage and INCONCLUSIVE status.
Repeated content retains its duplicate receipt and yields no second passage.

The bank declares `academic-decay-preprocessing.v2` and explicit lexical
relevance, rendered-content dedup and PARTIAL_BOUNDED coverage limits. The
verifier reconstructs selected metadata, filtering counters, document version
arguments, exact query receipt references, passage/duplicate output and required
limitations. Recomputed hashes cannot waive those checks. Legacy v1 builder
receipts retain their original extraction semantics and remain verifiable;
registry bytes and request/cache identities are unchanged.

## Offline proof

83 combined tests passed: RES-04 preprocessing, existing external router,
RES-02 routing, generated handoff/negative memory and RES-03 governance.
After the final linear-window implementation and legacy compatibility case,
all 11 focused RES-04 tests passed. Socket and BrainLLM method tripwires are
installed in the new suite. Duplicate results across two searches trigger one
detail retrieval; irrelevant metadata triggers no detail retrieval. Equivalent
rendered content produces one usable passage. Restart repeats no request work;
positive exact reuse and independent negative reconsideration still pass.

Commands, actual output and seven corrected baseline failures are retained in
`tests.txt` and `baseline-failures.txt`; `proof.json` pins exact source/test bytes.
Code-only `graphify update .` refreshes the engineering graph without semantic
extraction. The earlier Claude execution probe in this session was unavailable
(`Not logged in`); the authorized offline work proceeded locally.

This proves the supported academic metadata/abstract path, not exhaustive web
coverage or calibrated semantic relevance. The pipeline has no expensive
reasoning stage or paid capability to enable; future reasoning remains separate
work. SEC-01 stays deferred for this release and required for future live access.
