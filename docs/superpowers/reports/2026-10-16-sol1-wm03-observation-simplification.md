# WM-03 complete offline observation simplification

WM-03 removes demonstrably unused bridges and an exactly redundant serializer
wrapper. The active observation models have different necessary semantics and
remain separate. No new priorities, weights, policies, decision authority,
wire schemas, database migrations or evidence rewrites are introduced.

The baseline is DA-04 preservation head
`37383dcf6545490587ba31db35d56ed4e45a61bc`. The isolated implementation is
`eff6fdc3a969ad08873a1b66711f413a6d96b005`.

## Lineage and scope

The six preserved input bundles were independently hashed and verified. The
DA-04 bundle is a complete history, SHA256
`998c7f38bb6e373d55983bc4f44c56d6694765c4d7150d765dd62a447d9d2be9`.
Ancestry includes unified RP-01 `6bc0f4cc`, DA-08 `0b6f6067`, RP-03
`2e447489`, the reconciled DA-09 runtime `f32726d5`, and both DA-04 runtime
commits `d31bab4a` and `553dc423`.

The original DA-05 queue/readiness methods match the pre-DA-04 reconciled
head; DA-04 intentionally changed worker persistence/refusal handling later.
WM-03 preserves DA-04's complete feed file exactly. DA-05's Attention history
module and DA-09 closure tests match their independently restored bundle
versions. `input-and-scope.json` records hashes, actual ancestry and scope.
DA-04 and DA-05 retain their existing incident/coverage limitations.

The checkout and all test databases were isolated below `/mnt/luffy-recovery`.
No shared integration checkout or Opus RP-02 research-validation implementation
was modified. No provider/LLM calls, production edits, operational Kernel or
Dashboard starts, trades, statistical-budget spending, deployment, integration,
SDD or tracker edits were performed. The repository's preference for delegating
to a provider-backed Claude CLI was superseded by this assignment's explicit
no-provider boundary; no provider availability probe was made.

## Producers, consumers, persistence, replay and decisions

| Contract | Actual producer and consumers | Persistence and replay | Decision |
|---|---|---|---|
| Cognition `contracts.Observation` | `cognition.attention.evaluate` produces decision-scoped candle windows, salience components, breadth, participation, positioning and correlation diagnostics. `hypotheses`, `replay`, `investigation.make_state`, Attention payloads and `data.attention_history` consume them. | `cognition.replay` JSON traces; Attention scan payload `observations`; Investigation `adapt` re-evaluates exact joined versions and compares observations, rows and market. | KEEP. Mutable build-time `status` (including a later degenerate-window classification), optional event/availability clocks, `*` market symbol, diagnostic statuses and 12-significant-digit trace values differ from World evidence. |
| World `observation.Observation` and `ObservationIdentity` | `observability.world_producer` builds closed-volume windows and derived perceptions; perception and World APIs support other typed evidence and relationships. Attention `_world_volume`, claims, relationship evidence, exact-cut strategy/research context and intelligence traces consume them. | Immutable nested JSON data; stable event/provenance IDs plus full record hashes; `WorldModelRecord`, retained Attention scan receipts and `WorldHistory`; reconstruction verifies IDs and evidence bytes. | KEEP. Full precision, typed quality, source refs, explicit capture/event/availability/freshness clocks and exact instrument/timeframe/horizon are required. Identity is not a second measured payload. |
| World `state.WorldState`, `model.WorldModel`, `context.ObservationQuery` | World producer and reconstruction compose typed observations under exact instrument/hierarchy/cut coordinates; context queries feed strategy/research and Attention. | Canonical model/state/record IDs and retained exact cuts, including conflicts and non-VALID quality. Query adds explicit unavailable/ambiguous reasons. | KEEP. Composition and query results are not redundant observations. |
| World `perception.Measurement` | A pure declared perception transform returns this small result; `perceive` binds it to the resolved input observations and creates canonical World `Observation`. | Result is wrapped with provenance; the unbound transform result is not a competing persisted schema. | KEEP. Replacing it with Analyst Measurement or a fully bound Observation would invent identity/clocks or duplicate provenance construction. |
| Analyst `agents.measurement.Measurement` | Enabled analysts build typed immutable packets via `agents.base._vote`; Orchestrator validates and consumes packets; `cognition.decision_analysts` checks per-decision identity and required/optional supporting/opposing evidence. | Legacy Vote metadata, journal votes, frozen decision source dependencies and Learning capture; strict `from_dict` reconstruction and exact record digest. | KEEP. Input declarations, exact source digests, limitations, rationale, heuristic uncertainty and signed strength are not equivalent to scalar World values. Vote is the compatibility scoring projection, not an independent evidence authority. |
| Investigation `StateSnapshot`, `Dimension`, `InputBar`, `Measurement`, outcome/update records | `cognition.investigation` freezes registration-time state, catalog questions, alternatives, baseline versions, deadlines and subsequent observed outcomes; observability Investigation persists and research consumes these cases. | `cases`, `inputs`, `case_inputs`, immutable updates, retained context evidence and allocation decisions; exact Attention input-version joins and protocol/catalog bindings. | KEEP. Its Measurement is a prospective research measurement plan, not an Analyst packet or a World transform result. |
| `world.SnapshotObservation.price` | No tracked executable producer/consumer, call, test or script uses it; only definition and public re-export existed. | No stored type, replay decoder, schema or serialized reference uses this class. It merely manufactured a SUSPECT price wrapper from a legacy capture timestamp without event/availability clocks. | REMOVE class and package export. Canonical World Observation and actual source-aware producer remain. |
| `learning.consumers.attention_order` | No tracked executable caller, test or script. The real evaluator already reads and freezes temporal Attention state, verifies application proof, then stably ranks eligible rows. | None for this wrapper. Frozen `governed_attention_state` remains unchanged. | REMOVE unused wrapper. Its non-temporal latest-state convenience API is not the historical Attention evaluator contract. |
| `cognition.contracts.to_dict` | Attention and cognition replay callers, plus the WM-01 parity test. | The entire implementation was exactly `return dataclasses.asdict(rec)`. | MERGE with stdlib canonical implementation by importing `asdict as to_dict`. Existing callers and name stay compatible; recursive nested detachment remains exact. |
| Adaptive target registry, Rule, application and temporal consumers | `learning.dispatch`, `runtime`, `foundation`, `adaptive_application`, Attention and research scheduling use these contracts. Production `targets.PRODUCTION_RULES` is empty. `research_order` still calls `targets.priority_order`. Isolated tests inject rules under existing test-only/no-live/data-path fences. | Append-only target revisions, application proofs, immutable dispatch/refusal receipts, exact contextual CAS and frozen replay state; historical revision reads are meaningful even without a current production rule. | KEEP. Empty registration is an enforced no-policy/refusal boundary, not proof of dead executable machinery. No test-only learning capability or actual historical/governance consumer is removed. Foundation's registered lifecycle decay rule is distinct and unchanged. |
| Exit-policy bar Observation and complete PortfolioObservation | Versioned exits and portfolio servicing/recovery use completed OHLC bars and exact complete venue-position snapshots respectively. | Existing execution/recovery contracts. | KEEP. Distinct safety semantics outside the observation duplication targeted here. |

The overlap between the normal World volume z-score and Attention's volume
component was already addressed in WM-01: exact dataset-local Candle identity
shares the arithmetic. The raw input window, immutable World perception and
rounded decision-scoped Attention record still serve different provenance,
quality and replay purposes. External World values cannot replace the local
Candle measurement or admit an asset without its required return history.
Removing either stored record would change frozen source contracts and lose
useful evidence. Those paths and the WM-01 negative controls are preserved.

No other genuinely duplicate active representation or obsolete executable
adaptive-priority rule was established. The inspected production adaptive
registry contains no priority rule to remove. The safe simplification is
complete; further forced consolidation is explicitly rejected by WM-03's
KEEP rule.

## Authority and parity

Source observation records remain evidence, never trading permission. Attention
owns salience ranking and its existing admission policy. Governed priorities
can only be read from already-qualified exact contextual state; production
policy availability remains unchanged. Investigation owns research questions
and measured outcomes, and its transfers still stop at the existing validation
boundary. Strategy signals own decisions, with Risk, Execution, FROZEN and
recovery protections intact. Scope hashes prove those implementation files
remain unchanged.

Ten baseline/candidate differential cases compare 577,159 canonical bytes
exactly: valid data, absent anchor, hole, flat volume, future availability,
wrong instrument, later cut, chronological replay, late arrival and nested
serialization with nulls and micro/full-precision signed values. Their common
SHA256 is `62504f7d2843093e8acf3470ecf604a75053299b6cf273994b14ba50a0e28703`.
A fresh candidate process also reconstructed the baseline's retained World
record byte-for-byte. No stored evidence was upgraded, normalized or rewritten.

The comparison uses one shared captured fixture input, retaining its exact
request and revision IDs. An initial harness attempt generated independent
captures, correctly producing different source identities; it was corrected
before accepting differential results. No application identity rule was
changed to hide that difference.

## Measured reduction and costs

Across the four changed source files: **928 to 893 lines (-35)**,
**37,302 to 36,008 source bytes (-1,294)**, **15 to 14 classes**,
**49 to 46 functions**, and **6,230 to 6,073 AST nodes (-157)**. Byte counts
exclude terminal newlines equally on both sides. These are scoped source
counts, not a claim about the percentage of the entire repository removed.
The diff has three added and 38 removed lines. No new adapter, model, cache,
registry, queue or table replaces the deleted code.

Normal six-symbol scan payload: **63,922 bytes on both versions**; contained
World record JSON **15,951 bytes**; Cognition observation list **34,118 bytes**.
Dormant path deletion saves zero serialized bytes on the normal path. The
remaining separate evidence representations are intentionally preserved.

Seven repetitions, 20 synthetic six-symbol scans each: median wall **12.070 to
10.040 ms/scan**, CPU **11.753 to 10.002 ms/scan**. Seven repetitions of 10,000
four-record serialization batches: median wall **61.821 to 53.702 us/batch**,
CPU **53.202 to 48.329 us/batch**. Full samples and workloads are in
`performance.json`. Concurrent host load and variation prevent a significance
or stable speedup claim. Algorithmic work and normal wire size are unchanged;
only one trivial wrapper frame per Cognition serialization is eliminated.

## Regression and preservation

Final matched tests, independent restore checks, Graphify AST verification,
bundle hash and cleanup are recorded in the accompanying final verification
and return artifacts. All generated graph/cache data, raw test output,
fixture databases, scripts and task-created checkouts are removed after
preserving the verified complete-history bundle and essential evidence.

The scope does not close DA-04's unauthenticated historical snapshot incidents,
DA-05's historical coverage limitations, or Opus's separate RP-02 task.

Matched regression: **967 cases, 963 passed / 4 failed on both baseline and
implementation**, zero skips, identical failure IDs and messages, no new or
fixed failures. The focused observation/learning subgroup is **245 passed /
1 inherited isolation failure** on both versions. These runs used default
SQLite durability, including lock/restart/rollback assertions; no fixture
`synchronous` override was applied. Wall times were 1165.875 and 1166.249
seconds. These totals include large unchanged learning-source captures and
are not processing-latency benchmarks. The entire repository suite was not run.

Inherited failures retained without assertion changes:

- Cognition's old import allowlist rejects the existing required
  `trader.learning.consumers` import in Attention.
- Investigation capacity/retention reaches `max()` on an empty dossier
  result in the unchanged fixture; no new root-cause claim is made.
- Investigation's oversized-source refusal code is
  `SOURCE_PAYLOAD_BOUND_EXCEEDED`; its old regex expects `source_payload_bound`.
- The Investigation Dashboard fixture omits the mandatory explicit
  `dashboard.startup_mode`. No operational server was started.

These are pre-existing baseline failures, not repaired or silently excluded
from the matched 967-case comparison. WM-03 has no remaining justified
simplification work; those unrelated baseline failures and the separately
owned RP-02 work remain outside this closure. The independent bundle restore
and focused checks are recorded externally in `bundle-verification.json`
and `WM03_RETURN.json`, avoiding a circular self-hash in the bundle.
