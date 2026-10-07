# STR-03 canonical CLOSED

The exact closing condition is satisfied at `2b119a3`: Research→validation→probation→approval→Governor path loads exact approved artifact; approval refuses mismatch/stale context.. Original conditions, source references and dependency edges are preserved.

**Exact binding.** Request binds strategy_id, version_id, spec hash, STR-01 lineage hash, research/quantitative hashes, exact install and validation/probation receipts. Request ID is re-derived from these bindings including FIRST_LIVE_EXACT_VERSION scope. Owner decision binds request identity, exact version/evidence, owner actor/decision identity and configuration hash.

**Narrow scope.** Scope explicitly excludes by-name, latest and descendants; sibling/derived versions do not inherit receipts or approval. ACTIVE, grandfather receipts and unversioned/legacy authority cannot replace exact first-live approval.

**Integrity.** Factory writer triggers refuse ordinary raw-SQL request/decision inserts. Requests are re-derived, decisions re-hashed and rebound; APPROVED_FIRST_LIVE must name the exact owner decision. Changed/copy-forged bindings refuse even in the test that deliberately enables the internal flag.

**Staleness.** Material spec/version, research status/revision, quantitative, installed logic, probation trades, risk/config/approval limits or required data changes refuse use. Frozen rows cannot be updated/deleted; old owner artifacts remain inspectable with STALE validity/reasons. Approval scope alteration fails exact request verification.

**Predictive research.** Eligibility re-authenticates predictive lineage against the Research Bank; a superseding result refuses, and absent research_bank_path gives research_bank_not_asserted. Governor forwards this required call-boundary assertion.

**Governor.** Governor loads/authenticates the exact approved version, install and evidence; event names exact spec hash/version and owner decision. Unapproved sibling, changed probation/install/research/config refuse before any Governor event is written. Successful offline activation still preserves the live execution fence.

**Authority.** Owner approval alone changes no strategy live state, control state, trade count or Governor allocation. It grants no capital, orders, Risk bypass or Kernel start. Capacity, Risk and live/deployment/security gates remain downstream/deferred.

**Replay.** Same stored chain re-derives request identity, survives byte-copy restore/restart and identical retry; conflicting decision refuses. Derived versions have new identity and no inherited authority.

**Limitations.** research_bank_path is asserted at the eligibility/governor call boundary rather than stored as a permanent path authority. Fresh fixture rebuild IDs vary because a research look contains wall-clock data; replay is proven against the same stored chain.

SQLite writer guard can theoretically be bypassed by code deliberately enabling the internal write flag (journal._local.approval_write); it is an application writer boundary, not isolation from malicious trusted Python or direct database administration. This is non-blocking for the exact STR-03 architecture/offline condition: ordinary SQL is guarded and artifact identities are authenticated on use. Deliberate privileged code could fabricate self-consistent records. Hostile trusted-code isolation or cryptographic owner attestation is not proven; downstream security/live/deployment gates remain deferred.

**Validation.** 43 dedicated tests freshly passed through luffy-pytest. Retained related log: 642 passed / 1 failed in 1385.72s across 24 listed suites, including dedicated tests; counts overlap and are not summed. The failure is `tests/test_stage8_owner_os.py::test_portfolio_exact_cut_candidates_rejection_intent_risk` with JSONDecodeError, classified as unrelated baseline by the supplied engineering validation. Its before-change baseline log is not independently retained here. No full-suite green claim. No live/provider calls; Kernel and Dashboard remained stopped, watchdog.off present.

STR-04 dependency is satisfied and STR-04 is now eligible for its own proof. Its row is unchanged, unselected and not started. DEC-03 remains gated by STR-04. Approval and reconciliation grant no trading, capital, orders, Risk bypass or Kernel start. All 177 other rows and runtime state are preserved.

Status counts: CLOSED 62; EVIDENCE_TO_MAP 77; DEFERRED 7; AWAITING_EVIDENCE 9; AWAITING_OWNER 14; BLOCKED 5; OPEN 4 (178 total). Control-plane commit: enclosing Git commit containing this report. See [closure](closure.json), [fresh dedicated results](dedicated.xml), [related retained log](related-engineering.txt) and [control checks](control-checks.json).

Independent [offline proof](offline-proof.json) confirms request and decision UPDATE/DELETE refusals, actual persisted row values unchanged across restart/retry, re-derived request identity and identical decision identity. Process observations before/after found no Kernel or Dashboard process; watchdog.off remained present.
