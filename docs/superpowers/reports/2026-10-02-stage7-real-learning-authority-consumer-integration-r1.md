# Stage7 real learning authority and consumer integration R1

Package: `LUFFY-STAGE7-REAL-LEARNING-AUTHORITY-CONSUMER-INTEGRATION-R1`.

STAGE7_REAL_LEARNING_AUTHORITY_CONSUMER_INTEGRATION_R1: PASS. SAFE_TO_REVIEW: YES.

Worktree: `/mnt/luffy-data/luffy/workspaces/stage7-real-learning-authority-consumer-integration-r1`.
Branch: `luffy-stage7-real-learning-authority-consumer-integration-r1`.
No commit, deployment, production adaptive mutation or order submission was performed.

## Lineage and authority

Corrected Stage6 base: `640a5f20a8f78bf9ac68f1ac26d5c6c0de6977c3`.
Latest completed Stage7 source lineage: `9d2a1b8`, following `9d31fbd`, `d400394`, `1c77fdb`, `503d93c`, and `481b832` after `d256bfb`.
The Stage7 source, scripts, tests and historical evidence were integrated by file with three-way conflict resolution. STATE/NEXT were preserved from the corrected base and edited for this package; no stale state files were copied. Missing research, memory and signal-observation prerequisites required by the integrated lineage were selectively restored. There is one implementation of each learning component.

The corrected Stage5/Stage6 control fence, Risk release, strategy install and Governor authority remain in place. Learning's bound transaction is restricted to StrategyGovernor retirement and exact target comparison. Activation and resume still require the corrected control/Risk path. Risk implementation, configuration and StrategySpec source bytes match the corrected base.

## Owners and normal consumers

All adaptive state begins absent. There are no populated learned defaults. Context identity is exact across asset scope, horizon, proven regime or explicit non-applicability, evidence type, direction, strategy family and subject. Unknown context yields no learned adjustment. Reads expose revision and content hash. Updates compare the exact prior revision/hash, re-evaluate the verified proposal under a registered rule, and commit an append-only owner revision with an immutable application receipt in one transaction. SQL triggers reject unauthorized insertion, updates and deletion.

| Target | Typed owner in `trader/learning/targets.py` | Writer | Normal downstream consumer | Production rule |
|---|---|---|---|---|
| confidence_calibration | contextual Reliability | LearningApplication → compare_and_apply | Orchestrator evidence aggregation/net_score, exact context | POLICY_UNAVAILABLE |
| strategy_allocation | contextual AllocationBound | LearningApplication → compare_and_apply | Portfolio current source cut → existing Allocator context/resource bound | POLICY_UNAVAILABLE |
| attention_priority | contextual PriorityState | LearningApplication → compare_and_apply | Attention selection after unchanged eligibility/salience calculation | POLICY_UNAVAILABLE |
| research_priority | contextual PriorityState | LearningApplication → compare_and_apply | normal research_pass planning order before bounded case selection | POLICY_UNAVAILABLE |
| evidence_weights | contextual EvidenceReliability | LearningApplication → compare_and_apply | exact-context vote aggregation and net_score; raw evidence retained | POLICY_UNAVAILABLE |
| world_model_learning | contextual ClaimConfidence | LearningApplication → compare_and_apply | explicit WorldModel learned_claim_confidence query | POLICY_UNAVAILABLE |
| strategy_lifecycle | existing immutable StrategyVersion and Governor events | LearningApplication → StrategyGovernor RETIRED | existing version/lifecycle readers | existing-recent-decay.v1 |

Confidence is contextual, not a global scalar calibration. Legacy calibration is historical input only. Evidence projections retain raw confidence/conviction and provenance. WorldModel learning is a separately versioned layer: no historical snapshot is rewritten, no Bayesian model was added, and consumers query it explicitly. Allocation learning neither sizes positions nor bypasses economics, capacity or Risk; a zero resource bound can exclude a future new allocation, while nonzero values remain context until a unit-comparable calibrated policy exists. Existing live positions are not resized.

The six production adaptive registries are empty and immutable. Isolated tests use a registry argument only on a Journal explicitly marked test-only, outside production data, with no live trades. Normal runtime has no test-registry argument and never dispatches these test rules.

## Normal source capture and runtime application

The normal Portfolio checkpoint delivers an exact new allocator-decision manifest from the actual sources it used: immutable WorldModel and Opportunity Context receipts, portfolio cut, configuration/Risk/control versions, exact StrategyVersions, proposal and applicable intents, economics and market inputs. It does not depend on optional `snap.learning_sources`, infer absent sources or backfill earlier decisions. Missing objects remain UNAVAILABLE.

The Analyst's normal review calls the prospective recent-decay producer. It measures the exact installed version against copied frames and a copied configuration, retains the evaluator manifest and registered measurement, creates replay-complete LearningEvidence and enqueues the registered proposal. `DECAY_EVALUATION` explicitly describes evaluator inputs; it does not fabricate trading WorldModel/context dependencies. The existing decay formula, configuration thresholds and timeframe normalization are unchanged. Normal observation time is measured after evaluation.

The Kernel's normal end-cycle checkpoint consumes a bounded, deterministically ordered queue. Only production-registered rule/target pairs are selected. Request bindings include source configuration/code versions and the exact owner target. The owner and receipt commit atomically; restarts skip receipted proposals. Stale/conflicting targets and insufficient verified evidence cannot mutate. No LLM or venue API is involved. Work defaults to eight queued applications and has a validated finite maximum.

## Legacy adaptation

All three bypasses are classified `LEGACY_EXPLICIT_ISOLATED`:

- `strategy_weights`: reads an explicitly frozen legacy snapshot if retained, otherwise the neutral static fallback; no new outcome-driven computation is invoked by the runtime read.
- regime-conditioned `adaptive_base`: reads explicitly frozen retained state or the existing static base; it does not query fresh outcomes.
- `accuracy_multipliers`: reads explicitly frozen retained state or the existing empty fallback; it does not query fresh outcomes.

Historical pure calculation functions may remain for historical analysis. Future adaptive writes through these old routes are blocked by the integrated legacy learning gate. No production state was frozen, migrated or rewritten during this package.

## Validation

662 selected checks are verified: 661 passed in the combined run (511.88 seconds), and its one historical static inventory failure passed after correction (1.34 seconds). The original failure log is retained rather than overwritten. The new integration suite contains 17 passing scenarios. See `validation.json`, `combined-tests.txt` and `static-inventory-final-tests.txt` in the package evidence directory. The selected suite covers learning foundation, application, verified outcomes, legacy replay gates, blending, unchanged rolling evaluation, Stage6 normal sources/runtime, Attention persistence, allocation/candidate bridges, corrected activation/Risk and universal strategy authority, factory lifecycle and decision-source replay.

The new integration suite proves all six targets through verified LearningEvidence → test-only proposal → LearningApplication → actual owner revision → actual downstream observation. It also proves no-rule refusal, stale CAS conflicts, insufficient evidence, duplicate application, restart, read-only shadow, immutable receipts/revisions, raw SQL/write refusal, inaccessible normal test registries, unchanged Risk/StrategySpec and no venue/order calls. Attention replay retains its exact priority snapshot after later owner revisions. Lifecycle uses the normal prospective producer and automatic registered checkpoint to retire through the Governor in an isolated deterministic Journal.

The combined run needed a per-process file-descriptor limit of 8192 because many SQLite fixtures retain connections. One historical static caller-inventory assertion omitted existing corrected Stage6 readers. Its correction explicitly permits only `Portfolio.current.load_version`, the Kernel install guard's `versioned`, and the paper probation observation calls; activation writer allowlists are unchanged. Existing negative authority controls remain exercised.

Graphify was refreshed with `graphify update .` using AST-only extraction, without semantic/API extraction. Generated graph/cache outputs are listed separately in the exact changed-file manifest. Claude CLI verification was unavailable because its local session was not logged in; direct implementation and repository tests were used.

## Real read-only shadow

Source: `/mnt/luffy-data/luffy/production/data/luffy.db`; SQLite read-only/query-only access, no schema creation or migration. Artifact: `docs/superpowers/evidence/stage7-real-learning-authority-consumer-integration-r1/real-shadow.json`.

| Count | Result |
|---|---:|
| proposals observed | 0 |
| complete replay | 0 |
| registered rule eligible | 0 |
| would apply | 0 |
| policy unavailable | 0 |
| stale/conflict | 0 |
| production mutations | 0 |
| venue calls | 0 |

The shadow is PASS for read-only execution and reports `NO_REPLAY_COMPLETE_EVIDENCE`. Legacy decisions were not backfilled or manufactured into LearningEvidence. Zero proposals does not prove that any real update is eligible. Isolated tests, rather than the empty real shadow, establish the architecture paths.

## Review disposition

Missing architecture: NONE after the documented validation passes.
Policy/calibration blocked: confidence_calibration, strategy_allocation, attention_priority, research_priority, evidence_weights, world_model_learning. These require separately approved calibrated production rules. The registered existing lifecycle rule is retained unchanged.

Production trading behavior changed: NO. This code is isolated and undeployed; production adaptive state was not mutated. The proposed code deliberately isolates future legacy outcome-driven adaptation. Real order submissions: 0.

STATE_UPDATED: YES. NEXT_UPDATED: YES. Next recommended package: `LUFFY-PREDICTIVE-RESEARCH-BRIDGE-R1`; not authorized by this recommendation. Frozen research/pilot constraints remain intact.

Exact files, hashes, final tests and status are recorded under `docs/superpowers/evidence/stage7-real-learning-authority-consumer-integration-r1/`.
