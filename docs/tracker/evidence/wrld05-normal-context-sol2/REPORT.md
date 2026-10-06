# [SOL-2][WRLD-05] normal typed WorldModel context closure

Engineering result: **CLOSED**, 2026-10-06. Baseline: `ee9e69d`.
Dependencies reused: WRLD-03 canonically CLOSED at `1a2424b` and DATA-05
canonically CLOSED at `ee9e69d`. No prerequisite redesign or proof rerun.
No live Kernel, provider, venue, order or Dashboard calls.

## Existing evidence mapped first

| Evidence | Classification at intake |
| --- | --- |
| `world/model.py` typed scope/horizon observation and claim queries, exact hierarchy/conflict support; `world/replay.py` retained WorldModelRecord / WorldHistory | PROVEN by WRLD-03 package at 9e49c71; standalone support reused |
| `observability/world_producer.py` → normal Store.write → persisted scan receipt; Attention uses the supported instrument-volume observation | PROVEN, bounded to the existing opt-in supported component; no production claim emitter inferred |
| Historical reconstruction / retained receipt ids and hashes | PROVEN standalone; consumer integration IMPLEMENTED_BUT_UNMAPPED until traced |
| Compiled strategy entries/live evaluator, FeatureCtx, research Bundle/loaders/threshold/evaluation contexts | ACTUAL_GAP: no typed WorldModel input/query in these normal interfaces. Reused `../dec01-chain-sol1/world-normal-path-gap.json`, confirmed against baseline signatures/source |

## Demonstrated gaps and changes

1. Normal strategy/research consumers could not query retained WorldModel
   context. `world/context.py` now supplies the shared immutable `WorldContext`
   and typed `ObservationQuery`. It invokes public `WorldHistory.get_exact`
   and `WorldModel.get_observations`, retaining query cut, scope, horizon,
   kind, model identity, original observations, quality and reason.
2. A normal retained source adapter was missing. Public
   `observability.store.read_world_history` reads existing scan receipts in
   one `mode=ro`, `query_only` transaction. Record reconstruction verifies
   canonical bytes, receipt record/model ids and all stored cuts. Missing
   retention remains absent; corrupt/ambiguous sources become UNKNOWN.
   The consumer never enumerates private WorldModel fields or scrapes
   journal/producer internals. SQLite receipt decoding belongs to the Store
   persistence boundary. No new parallel WorldModel store is introduced.
3. The compiler and research paths had no common feature contract.
   `FeatureCtx.world`, `Snapshot.world` and `Bundle.world` use WorldContext.
   Registered `world_observation(kind, horizon)` queries exact instrument
   scope and supplies numeric observations through the ordinary DSL. Its
   derived requirement is `world`; specs cannot misdeclare it OHLCV-only.
   Feature cache identity includes the frozen world context identity.
4. Missing history must not become a signal or statistical evidence.
   UNKNOWN/non-numeric/conflicting/stale values become NaN with typed-query
   receipt metadata. Compiler entry/exit masks prevent `not` or `!=` from
   promoting absent context. Historical admission/research evaluation
   reports insufficient WorldModel coverage as UNTESTED with context gaps.
   Research numeric/boolean gauges retain NaN through complement/comparison;
   independent candle gauges do not inherit another gauge's missing-world
   mask. Threshold measurement retains its existing finite-data gates.

## Normal caller paths proved

- Strategy: existing `CompiledStrategy.to_evaluator` → `entries` → FeatureCtx
  → shared WorldContext → public exact-cut WorldModel query. A world-dependent
  evaluator reads the retained default `ROOT/data/attention.db` source when
  no explicit typed context was supplied. Live signal params retain the
  context identity, query cut and source status. Other strategies do not read
  this source. Current final-row queries use the explicit Snapshot cut.
- Historical strategy: ordinary compiled entries resolve retained context
  and query each bar's close. An explicit WorldContext also works through
  the same entries API; a raw live WorldModel is rejected as that input.
- Research: `load_bundle` → frozen WorldContext (`paths.world` overrides the
  same retained source) → `evaluate`/compiled entries and `job._ctxs`/threshold
  measurement. Referee historical bundle loading and portfolio-null entry
  generation pass the same typed context; discovery A is cut-bounded.
  Research's actual/null entries share the frozen context. No held-out
  research evaluation or real-data backtest was performed for verification.
  An explicit detached dataset path set lacking `paths.world` returns UNKNOWN;
  it cannot silently acquire WorldModel context from today's production source.

Exact-cut means exact: no nearest/latest snapshot, today's learned state,
live producer, network or guessed aggregation fills a missing historical cut.
Replay rows keep their own closed-bar cuts even when queried from a later
current evaluation. Future records cannot affect a past exact-cut query;
restart reopens the same retained bytes. Missing historical coverage is
UNKNOWN, not zero evidence or proof of no edge.

The supported new numeric DSL query is instrument observation context.
Other source families, live-only measurements, claim-derived numeric features
and scoped htf/ref/cross-sectional wrappers are not asserted as supported
historical WorldModel inputs. Unsupported wrappers/horizons are explicitly
refused. No strategy spec, producer flag, salience formula, confidence rule,
Risk limit, registered research gate or execution behavior was changed.
This is offline architecture/engineering closure, not production deployment
or real historical coverage/calibration proof. The production producer remains
opt-in; finite operational retention can legitimately leave older cuts UNKNOWN.

## Tests and checks

- `test_wrld05_normal_context.py`: **29 passed**. Normal upstream capture and
  Store.write generate the retained records; strategy live evaluation and
  research query/evaluation/threshold/null callers consume them. Tests deny
  network. Covers source restart/frozen JSON, exact current/as-of parity,
  historical bar cuts, future records, retention deletion, missing source,
  wrong scope/horizon, stale/nonnumeric/conflicting observations, corrupt
  record/cut/id, ambiguous same-cut records, source identity in caches,
  direct-live-model refusal, complements, UNTESTED coverage, and unsupported
  query wrappers. Three-bar routing proof explicitly makes no performance claim.
- Final combined focused run: **125 passed in 23.93s** (29 new checks,
  4 existing evaluation-context, 30 WRLD-01 measurement, 53 DSL and
  9 research-threshold checks).
- Regression run: **155 passed, 2 deselected in 78.61s** across compile,
  features, DSL, research evaluate/job/portfolio-null/referee and spec evidence.
  The 53 DSL checks overlap the final focused run. Thus **227 distinct
  checks passed**, without double counting.
- Initial regression: 143 passed, 2 failures. The two research-job fixtures
  load zero symbols/cut from their legacy candle stores. Both failures also
  occur using the baseline `ee9e69d` research module, loaded only in a
  separate test process. Exact baseline assertions are retained in
  `baseline-research-job.txt`; they were excluded from the final run and
  are not counted as passes. No unrelated loader repair was made.
- Scoped `git diff --check`: PASS. Code-only `graphify update .` performed,
  no semantic extraction/API cost. Output is retained in `graph-update.txt`.
  Generated graph files were already dirty and are excluded from this commit.

## Closure impact

| Consumer / dependent | Assessment |
| --- | --- |
| DEC-01 | **NOW UNBLOCKED: NO**. WRLD-05 engineering dependency is satisfied, but STR-02 retains its STR-01 gate. DEC-01's own required/optional analyst and supporting/opposing Opportunity Context binding still needs closure; WRLD-01's typed measurement producer is not that binding. Current `portfolio/opportunity_live.py` source roles/required_roles do not provide this analyst contract. No DEC-01/DEC-04 change made. |
| STR-02 | WorldModel consumer gap repaired; normal compiler version/evaluation regressions pass. STR-01 remains EVIDENCE_TO_MAP, with RES-08/QNT-08 dependencies. STR-02 is not automatically CLOSED. |
| Research consumers | Shared typed retained input now reaches loader, threshold measurement, ordinary evaluation, referee bundle and portfolio-null entries. Unsupported/missing history remains unavailable/UNTESTED. No research gate, admission threshold or held-out evidence claim changed. |

Shared tracker/STATE/NEXT/manifest files contain unrelated pending work and
are left untouched. WRLD-05 control-plane reconciliation remains pending;
this package closes its engineering gap and records the exact evidence only.
