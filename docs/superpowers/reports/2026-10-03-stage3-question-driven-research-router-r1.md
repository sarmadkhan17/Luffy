# Stage3 question-driven research router R1

Result: **PASS** for `LUFFY-STAGE3-QUESTION-DRIVEN-RESEARCH-ROUTER-R1`.
Narrow Stage3 missing architecture: **NONE**. This is TESTED architecture,
uncommitted and undeployed; it is not a production usefulness or predictive-edge claim.

Worktree `/mnt/luffy-data/luffy/stage7-r2`, branch
`luffy-stage7-normal-replay-world-consumer-closure-r2`, unchanged baseline HEAD
`06d331b6c86566e4bf8220984a30bea75ce99975`.

## Implementation and existing boundaries

The normal opt-in asynchronous consumer reads registered `research-question.v1`
questions and verifies their registration envelope, row projection and original
health evidence through the existing question contract. Strategy-decay questions
route to `academic_factors`; unsupported kinds, scopes, classes and source IDs
are refused. The router selects sources and does not decide truth.

The external catalog reuses `research_sources` fact states and all SDD 12.4
metadata fields. Its first adapter uses public Crossref scholarly metadata.
Unknown credibility, historical coverage and provider rate limits remain
explicitly unassessed/unmeasured. The internal registry and pinned hash are
unchanged. Public endpoint/access documentation:
[Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/).

The Search Broker uses the existing low-priority spawned child/deadline runner
for bounded HTTPS JSON metadata search and DOI metadata retrieval. It follows
no redirects, loads no ambient credentials and performs no paid or LLM calls.
Title relevance, canonical URL and title dedup run before page retrieval.
Content dedup precedes passage extraction using existing DeepCrawler
`html_to_text`; missing abstracts and extraction failures retain explicit status.
Search metadata and unverified excerpts are evidence context, not validated claims.

External objects use the **existing** `research_bank_objects` and
`research_registrations` stores. Journal family reads preserve existing Bank
and recall verifiers; external rows are available through
`research_bank_objects(schema='external-evidence-bank-object.v1')` and the
external worker's `verify_bank`. Each object links the exact question, route,
source descriptors/hashes, queries, request/checkpoint IDs, source/retrieval URLs,
retrieval/availability times, response and passage hashes, extraction statuses,
limitations, next question and cost/status. Claims/support/refutation remain
unavailable/unclassified; the conclusion remains INCONCLUSIVE.

The existing predictive bridge only accepts its own verified investigation Bank
chain. It cannot consume this external contract as predictive evidence; its
provenance, experiment and referee gates remain unchanged. There are no new
callers from Kernel, decision, Risk, Execution, StrategySpec or StrategyVersion.

## Resource and recovery behavior

Defaults per question: 1 metadata query, 3 document requests, 10 metadata items
per query, 131072 retained response bytes per request, 2000 excerpt characters,
30 seconds of retrieval-worker time, $0 paid requests and 0 LLM calls. Validated
hard ceilings: 2 queries, 8 documents, 20 items/query, 262144 response bytes,
4000 excerpt characters, 60 seconds, 4 questions/pass. Streaming discards
over-limit responses; detecting overflow can receive one additional bounded
8192-byte chunk, which is counted. Child termination uses the existing bounded
cleanup allowance. One-second source spacing applies across passes.

The existing journal event store retains hashed request-start/result
checkpoints, with at most 8192 checkpoints and 1 MiB/checkpoint. External Bank
objects are capped at 1024. Question history verification refuses more than 512
source rows or oversized source records. A nonblocking process lock prevents
two workers from doing the same work concurrently.

Completed requests reuse their exact retained results and original timestamps.
A request claimed before a process interruption is **not** automatically
retried: recovery files `INTERRUPTED_REQUEST_NOT_RETRIED` as explicit unavailable
evidence. Empty and failed research become retained Bank memory, and normal
passes skip already-filed questions. Cache reuse is not a freshness claim.
Network/byte counters apply to the current pass; total operating cost is unknown.

Normal invocation, for subsequent separately authorized operation only:

```bash
python -m trader.research.external_research --enable --once --journal PATH
```

Without `--once`, the separate research process checks at a bounded cadence;
it does work only for registered unfiled questions. Without `--enable`, it
creates no Journal and makes no network calls. No worker was enabled/deployed.

## Acceptance evidence

| NEXT acceptance | Evidence in `tests/test_external_research_router.py` |
|---|---|
| Registered question selects classes without supplied URLs | normal question/router/provenance test |
| Bounded external retriever and exact provenance | normal Bank test and offline HTTP metadata/detail parsing test |
| Deterministic filtering and URL/content dedup | metadata/content dedup and canonical URL tests |
| Structured existing Research Bank | normal Bank test and mixed legacy/external Bank/recall regression |
| Unsupported question/source fails closed | unsupported/paid/definition-mismatch tests |
| No network in the live decision loop | unchanged live callers and worker import-boundary test |
| No trading authority change | trading tables unchanged; no new strategy/engine imports; single creation-path regressions |
| Predictive provenance/quant gates preserved | external Bank refusal test and existing predictive bridge regressions |
| Restart/idempotency and resource bounds | completed restart, interrupted claim, negative memory, worker-lock, storage-capacity, byte/page/query/time and real child-deadline tests |

Final focused suite: **450 passed**, including **27 new acceptance tests**.
See [test evidence](../evidence/stage3-question-driven-research-router-r1/tests.txt).
Compile checks and `git diff --check` passed. The AST-only graph update uses no
LLM/API extraction; existing dirty graph/evidence artifacts were preserved.

The final regression run also corrects two stale fixtures/references in the
current lineage: the pinned internal registry's STATE weakness reference and
the old health test Journal's already-required exact-version query. Production
Analyst and Stage5-7 code were not modified.

## Completion boundary

STATE records Stage3 architecture closure while retaining production/evidence
maturity gaps. NEXT preserves the exact completed package and its acceptance
criteria, and recommends `LUFFY-STAGE8-OWNER-OS-CLOSURE-R1` for later work.
Stage1/2/4 maturity continues independently. Stage5-7 remains frozen.
No commit, deployment, restart, trading activation, venue order, Scheme-D,
cloud compute or paid spend was performed. No paid Claude development call
was invoked under the session's no-spend constraint.
