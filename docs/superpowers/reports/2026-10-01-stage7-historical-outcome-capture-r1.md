# Stage 7 historical outcome capture R1

Package: `LUFFY-STAGE7-HISTORICAL-OUTCOME-CAPTURE-R1`.
Base: `9d31fbd61ed4c4b8c697248ac32dfd0451b537e4`, the committed Stage7 foundation.
Branch: `luffy-stage7-historical-outcome-capture-r1` in an isolated worktree.

The prospective capture foundation passes its focused acceptance tests. Historical
completeness has not been upgraded. The normal trading producer still does not
carry an exact WorldModel/Opportunity Context cut. Recording its absence is
intentional; a successful registration is never a completeness certificate.

No production process, configuration, orders, StrategySpec, Risk limits, lifecycle
state or learning authority was changed. No commit, push, deployment or restart.
Only the inventory read production; URI `mode=ro`, query-only SQLite snapshots,
no Journal construction, API requests, reconciliation acquisition or learning apply.

## Capture and replay

`trader/learning/capture.py` owns append-only registration, content-addressed
snapshots, action receipts, outcome receipts and deterministic manifests. Identical
inserts reuse the retained bytes; conflicts, UPDATE, DELETE and replacement refuse.
Immutable producer references resolve exact table keys and original content hashes.
Unprotected/mutable sources are snapshotted. No latest-version resolver exists.

Dataframes are deduplicated per content chunk and exported with exact binary float
values, nanosecond timestamps, axes and column dtypes. `restore_frame` verifies a
round trip without a feed, registry or current configuration. JSON rounding is
not used for source frames or selected forward target prices. Snapshots retain
observed values rather than asserting that the current bar was final.

The Kernel detaches source inputs before deciding. Journal decision insertion
captures the exact original cycle, decision, reasons, data, portfolio and safe
decision configuration. Exact StrategyVersion binding uses the actual selected
signal's spec hash and refuses ambiguous versions. Exit semantics require their
actual binding. WorldModel, Opportunity Context, proposal, intent and derivative
identity are retained only when carried by the producer; they are not inferred.
Actual Risk outputs and the arguments used for the check are captured separately.
Capture failures roll back their own savepoint and record a diagnostic while
preserving the original trading/research transaction.

Registration freezes original knowledge. Later events may add action, actual Risk
decision, execution/trade identifiers and measurement evidence; they cannot replace
the original context, configuration, data, version or lineage. Each manifest lists
every dependency as AVAILABLE, UNAVAILABLE or NOT_APPLICABLE. Requirements differ
between trading, research and incident profiles and between outcome kinds.

Required missing/tampered data, source clocks, world/context binding, exact strategy
and exit binding, Risk configuration, allocation/intent integrity, accounting,
funding, research links or measurement verification prevent REPLAY_COMPLETE.
Legacy PARTIAL means retained descriptive fragments exist; it is not authoritative.
UNRESOLVED remains INCOMPLETE; UNASSESSABLE is retained separately.

The existing canonical foundation accepts the verified manifest through its replay
API and re-verifies it when constructing LearningEvidence. Incomplete manifests
produce NON_AUTHORITATIVE evidence and INCOMPLETE_REPLAY proposals. Complete
synthetic chains produce VERIFIED_REPLAY evidence and deterministic proposals;
an unregistered confidence rule still produces UNREGISTERED_RULE. No authority
application was added or invoked. The existing recent-decay rule is unchanged.

## Event coverage and truthful limits

* Ordinary Journal decisions/actions: executed, rejected, Risk-blocked, HOLD/cash
  and skip reasons. Missing-snapshot scan skips create UNASSESSABLE missed records;
  they do not invent an opportunity, decision context or future return.
* Booking entries and closures: exact original receipt and trade state, action,
  trade/order identifiers; open bookings are UNRESOLVED and closures remain
  UNASSESSABLE without whole-trade accounting. Neither journal gross P&L nor a
  good/bad fill is sufficient to establish realized money or cause.
* Existing forward resolver: the original scheduled prediction declaration,
  selected post-horizon bars, clock and arithmetic. Only a fully closed target
  verifies. Rejected/missed/cash/Risk-blocked returns remain COUNTERFACTUAL /
  SIMULATED / UNREALIZED. An executed decision's hypothetical price path is also
  counterfactual; it cannot replace its actual monetary outcome.
* Exact reconciled trade receipts: a tested adapter retains the existing
  whole-trade producer's raw histories, order/fill identities, owned funding,
  costs, execution reference and position basis. Money is REALIZED only after
  the existing typed accounting replay verifies it. The producer remains
  offline; no automatic venue acquisition was introduced. Import retries with
  a new transient import clock reuse the first retained receipt for that source.
* Paper cost finalization: immutable cost reference, original source bundle,
  public funding receipt where supplied, simulated reference and immutable paper
  exposure where present. Paper fills are never realized venue P&L. Absent
  calibrated costs or funding-event position/notional authority remain unavailable,
  rather than zero. Existing finalized unknown receipts are not retroactively
  upgraded using later/current rates.
* Research Bank: exact Q/P/E/result/run/bank chains for strategy decay, unreadable
  health and the registered investigation volume-anomaly family. Filing clocks
  establish knowledge at filing, not a fabricated original prediction timestamp.
  Results retain existing falsifiers or their absence; no claim equivalence,
  predictive edge, causal effect or semantic suppression was introduced.
* Forecast registrations/results and explicit execution/data incident events:
  exact existing producer evidence is retained. Forecasts without the formal
  plan/result/Bank chain remain incomplete; incidents without decision/causal
  lineage remain unassessable.

Funding validation uses retained public interval receipts or existing complete
owned venue accounting, with exact environment and response/fill references.
No new funding schedule, rate, notional or zero-cost assumption was introduced.
Execution-quality evidence does not grant predictive confidence or cost calibration.

## Real inventory

At `1790883370932`, the same bounded stratification as the foundation selected
564 decision-outcome chains from 702,201 recorded decisions. This is an inventory
sample, not a claim that all history was enumerated.

| Classification | Count |
|---|---:|
| REPLAY_COMPLETE | 0 |
| REPLAY_PARTIAL | 251 |
| REPLAY_INCOMPLETE | 259 |
| UNASSESSABLE | 54 |
| Total | 564 |

Production contained zero prospective capture receipts because this branch is not
deployed. Applicable applications: zero. Production mutations: zero. These counts
refine the old all-incomplete replay result; none of the 251 partial chains became
complete or authoritative. Aggregate inventory evidence contains no runtime rows
or shadow data. The temporary detailed legacy inventory was deleted after counting.

Next-event capture readiness is YES for the reviewed plumbing. This means relevant
existing producer events can be retained with explicit missing sources when this
code is eventually installed; it does not certify the next real event COMPLETE or
authorize deployment. Actual runtime readiness is not deployment evidence.

## Validation and remaining implementation

`tests.txt`: 248 focused tests passed across the new capture integrations,
foundation, booking/provenance, outcome/backfill, paper costs, whole-trade accounting,
research family and authority guards. No full suite. The old provenance expectation
now includes its already-existing `observed_at` field. Two CLI tests used an
untracked temporary venv link in the isolated worktree; the link was removed after
verification. Supplemental entry/Risk boundary results are recorded separately.

`git diff --check` and YAML parsing are required final checks. Graphify was updated
with its AST-only CLI, no semantic extraction or API spend. Its generated output
is excluded from this package. The CLAUDE instruction's minimal CLI availability
probe returned `Not logged in`; no external review was claimed or fabricated.

STATE records PROSPECTIVE_OUTCOME_CAPTURE and REPLAY_MANIFEST as TESTED, and
DIGITAL_TWIN_REPLAY as TESTED_PROSPECTIVE. Stage7 remains incomplete.

The single next package is
`LUFFY-STAGE7-DECISION-REPLAY-SOURCE-INTEGRATION-R1`: carry the actual original
WorldModel/Opportunity Context cut into normal trading decision registrations.
Whole-trade accounting automation and calibrated derivative/cost/funding authorities
remain absent where explicitly recorded. Legacy direct statistical writers now fail closed at the authority boundary in
this recovery worktree. Agent calibration/refit, online weights (including direct
save), meta-model refit/save, static validation weight publication, legacy genome
statistical lifecycle transitions, and the Kernel's direct recent-decay retirement
path cannot apply raw statistical results. These consumers lack a verified replay
and registered proposal adapter; even an asserted complete receipt does not grant
an unregistered confidence/weight rule. Descriptive health/statistics and existing
cached files remain unchanged. No new rule, learning redesign or source integration
was introduced. The canonical registered `existing-recent-decay.v1` path already
refuses NON_AUTHORITATIVE evidence before evaluator invocation and re-verifies the
chain before isolated authority application.

Preserved Stage6 boundaries: paper cost protocol TESTED; real cost calibration
INSUFFICIENT_EVIDENCE; economic probation INCOMPLETE; capacity UNAVAILABLE;
first-live BLOCKED. All parked operational/research boundaries remain parked.


## Recovery final verification

Recovered into `/mnt/luffy-data/luffy/workspaces/stage7-outcome-capture-recovery-r1`
on `luffy-stage7-outcome-capture-recovery-r1`. Original branch reflog explicitly
records creation from `luffy-stage7-learning-closed-loop-foundation-r1` at
`9d31fbd61ed4c4b8c697248ac32dfd0451b537e4`; original HEAD and package report agree.
All 25 modified/untracked package files were copied and byte-verified; their hashes
are retained in `recovery-transfer.json`. Generated graph changes were excluded,
as were runtime/shadow, frontend/M4, and unrelated files. The original worktree
was preserved. This recovery has no commit or deployment. Local Claude execution
remains unavailable (`Not logged in`); verification and bounded authority fixes
were executed in this session.

The recovered STATE/NEXT contained provisional PASS/completed-prerequisite claims.
They were returned to verification state before the final checks. Final claims
below refer only to this isolated recovery package, not Stage7 completion.

Initial verification found three stale Research Bank nonmutation assertions:
they incorrectly treated new append-only capture receipts as unrelated writes.
The corrected assertions still compare every unrelated table and verify every
previously retained capture row is unchanged. A runtime-isolation assertion now
allows only the refusal helper from foundation, preserving the ban on production
proposal/application imports. Legacy lifecycle regressions now require refusal
and preserved state/clocks.

The live linkage dependency is confirmed by the current Kernel call to
`runtime_inputs`: it carries source snapshots, order-book/positioning observations
and portfolio/config, but no exact WorldModel/OpportunityContext objects.
`runtime_inputs` accepts producer-carried `snap.learning_sources`, and no runtime
producer sets that field. Missing dependencies remain UNAVAILABLE. No integration
of those sources was attempted.

Final recovery verdict: PASS. The final focused run passed **584 tests** across
prospective capture, legacy classification, replay manifests, trade/rejected/missed/
research outcomes, derivative/funding references, LearningEvidence, registered rule
refusal, legacy authority refusal, restart/replay/idempotency, and adjacent booking,
accounting, lifecycle and Risk fences. `recovery-final-tests.txt` is authoritative;
earlier attempt logs are superseded. Final `git diff --check` and STATE/NEXT YAML
validation passed. AST-only Graphify update passed with no semantic extraction;
generated graph artifacts were removed from this isolated package per scope.

Fresh read-only bounded inventory at 1790887555321 selected 564 chains
from 703719 recorded decisions: total 564, complete 0,
partial 251, incomplete 259, unassessable 54, prospective production captures 0,
applicable updates 0, production mutations 0. This remains a bounded stratified
sample, not an enumeration of all recorded history. Aggregate evidence is
`recovery-inventory-summary.json`; no detailed runtime/shadow rows are retained.

NEXT_EVENT_CAPTURE_READY is YES for the verified future event hooks in this code,
not for production deployment or next-real-event replay completeness. STATE/NEXT
now reflect final verification and preserve the exact live source linkage gap as
`LUFFY-STAGE7-DECISION-REPLAY-SOURCE-INTEGRATION-R1`. Stage7 remains incomplete;
no historical receipt was upgraded, no runtime authority applied, no commit/deploy.
