# STR-04 canonical BLOCKED

Engineering `872131f`; verification pinned to `516638e53b410ca7244f232c896ce4176a35a861`. STR-03 is canonically CLOSED at `516638e`.

The exact condition remains unsatisfied: Existing approved version lifecycle changes are bounded, atomic, idempotent and linked to evidence/owner limits.

Required proof 2 fails on an ordinary supported path. An approved version with an exact owner decision can take its first Governor transition directly to PAUSED, DEGRADED or RETIRED. All three new events store `owner_decision_id: null`; `governor_events` replays them successfully. The approval request is bound, but its owner decision is not. The writer takes the decision from prior Governor history and only resolves the current decision for ACTIVE/REACTIVATED. This is independent of the privileged-writer limitation: the reproducer sets no internal flag, drops no guard and rewrites no event. See [reproducer](binding-proof.py) and [results](binding-proof.json).

The 26 dedicated STR-04 tests and 44 STR-03 tests passed together (70 total). Existing lifecycle tests exercise pause/degrade/retire after ACTIVE, which already supplies owner decision identity. They do not cover the failing approved pre-ACTIVE paths.

**Sole authority.** govern_version writes Governor events and projects versioned strategies.state. DB triggers refuse lifecycle version events, raw Governor inserts and ungoverned versioned row state edits/deletes/replacements. Other state writers remain legacy-only for versioned ids.

**Exact binding.** New events carry version/spec, strategy/lineage/install/request binding, transition_id/key and requested allocation. Post-ACTIVE lifecycle events inherit owner decision. First approved non-activation events have the blocking omission.

**State machine.** GOVERNOR_ALLOWED below is exact. Dedicated tests verify retired terminality, unapproved activation refusal and stale probation reactivation refusal.

**Atomicity.** Both injected strategies-row UPDATE failure and Governor-event INSERT failure roll back event and projection; retry succeeds after fault removal.

**Idempotency.** Latest duplicate returns identical event; explicit earlier transition key replays prior event, conflicting key refuses; restart retains exact history/state.

**Projection.** ACTIVE/REACTIVATED active; PAUSED/DEGRADED paper; RETIRED retired. Divergence is detected and live_entry_block refuses.

**Activation.** ACTIVE/REACTIVATED consume eligible_for_first_live with exact approval/install/available inputs/current capacity, authoritative same-journal Risk release, matching Risk policy and owner ceiling/risk hash. Full approval config_sha256 is rechecked through STR-03.

**Immutability.** Dedicated snapshot covers versions, validation, install, probation, approval request and decision across full lifecycle. Lifecycle API accepts no spec/lineage/evidence rewrite; same-id spec rewrite blocks live entry; changed semantics derive a new version.

**Bypass.** Dedicated refusal evidence includes raw writes, version-event lifecycle insert, label/grandfather, sibling and stale approval; rewritten installed semantics cannot authorize entry.

**Execution boundary.** ACTIVE alone preserves version_first_live_execution_not_enabled; Kernel consumes live_entry_block.

Exact allowed transition matrix (all other transitions refuse):

| From | Allowed targets |
|---|---|
| PROPOSED | DEGRADED, RETIRED |
| VALIDATED | DEGRADED, RETIRED |
| SHADOW | DEGRADED, RETIRED |
| APPROVAL_REQUIRED | DEGRADED, RETIRED |
| APPROVED_FIRST_LIVE | ACTIVE, DEGRADED, PAUSED, RETIRED |
| ACTIVE | ACTIVE, DEGRADED, PAUSED, RETIRED |
| REACTIVATED | ACTIVE, DEGRADED, PAUSED, RETIRED |
| PAUSED | DEGRADED, REACTIVATED, RETIRED |
| DEGRADED | PAUSED, REACTIVATED, RETIRED |
| RETIRED | None (terminal) |

REJECTED has no Governor transitions. Retry may return an already-recorded event without a new transition.

Limitations retained: Pause/degrade/retire reason strings are bound but not registry-validated. DB guard is flag-based, not cryptographic protection against privileged code deliberately setting governor_write. STR-05 owns what evidence may trigger degradation, retirement or recovery.

Retained broader engineering log: 682 passed / 3 failed. Identical clean-baseline reproduction is supplied engineering evidence; a separate baseline log is not independently retained here. No full-suite green claim; counts overlap. No live/provider calls or runtime activation.

STR-04 remains BLOCKED; all 177 other rows, STR-03 closure, original conditions/dependencies, readiness gates and runtime state are preserved. DEC-03 dependencies are not satisfied and it is not eligible, selected or started.

Next action: In a separate authorized engineering task, bind the existing exact owner decision for approved versions even before the first activation, verify the binding on replay, and add regression evidence for these paths. Implementation is unchanged by this task.

Counts: CLOSED 62, EVIDENCE_TO_MAP 76, DEFERRED 7, AWAITING_EVIDENCE 9, AWAITING_OWNER 14, BLOCKED 6, OPEN 4 (178 total). Control-plane commit: enclosing Git commit.
