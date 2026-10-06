# [SOL-2][RES-03] Governed source registry — CLOSED offline

RES-02 is CLOSED. SEC-01 remains DEFERRED / NOT_REQUIRED_FOR_CURRENT_RELEASE;
its dependency edge and future live-access closing conditions are preserved.
This closes source governance for the supported architecture/offline scope.
No credentials, live provider requests, services or trading activation were used.

## Evidence mapped before changes

| Requirement | Existing exact evidence | Result |
|---|---|---|
| Source/access class | `research_sources.py` internal journal stores/readers; `external_sources.py` academic factors, scholarly metadata, public HTTPS GET JSON | Implemented; registry pin and exact descriptor binding already tested |
| Credibility | Internal NOT_ASSESSED; external bibliographic index is not claim validation | Explicit uncertainty; no source is declared truth |
| Cost | Internal NOT_MEASURED with attribution reason; external recorded zero paid request cost and public/no-signup access with documentary provenance | No invented internal cost or measured current provider pricing |
| Historical depth | NOT_MEASURED, coverage not measured | Honest limitation, not full historical coverage |
| Rate limits | NOT_ASSESSED, dynamic provider limits; local adapter spacing of one second | Local bound is not a measured provider quota |
| Weaknesses | Internal selection/health limitations; Crossref incomplete metadata, missing abstracts, no full-text or validity claim | Recorded provenance and limitations |
| Scope | Registered strategy-decay question contract, `academic_factors` class, unsupported routes refused | Implemented by existing router; RES-02 proof retained |
| Paid access | Free-only `select`, zero-only `Bounds.max_cost_usd`, no paid approval issuer | Paid requests refused; no governed discovered-source ledger |
| Claims/credentials | RESEARCH_ONLY/INCONCLUSIVE bank; no ambient transport credentials; source equality/domain checks and unchanged strategy/decision/trade tables | Existing authority boundary retained |

The demonstrated gap was durable discovery and governed catalog adoption.
Registry metadata, search, extraction and RES-02 routing did not require redesign.

## Change

`source_governance.py` records exact bounded source descriptors in the existing
brain-event journal. Each descriptor includes all nine metadata facts,
source/access class, scope and discovery provenance. Explicit unknowns require
reasons; unknown or malformed cost never becomes free. Content hashes bind
discovery and decisions. Reopening and repeated calls preserve the same records;
changed source content gets a distinct identity. The read-only catalog keeps
discovery separate from adoption and rejects tampered evidence.

Catalog adoption is limited to supported strategy-decay research and the
academic-factors class. It grants RESEARCH_ONLY descriptive planning authority,
with trading, spending and acquisition authority explicitly false. A discovered
entry cannot extend the engineering-registered retrieval allowlist. The actual
external selector checks the same governance policy before the worker can run;
the existing source registry bytes, route identities and transport remain intact.

Paid access, including an allegedly free descriptor with positive cost, is
refused for owner approval. The decision records required capability/hypothesis,
insufficiency of existing sources, expected value and ongoing cost justification
from SDD 2.7/12.4. No paid approval issuer exists in this offline package.
Credential-dependent access is refused for future SEC-01 proof. External
approval/credentials/authority fields are rejected; credible-looking claims,
ambient fixture credentials and approval strings cannot grant authority.

## Validation and boundaries

210 tests passed across the new RES-03 suite and existing external router,
internal registry and evidence-source binding suites. 20 RES-02 generated-routing
and negative-memory regressions passed. New tests install socket tripwires;
existing transport cases use fixture children/sessions. Journal fixtures verify
unchanged strategies, decisions, trades, control state, questions and Research
Bank objects. Exact commands/results and source hashes are retained in
`tests.txt` and `proof.json`. `git diff --check` passes.

The local Claude execution probe returned `Not logged in`; authorized RES-03
work proceeded locally. Graphify was used for initial evidence navigation and
updated through code-only AST extraction, with no semantic/provider extraction.

RES-04 is dependency-eligible because RES-03 and LLM-02 are CLOSED at the current
offline scope; RES-04 is unselected and its own closing condition is unchanged.
Future live work still requires SEC-01 credentials/least-privilege and provider
access verification, measured coverage/quotas/cost where needed, explicit adapter
registration for new retrieval, and trusted owner approval plus cost/value
justification before paid adoption. None of those live capabilities is asserted
by catalog adoption or this offline closure.

Canonical reconciliation is retained in `closure.json` and `control-checks.json`:
the read-only Tracker/NEXT contract is AVAILABLE with RES-03 CLOSED, all 178 IDs
and 177 unrelated rows unchanged, original closure conditions/dependencies
preserved, bundle/source hashes verified, and duplicate YAML keys rejected.
SEC-01 and RES-02 rows are unchanged. RES-04 eligibility is recorded without
changing its row or selecting it.
