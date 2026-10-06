# [SOL-2][WRLD-03] evidence and dependency map

Result: **BLOCKED**, 2026-10-06. Work is confined to WRLD-03 evidence mapping
and read-only impact assessment. No prerequisite or dependent implementation
is changed. Control-plane reconciliation is pending.

Source baseline: `31fc0578ddd6e61d77a3744b41166cdaddec06df`.
The production workspace already contained concurrent DATA-06 edits to
`trader/data/feed.py`, an untracked DATA-06 test, shared control-plane edits,
and dirty Graphify output. Those are not this package's changes or closure
evidence. Claude's required execution probe returned `Not logged in`.
Verification and documentation continued with the available Codex runtime.

## Evidence mapped before changes

| Requirement / evidence | Initial classification | Exact mapping / limits |
| --- | --- | --- |
| Global, asset-class, group and instrument hierarchy | IMPLEMENTED_BUT_UNMAPPED | `trader/world/model.py`: ScopeLevel, HierarchyNode, parent/ancestors/children. Parent level/identity checks prevent invalid hierarchy. Higher nodes are identities; aggregate observations are not fabricated. |
| Relevant state at each hierarchy level | IMPLEMENTED_BUT_UNMAPPED | `WorldModel.get_claims/get_one_claim` query ClaimCoordinate(scope, horizon, dimension) for every declared level. Scope-specific claims carry their own evidence. `get_state/get_observations` deliberately expose instrument measurements only. |
| Multi-horizon dimensions and conflicts | IMPLEMENTED_BUT_UNMAPPED | `WorldState` keeps observations as a canonical multiset; `get_one` refuses ambiguity. `WorldModel.get_observations` selects exact horizons; ClaimCollection keeps distinct contradictory claims and evidence references. No global label or narrative fallback is generated. |
| Relationship state | IMPLEMENTED_BUT_UNMAPPED | `trader/world/relationship.py`: RelationshipCoordinate(source, target, kind, horizon), RelationshipCollection.query/get_one. Accessible through the typed `WorldModel.relationships` collection. Relationships are edges, not an invented fifth hierarchy level. |
| Durable model identity and reconstruction | PROVEN, with bounded historical evidence | `trader/world/replay.py`: WorldModelRecord; `tests/test_intelligence_spine.py` normal scan storage/replay, receipt tamper and rederivation refusals. Stage7 report below proves frozen learned query revisions, using test-only claim emission. |
| Normal Attention producer/consumer | PROVEN, bounded to existing supported component | `observability/world_producer.py` → `observability/attention.evaluate_snapshot` → `cognition/attention._world_volume`. Emits instrument INTRADAY volume observations at exact scan cut, explicit unusable quality, retained record. It does not emit production claims, group measurements, other mechanisms or other horizons. This is not proof of those production measurements. |
| DATA-02 prerequisite | PROVEN by reused item-level closure | `../data02-provenance-lineage/closure.yaml`, commits ccef7e8 + cae1128. Durable receipt reconstruction and fail-closed provenance; no rerun or reopening of DATA-02. Shared tracker status has not yet reconciled that closure. |
| WRLD-01 prerequisite | ACTUAL_GAP in normal packet contract; standalone typed evidence IMPLEMENTED_BUT_UNMAPPED | Observation/PerceptionSpec exist. Normal `agents/base.py` instead specifies `evaluate(Snapshot) -> Vote`; `core/types.py:Vote` mandates side/conviction/confidence/rationale but no input references, quality, uncertainty or limitations. `engine/orchestrator.py` collects those Votes directly. `agents/structure.py` returns a neutral Vote with confidence .2 and no explicit quality on missing history. This does not satisfy the mandatory normal typed measurement contract. No claim of analyst execution authority is made. |

Historical evidence reused with its limits:
`docs/superpowers/reports/2026-10-03-stage7-normal-replay-world-consumer-closure-r2.md`.
It explicitly records test-only claim emission and no production WorldClaims;
its recorded read-only shadow is not a new runtime observation in this package.

## Actual gap and change

No demonstrated WRLD-03 storage/query implementation defect was found.
The missing item-level mapping is resolved by this report and the reproducible
synthetic contract probe. The first six IMPLEMENTED_BUT_UNMAPPED contract
areas above are now **PROVEN by synthetic verification**, not production
multi-mechanism or calibration evidence.

The mandatory WRLD-01 normal measurement-packet prerequisite is not closed.
Repair belongs to WRLD-01 and is outside the instruction to work only on
WRLD-03. Consequently WRLD-03 cannot be declared dependency-complete.
No prerequisite bypass, new indicator, aggregation rule, production claim
emitter or discretionary trading behavior is introduced.

Added only this evidence report, `contract-probe.txt`, its JSON result,
`prerequisite-reproducer.json`, and the validation record.
The probe exercises existing APIs; it is not a new production implementation.
No code was modified, so no Graphify rebuild was required. In particular,
no tracker row, STATE, NEXT or manifest was edited or staged.

The WRLD-01 blocker was reproduced with an empty-history Snapshot through the
existing enabled Structure analyst; `prerequisite-reproducer.json` records
the returned Vote. `trader/kernel.py:124-126` includes that analyst in the
normal roster. The current `config.yaml` contains no `world_model` setting;
the WorldModel producer is opt-in by `observability/attention.settings`, not
proof of a newly observed enabled production deployment. No flag was changed.

## Validation

`./venv/bin/python < docs/tracker/evidence/wrld03-map-sol2/contract-probe.txt`
passes **9 synthetic contract checks**; exact result and model identity are in
`contract-result.json`. Checks cover all hierarchy levels, opposing short and
structural horizons, same-coordinate conflict retention, contradiction refs,
ordered relationship endpoints, no invented aggregate observations, full
record reconstruction, canonical identity, and cut/horizon rejection.

Consumer regression results are recorded in `validation.txt`.
All **147 tests passed in 208.94 seconds** across intelligence spine, Stage7
replay/world closure, cognition Attention, Opportunity Context and historical
learning-cut suites. Tests ran against the shared workspace; they do not
certify another agent's uncommitted DATA-06 changes.

## Impact check

| Dependent | Result / remaining requirement |
| --- | --- |
| ATT-01 | **NOW ELIGIBLE: NO**. WRLD-03 dependency completion requires WRLD-01. ATT-01 independently requires DATA-06; no reconciled item-level DATA-06 closure artifact was available at intake, and its concurrent uncommitted work is not adopted. Existing normal supported world-volume input and exact replay are mapped; real event/ranked candidate completeness remains ATT-01's own proof. |
| WRLD-04 | Hierarchy/horizon/claim coordinate support is mapped. Its provenance, confidence and validity-history requirement still needs its own item-level closure; not automatically closed by this package. |
| WRLD-05 | Structural query/replay support is mapped. Reuse `../dec01-chain-sol1/REPORT.md`: normal strategy/research typed WorldModel integration remains an identified gap. That consumer change is outside WRLD-03. |
| WRLD-06 | No learned layer or historical cut changes. Existing exact-cut overlay regression evidence is checked; dependency WRLD-04 and DATA-05 gates remain independent. |
| WRLD-07 | Endpoint/kind/horizon retention and typed relationship query/replay verified. This does not establish approved relationship estimators, stability diagnostics or real measurements. Its own proof remains required. |

Next bounded owner action: close the WRLD-01 normal measurement-packet
prerequisite, reconcile its dependency evidence, then reevaluate WRLD-03 and
ATT-01 alongside the independent DATA-06 closure. Do not expand this package
into those items or mark their rows closed from API-only evidence.

CONTROL-PLANE RECONCILIATION PENDING: **YES**.
