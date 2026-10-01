# LUFFY-VERSIONED-PAPER-EXECUTION-R1

Review correction: the original cost/identity probation claims required two
P1 fixes. LUFFY-VERSIONED-PAPER-PROBATION-TRUTH-FIX-R1 supersedes those claims;
see [truth-fix report](2026-10-01-versioned-paper-probation-truth-fix-r1.md).
Mechanical paper execution remains TESTED. Production economic probation is
INCOMPLETE_COST_EVIDENCE, not complete or proven.

Base: Capacity Evidence Integration committed `e60e045`. Worktree:
`/mnt/luffy-data/luffy/workspaces/versioned-paper-execution-r1`, branch
`luffy-versioned-paper-execution-r1`. Uncommitted, not deployed.

## Authority and isolation

Kernel retains the live F1 refusal and its false live-entry result. Before
that refusal it can dispatch an exact installed version to `engine.paper`.
That runner has no venue/executor, reconciliation or control capability.
Executor's existing F1 and non-live-mode fences are unchanged. SHADOW,
APPROVAL_REQUIRED and owner-approved versions can simulate paper entries;
none acquire activation. Capacity UNAVAILABLE continues to block first live.

The existing frozen compiler revalidates the decision's exact spec hash,
signal bar and action. Frozen universe, markets and regimes are respected.
Frozen stop/target distance helpers are reused, on CLOSED spec-frame ATR.
The actual RiskManager uses unchanged configured limits, control permission,
paper exposure and a separate durable paper baseline/account seeded once
from the supplied reference equity. It cannot mutate real Risk/control state.
Meta sizing shrinks only. Duplicate signal retries return the original paper
trade id; exit-bar reentry is refused.

`versioned_paper_trades` stores OPEN and CLOSED positions; it never writes
`trades`, trade legs/fills/bookings, venue/account snapshots or real equity.
Exact probation reads this isolated ledger alongside compatible historical
paper records. Each runtime trade has strategy/version/spec hash, exact
install and validation receipt, decision/cycle identity and immutable Trade
Provenance entry identity. IDs are local paper identities, never venue IDs.

## Fill and exit evidence limits

Entry fill: recorded `decision_snapshot_price`, with reference price,
quantity and timestamp. Exit fill: recorded frozen stop/target level or
closed spec-bar reference close. This is deterministic paper bar simulation,
not a claim of achievable venue fills or execution-cost realism. No paper
commission/slippage/funding policy was found; all are recorded UNAVAILABLE,
commission is NULL, and P&L is GROSS_BEFORE_UNAVAILABLE_COSTS. No fee or
slippage number is fabricated. Existing probation thresholds are unchanged.

Paper exits process each unseen CLOSED spec bar in order, including after
restart: stop first on both-hit bars, frozen target, frozen ATR trail and
unconditional max_bars close. They do not inject configured TP1/flip overlays.
Fixed/trailing geometry has direct comparisons with vector_backtest._trade
under explicit zero-cost comparison inputs; these tests do not claim cost
parity. Signal exits are refused at exact install and at probation scoring
with `unsupported_paper_exit:signal_exit:runtime_parity_unavailable`.
Missing bars/ATR fail closed rather than skipping an unobserved exit.

The live ExitEngine still differs in TP1 partial, flip, time-stop and
signal_exit behavior. This package does not establish full runtime/live /
backtest parity or activation. The next selected blocker is B: remaining
runtime/backtest exit parity for admitted versions, before first live.
Liquidity/impact capacity validation also remains outstanding, but is not
the selected next package. Stage 5 stays TESTED, not PROVEN or ACTIVE.

## Acceptance

`test_end_to_end_15_actual_signal_entries_and_closes` registers a referee-
passed deterministic fixture through the production research ledger, freezes
an immutable version/validation receipt and exact paper install, evaluates
real frozen compiler signals over deterministic frames, dispatches entries
through Kernel._try_enter with the real Executor behind the unchanged fence,
and closes 15 paper positions through Kernel's paper runtime. No trade rows
are manually inserted. The corrected test runs two paths: unavailable costs produce
INCOMPLETE_COST_EVIDENCE without an approval request; explicitly registered
pytest-only cost evidence produces SATISFIED, a request and test owner
approval. Capacity UNAVAILABLE keeps first-live eligibility false in both. Venue submission count remains zero throughout.

Other direct tests cover restart/reopen identity, duplicate entry/exit replay,
missing-bar replay, Risk/control refusal, wrong decision hashes, short signals,
stop/target/both-hit/time/trail behavior, and actual reconciliation seeing no
paper exposure. Paper entry/close snapshots leave real trading/accounting and
control tables unchanged. F1-F4 and historical live-trade exclusion are rerun.

The following validation counts are the original pre-fix receipts, not
validation of the corrected economic/identity gates. Current validation is
recorded in the truth-fix report and STATE.yaml. No full suite, authenticated
venue write, process restart, research activation, frontend edit, commit or
deployment. The local Claude execution check failed because it is not logged
in; work was completed directly within the user's authorized package scope.

Final checks: 290 focused tests passed. Following the exit-bar retry guard and
stronger capacity/refusal assertions, the affected paper/authority/factory
suites were rerun: 92 passed. YAML parses and git diff --check pass.
Graphify AST-only update completed (22,556 nodes); it skipped 98 unclassified
files and reported changed community labels. No semantic extraction or paid
API was run; graph outputs/cache changes are expected review artifacts.
