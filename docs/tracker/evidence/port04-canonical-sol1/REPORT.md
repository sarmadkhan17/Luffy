# PORT-04 canonical CLOSED

Engineering `387d130dedfe259e707ddda77b5eab83aa493aa7`, parent `2ea4a95349062b7ec19cde6263fa8b3e8af45660`; control baseline `b6b6e6016f76d0df4869312b1b7c1a4551ba0918`.

Exact contract: Exact whole-book measurement and lineage identify concentration/common-factor risk for deterministic constraints.

NORMAL_CALLER: current.checkpoint -> current.freeze -> relationship_sources -> common_factor.attach -> Consumer/allocator R4/from_allocator. Real persisted Store.write scan and joined bars are consumed without downstream source injection; independent actual checkpoint test proves delivery and zero order submissions.

ATTENTION_RELATIONSHIP: Exact attention-relationship-input.v1 scan, input-version joins, bars, method, window, peer membership, original as_of_ms, bounded valid_until_ms and evidence/source identities retained. adapt re-evaluates observations at the original cut and refuses evaluator mismatch. Basket measurements are CONTEXT_ONLY.

WORLD_MODEL_LIMITATION: B: world-model capture currently disabled (attention settings default false; config has no enabling key). WORLD_RELATIONSHIP_SOURCE_NOT_CONFIGURED is explicit. No normal production world pair measurement is claimed. Existing WorldModelRecord replay and WRLD-07 measurements remain supported when valid evidence exists.

CONCENTRATION_MEASUREMENT: Exact verified complete venue book plus independently bound runtime metrics supply current gross/per-instrument exposure, heat, per-symbol risk and margin where established. Existing max_open_positions, portfolio_heat_cap_pct, per_symbol_risk_cap_pct, max_position_margin_pct and max_total_margin_pct are checked; missing metrics remain unavailable and excess limits block. Shared proven lineage deduplicates contributors; independence is NOT_ESTABLISHED.

PAIRWISE_ATTRIBUTION_FIX: Pair measurement requires two explicitly attributed instruments, current freshness and exact endpoint set. A one-instrument leave-one-out basket cannot supply pair measurement_ids. Parent counterexample reproduced offline; invalid old attribution receipts refuse on replay and are never rewritten.

MISSING_SOURCE_SEMANTICS: Missing/stale optional scan produces no measurement and explicit unavailability. Unproven pair relationship remains UNKNOWN unless independent exact same exposure/shared lineage establishes EXACT_SHARED_EXPOSURE. NO_ESTABLISHED_RELATIONSHIP for distinct proven lineage is not statistical independence (independent_evidence_count=null); no optional absence establishes a measured pair. Malformed initial optional evidence is omitted/refused; changed frozen sources or replay mismatch refuse exposure authority.

REPLAY: Frozen source hashes/identity, original-cut input joins, observations, expiry and exact receipt reconstruction are checked. Mutated values, another scan, expired or replaced frozen source refuse. Restart reconstruction is deterministic. Invalid pre-fix attribution is refused, not migrated.

R3_R4_COMPATIBILITY: R3 remains explicit historical replay; R4 remains the current allocator and refuses absent/ineligible/stale/mismatched capability. Allocator source byte-identical to PORT-01 parent. Fresh PORT-01 and PORT-04 replay/capability tests pass. No blanket acceptance of invalid historical evidence is claimed.

RISK_AUTHORITY: Risk, risk_intent, entry_authority, executor and Kernel bytes unchanged from engineering parent. Proposal-only/context-only evidence grants no sizing, leverage, order, venue or Risk approval authority. No numeric factor model, penalty, assumed correlation or threshold invented.

PORT01_IMPACT: UNCHANGED CLOSED at ec2a68e. Whole-book/R4 capability tests pass and relevant allocator/metric/authority bytes remain identical to 2ea4a95. The previously separate relationship-source gap is resolved on PORT-04 engineering only; source integration remains pending.

DEC03_IMPACT: UNCHANGED CLOSED. One-expression/conflicting-strategy/lineage behavior passes targeted DEC-02/common-factor tests. DEC-03 is separate engineering 1ec9870, not present in this engineering base; current.py is shared, with disjoint relationship wiring changes; the parent never contained DEC-03 position capture, so this commit does not remove it. Separate DEC-03 runtime/position capture must be retained during future integration. Prior PORT-01 evidence includes 80 DEC-03+R4 integrated verification passes; no new combined-tree certification or application integration claimed.

WRLD07_IMPACT: UNCHANGED CLOSED. Fresh WRLD-07 exact metadata, freshness, invalid evidence, measured endpoints and historical replay checks pass. Capture is still disabled; no runtime upgrade.

QNT03_IMPACT: UNCHANGED CLOSED. Fresh dependence/lineage tests pass; research/dependence.py unchanged. Distinct strategy lineage does not establish statistical independence; no numeric factor adjustment or pseudo-replication introduced.

RISK03_IMPACT: PORT-04 and RISK-01 dependencies CLOSED; RISK-03 now eligible for its own exact evidence mapping, unselected/unstarted. Row unchanged EVIDENCE_TO_MAP; no automatic closure. Whole-account period losses/drawdown and pre-catastrophe preservation remain its independent contract.

SOURCE_INTEGRATION: YES pending. Control-only commit on separate branch; 387d130 is not integrated into canonical application source. No Kernel/Dashboard operation, provider/venue call, configuration activation or live action.

Limitations adjudication:

- B: WRLD-07 world-model capture disabled. Truthful unavailable optional evidence; existing Attention basket measurement and shared-lineage/exact-book evidence identify supported risks. No world pair capability established.
- A: Asset/cluster concentration policy not configured. No supported owner limit exists to consume; exact condition requires measurement/lineage for deterministic constraints, not invention of owner policy. Existing configured constraints stay enforced.
- A: Per-instrument exposure measured but not consumed as a new limit. Exact measurement is retained; per-symbol risk policy remains consumed. A new exposure ceiling has no owner policy authority.
- B: Historical held-position lineage may remain UNKNOWN. Unattributed held positions remain in the complete book and same-instrument exposure checks. No reconstructed provenance or independent vote is invented.
- A: No production runtime observation. Architecture/offline closure only; runtime unproven and source integration pending.

No C limitation found. Fresh exact evidence: {"fresh-targeted": {"passed": 283, "failed": 0, "by_file": {"tests.test_port04_relationship_sources": 11, "tests.test_port01_whole_book": 27, "tests.test_portfolio_common_factor": 17, "tests.test_portfolio_common_factor_shadow": 3, "tests.test_stage6_normal_sources": 7, "tests.test_stage6_closure": 46, "tests.test_wrld07_relationship_state": 54, "tests.test_qnt03_dependence_lineage": 10, "tests.test_dec02_comparison": 25, "tests.test_risk01_entry_binding": 19, "tests.test_risk02_exposure_feasibility": 64}}, "fresh-metrics": {"passed": 20, "failed": 0, "by_file": {"tests.test_runtime_portfolio_integration": 20}}, "fresh-independent": {"passed": 3, "failed": 0, "by_file": {"sol1-port04-canonical.docs.tracker.evidence.port04-canonical-sol1.independent_test": 3}}, "distinct_fresh_passes": 306, "reported_engineering_passes": 327, "reported_327_exact_command_and_artifact": "NOT_RETAINED; not independently certified or summed with fresh evidence", "full_suite_green_claim": false}. Only PORT-04 changes; other 178 rows, contracts, dependencies and readiness gates unchanged.
