# Stage3 generated-question handoff and negative memory R1

Package: `LUFFY-STAGE3-GENERATED-QUESTION-HANDOFF-AND-NEGATIVE-MEMORY-R1`.
Result: PASS, uncommitted and undeployed. Worktree: `/mnt/luffy-data/luffy/stage7-r2`;
branch/head remain `luffy-stage7-normal-replay-world-consumer-closure-r2` /
`06d331b6c86566e4bf8220984a30bea75ce99975`. Base is the existing uncommitted router R1.

The worker now reads persisted questions from the existing shadow store through
`GeneratedQuestionSource`. It verifies the original question registration, producer
invocation receipt, immutable source-path binding, bounded health snapshot hash and
existing question derivation contract. The returned question is unchanged. Its
original registration/availability and producer/store provenance are bound into the
external Bank plan. No question is registered or imported into the worker Journal.
The enabled worker CLI requires both generated-question database paths:

```sh
python -m trader.research.external_research --enable --once --journal WORKER_DB \
  --question-shadow-db SHADOW_DB --question-source-db SOURCE_DB
```

These paths are operator inputs, not a URL list. The worker refuses source/store
aliases before initializing its Journal. Existing manually registered question APIs
remain available to tools/tests. The Bank is still the existing Bank table and
registration contract, with schema-specific readers.

The real producer test found that the isolated shadow Journal inherited regular
Bank learning capture, which its existing import guard correctly refused. A small
Journal hook preserves the regular capture body unchanged; ShadowJournal overrides
only that hook, since its research-only database has no learning target tables and
forbids those modules. The actual guarded subprocess now completes and persists
generated questions through the existing research chain.

Request identity now includes the consuming question, registry schema/hash, source
class and retriever version. Every independent question performs its bounded search.
Negative results retain normalized query, operation/arguments, attempt/retrieval and
local availability times, supplied source availability (otherwise unknown), result-set
identity, response hash when available, classification and reason. Original legacy
checkpoint records remain unchanged and queryable. No timestamp TTL or source-state
identity was invented. Restart reuses the same question's committed attempts; an
interrupted claim becomes explicit unavailable historical evidence without another
network attempt for that same work item.

Positive retrieval reuse requires exact metadata returned by the new search to match
both the earlier request's metadata digest and its retained positive response. It
retains the originating request ID, original timestamps and response hash. A positive
cache hit claims retained content, not current source freshness. Empty/unavailable
responses cannot satisfy that reuse rule. New independent searches and their Bank
objects do not overwrite earlier failures.

`ReconsiderationPolicy` / `RetryEligibility` provide a typed future scheduling seam.
The production registry is empty. A test-only registered policy receives an exact
verified historical Bank context and can defer eligibility; it cannot rewrite those
records through this API. Deferral creates no terminal Bank object, so later policy
removal does not inherit suppression authority. Without a policy, independent
questions are eligible by default. Prior failure changes no Attention salience,
strategy confidence, predictive support, allocation, Risk or execution authority.

Validation: 1038 distinct focused cases verified across isolated batches and targeted
corrections, including 12 new handoff/memory cases and 27 external-router cases.
Coverage includes the decay chain, source registry, Bank/recall, unreadable/failed
memory, predictive bridge, Attention/investigation interfaces and unchanged regular
Bank capture. The new E2E starts with health events, invokes the real guarded shadow
subprocess, discovers its persisted question and reaches external Bank evidence using
only an offline retriever fixture. It verifies exact identity, no question import,
completed and interrupted restart, source/question/registration/receipt corruption,
independent EMPTY/UNAVAILABLE reconsideration, retained legacy failure, positive dedup,
test-only policy deferral and no production policy. Trading tables stay unchanged.

Some existing static caller lists and capture-table expectations were stale against
the current branch. Their exact expectations now recognize existing shadow, capture,
trace and predictive bridge readers and the new shadow capture hook; they do not
permit additional live consumers. Shared Bank schema isolation replaces an obsolete
expectation that an unreadable object must cause the decay loader to throw.

An initial combined test process exhausted file descriptors because fixtures retain
SQLite connections; smaller process batches completed. `test_attention_kernel_wiring.py`
cannot collect because this checkout lacks `tests.test_attention_supplemental`;
available cognition Attention and investigation-family interface tests passed. No
unrelated architecture was implemented to repair that missing fixture.

The existing predictive bridge still rejects external descriptive Bank evidence
without a registered transformation, and preserves quantitative/protected evaluation,
Referee and Factory gates. That policy blocker is expected. Bounds, cost receipts,
free-only sources and asynchronous research-side execution are preserved. No LLM,
paid/cloud spend, deployment, trading activation, order submission, Scheme-D or commit
occurred. Risk, Execution, StrategySpec, StrategyVersion, Governor and allocation
authority are unchanged.

STATE records this implementation and corrects the premature router-only formal
closure claim. NEXT is `LUFFY-STAGE3-CLOSURE-R2`, `READ_ONLY_CLOSURE_CHECK`; Stage3 formal
build completion and any Stage8 recommendation await that independent closure.
